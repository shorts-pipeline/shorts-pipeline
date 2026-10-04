#!/usr/bin/env python3
"""
Generate a map intro clip for Lewis & Clark videos: location pin on US map + title overlay.
Uses folium (map), geopy (geocoding), Playwright (screenshot), ffmpeg (video).
Output: movie-images/{date_id}/00_intro.mp4
Also prepends intro to durations.json (assembly builds full audio as intro_date + final.mp3).
"""

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import json
import os
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import ImageDraw, ImageFont

import ffmpeg
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from pipeline.tts_text_normalize import normalize_text_for_tts
from pipeline_logging import log_file_created

TEI_NS = "http://www.tei-c.org/ns/1.0"

INTRO_DURATION_SEC = 3.0
MAP_SIZE = (1280, 720)  # landscape 16:9
MAP_SIZE_SHORTS = (720, 1280)  # vertical 9:16 for YouTube Shorts
# Frame continental US, no Mexico: use explicit zoom + center (fit_bounds is unreliable in Playwright screenshot)
MAP_ZOOM = 5  # zoom 5 with 1280x720 gives ~56° lon × ~25° lat at mid-US
MAP_CENTER = [38.5, -98.5]  # center a bit west to show more of California
# URL for US state boundaries overlay (outline only)
US_STATES_GEOJSON_URL = "https://raw.githubusercontent.com/python-visualization/folium/main/examples/data/us-states.json"

# Location data directory: waypoints + cached map images (by lat/lon, no title)
LOCATION_DATA_DIR = Path(__file__).resolve().parent / "location_data"
MAP_CACHE_DIR = LOCATION_DATA_DIR / "cache"


def geo_from_journal_xml(date_id: str, journal_dir: Path) -> tuple[float, float, str] | None:
    """Extract (lat, lon, location_label) from journal TEI XML geoDecl if present.
    Returns None if file missing, no geoDecl, or parse error.
    Location label comes from note or placeName; fallback to 'Journal location'.
    """
    date_str = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"
    xml_path = journal_dir / f"{date_str}.xml"
    if not xml_path.exists():
        return None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # encodingDesc/geoDecl/geo (and optionally note or placeName)
        geo_decl = root.find(f".//{{{TEI_NS}}}encodingDesc/{{{TEI_NS}}}geoDecl")
        if geo_decl is None:
            return None
        geo_el = geo_decl.find(f"{{{TEI_NS}}}geo")
        if geo_el is None or not (geo_el.text and geo_el.text.strip()):
            return None
        parts = geo_el.text.strip().split()
        if len(parts) < 2:
            return None
        lat = float(parts[0])
        lon = float(parts[1])
        label = "Journal location"
        note_el = geo_decl.find(f"{{{TEI_NS}}}note")
        if note_el is not None and note_el.text and note_el.text.strip():
            label = note_el.text.strip()
        else:
            place_el = geo_decl.find(f"{{{TEI_NS}}}placeName")
            if place_el is not None and place_el.text and place_el.text.strip():
                label = place_el.text.strip()
        return (lat, lon, label)
    except (ET.ParseError, ValueError, OSError):
        return None


def best_geo_for_date(
    date_id: str,
    journal_dir: Path,
    max_days_back: int = 365,
) -> tuple[float, float, str, str] | None:
    """Return (lat, lon, location_label, source_date_id) for this date or the last earlier date with geo.
    Tries the journal entry for date_id, then previous days, so we show last known position when today has no geo.
    Returns None if no journal geo found in the window.
    """
    year, month, day = int(date_id[:4]), int(date_id[4:6]), int(date_id[6:8])
    start = datetime(year, month, day)
    for i in range(max_days_back):
        d = start - timedelta(days=i)
        try_id = d.strftime("%Y%m%d")
        result = geo_from_journal_xml(try_id, journal_dir)
        if result:
            lat, lon, label = result
            return (lat, lon, label, try_id)
    return None


def _load_location_dates(config_path: Path) -> list[dict]:
    text = config_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"location-dates.json is empty: {config_path}")
    data = json.loads(text)
    return sorted(data, key=lambda x: x["date"])


def find_location_for_date(date_id: str, location_dates: list[dict]) -> tuple[str, str] | None:
    """Return (location, date_str) for the nearest waypoint on or before this journal date."""
    # date_id is YYYYMMDD
    year = int(date_id[:4])
    month = int(date_id[4:6])
    day = int(date_id[6:8])
    journal_date_str = f"{year}-{month:02d}-{day:02d}"

    best = None
    for entry in reversed(location_dates):
        if entry["date"] <= journal_date_str:
            best = (entry["location"], entry["date"])
            break
    return best


def geocode(location: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a location string. Uses Nominatim (rate-limited)."""
    geolocator = Nominatim(user_agent="lewisclark_youtube_map_intro")
    rate_limited_geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)
    try:
        geo = rate_limited_geocode(location, timeout=10)
        if geo:
            return (geo.latitude, geo.longitude)
    except Exception:
        pass
    return None


def waypoints_up_to_date(date_id: str, location_dates_path: Path) -> list[tuple[str, str]]:
    """Return list of (date_str, location) from location-dates.json for all entries on or before date_id."""
    journal_date_str = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"
    location_dates = _load_location_dates(location_dates_path)
    out = []
    for entry in location_dates:
        if entry.get("date", "") <= journal_date_str:
            loc = entry.get("location", "").strip()
            if loc:
                out.append((entry["date"], loc))
    return out


def journal_points_up_to_date(
    date_id: str,
    journal_dir: Path,
) -> list[tuple[float, float]]:
    """Collect (lat, lon) points from journal XML geoDecls for all entries on or before date_id.

    This prefers the per-day TEI coordinates so the mid-episode map polyline follows the actual
    expedition path, instead of a straight segment between coarse waypoints.
    """
    journal_date_str = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"
    if not journal_dir.exists():
        return []
    points: list[tuple[float, float]] = []
    # Files are named YYYY-MM-DD.xml; sort to walk chronologically.
    for xml_path in sorted(journal_dir.glob("*.xml")):
        stem = xml_path.stem  # YYYY-MM-DD
        # Skip anything after the current date.
        if stem > journal_date_str:
            break
        # Derive date_id for geo_from_journal_xml.
        try:
            y, m, d = stem.split("-")
            this_date_id = f"{y}{m}{d}"
        except ValueError:
            continue
        geo = geo_from_journal_xml(this_date_id, journal_dir)
        if not geo:
            continue
        lat, lon, _label = geo
        points.append((lat, lon))
    return points


def polyline_points_for_date(
    date_id: str,
    location_dates_path: Path,
    geocode_cache_path: Path | None = None,
) -> list[tuple[float, float]]:
    """Build list of (lat, lon) for a running map: points from expedition start up to date_id.

    Preference order:
    1) Per-day journal TEI geoDecl coordinates (one point per dated journal entry).
    2) Fallback: waypoints from location-dates.json (geocoded, cached).
    """
    # 1) Prefer exact coordinates embedded in the TEI journal XML.
    journal_dir = Path(__file__).resolve().parent / "journal-entries"
    journal_points = journal_points_up_to_date(date_id, journal_dir)
    if len(journal_points) >= 2:
        return journal_points

    # 2) Fallback to coarse waypoints file if we do not have enough journal coords.
    waypoints = waypoints_up_to_date(date_id, location_dates_path)
    if not waypoints:
        return []
    cache_path = geocode_cache_path or (LOCATION_DATA_DIR / "cache" / "geocode.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}
    else:
        cache = {}
    points = []
    for _date_str, location in waypoints:
        if location in cache:
            lat, lon = cache[location]
            points.append((float(lat), float(lon)))
        else:
            coords = geocode(location)
            if coords:
                lat, lon = coords
                cache[location] = [lat, lon]
                points.append((lat, lon))
    if cache_path and cache:
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return points


def _map_cache_path(lat: float, lon: float, width: int, height: int) -> Path:
    """Path for cached map image (no title) keyed by location and size."""
    MAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"map_{lat:.2f}_{lon:.2f}_{width}x{height}.png"
    return MAP_CACHE_DIR / name


def render_map_png(
    lat: float, lon: float, output_path: Path, map_size: tuple[int, int] = MAP_SIZE
) -> None:
    """Create folium map and screenshot to PNG via Playwright.
    Uses Esri World Physical (blue water, terrain, rivers); framed on continental US.
    """
    import folium
    from branca.element import Figure
    from playwright.sync_api import sync_playwright

    w, h = map_size
    fig = Figure(width=w, height=h)
    # Esri World Physical: blue oceans, mountains, rivers; no API key
    esri_physical = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}"
    m = folium.Map(
        location=MAP_CENTER,
        zoom_start=MAP_ZOOM,
        tiles=esri_physical,
        attr="Esri",
        width="100%",
        height="100%",
        max_bounds=[[26, -125], [50, -66]],  # restrict pan to US (no Mexico)
    )
    # State outlines overlay (outline only, no fill)
    try:
        with urllib.request.urlopen(US_STATES_GEOJSON_URL, timeout=15) as resp:
            states_data = json.loads(resp.read().decode())
        folium.GeoJson(
            states_data,
            style_function=lambda _: {
                "fillOpacity": 0,
                "color": "#aaa",
                "weight": 1.2,
            },
            name="State boundaries",
        ).add_to(m)
    except (OSError, json.JSONDecodeError):
        pass  # skip state outlines if fetch fails
    folium.Marker([lat, lon], popup="Expedition location").add_to(m)
    fig.add_child(m)

    html_path = output_path.with_suffix(".html")
    fig.save(str(html_path))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(f"file://{html_path.resolve()}")
        page.wait_for_timeout(2000)  # Let tiles load
        page.locator(".folium-map").screenshot(path=str(output_path))
        browser.close()

    html_path.unlink(missing_ok=True)


def render_map_png_polyline(
    points: list[tuple[float, float]],
    output_path: Path,
    map_size: tuple[int, int] = MAP_SIZE,
    pin_at_end: bool = True,
) -> None:
    """Create folium map with polyline (trail) and optional pin at last point; screenshot to PNG via Playwright."""
    if not points:
        raise ValueError("render_map_png_polyline requires at least one point")
    import folium
    from branca.element import Figure
    from playwright.sync_api import sync_playwright

    w, h = map_size
    fig = Figure(width=w, height=h)
    esri_physical = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}"
    m = folium.Map(
        location=MAP_CENTER,
        zoom_start=MAP_ZOOM,
        tiles=esri_physical,
        attr="Esri",
        width="100%",
        height="100%",
        max_bounds=[[26, -125], [50, -66]],
    )
    try:
        with urllib.request.urlopen(US_STATES_GEOJSON_URL, timeout=15) as resp:
            states_data = json.loads(resp.read().decode())
        folium.GeoJson(
            states_data,
            style_function=lambda _: {"fillOpacity": 0, "color": "#aaa", "weight": 1.2},
            name="State boundaries",
        ).add_to(m)
    except (OSError, json.JSONDecodeError):
        pass
    folium.PolyLine(points, color="#8B4513", weight=3, opacity=0.9).add_to(m)
    if pin_at_end:
        folium.Marker(points[-1], popup="Current location").add_to(m)
    fig.add_child(m)

    html_path = output_path.with_suffix(".html")
    fig.save(str(html_path))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(f"file://{html_path.resolve()}")
        page.wait_for_timeout(2000)
        page.locator(".folium-map").screenshot(path=str(output_path))
        browser.close()

    html_path.unlink(missing_ok=True)


def generate_mid_episode_map(
    date_id: str,
    visual_style: str,
    duration_seconds: float,
    output_dir: Path,
    location_dates_path: Path | None = None,
    map_size: tuple[int, int] = MAP_SIZE,
) -> Path | None:
    """Generate running map (polyline) asset for mid-episode insertion.
    visual_style: 'parchment_overlay' -> PNG for overlay on segment (no standalone clip).
    Returns path to created file or None if no waypoints."""
    loc_path = location_dates_path or (LOCATION_DATA_DIR / "location-dates.json")
    if not loc_path.exists():
        return None
    points = polyline_points_for_date(date_id, loc_path)
    if not points:
        return None
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if visual_style == "parchment_overlay":
        out_path = output_dir / "mid_map_overlay.png"
        render_map_png_polyline(points, out_path, map_size=map_size, pin_at_end=True)
        log_file_created(out_path, out_path.stat().st_size)
        return out_path
    return None


def _letterbox_image(src_path: Path, dest_path: Path, target_w: int, target_h: int) -> None:
    """Scale image to fit inside target_w x target_h, center with black padding."""
    from PIL import Image

    img = Image.open(src_path).convert("RGB")
    sw, sh = img.size
    # Scale to fit inside target (preserve aspect ratio)
    scale = min(target_w / sw, target_h / sh)
    new_w = int(round(sw * scale))
    new_h = int(round(sh * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    out.paste(img, (x, y))
    out.save(dest_path, "PNG")


def _wrap_line(
    draw: "ImageDraw.Draw", text: str, font: "ImageFont.FreeTypeFont", max_width: int
) -> list[str]:
    """Word-wrap a single line to max_width; returns list of wrapped lines."""
    words = text.split()
    if not words:
        return []
    lines = []
    current = []
    current_width = 0
    space_width = (
        draw.textbbox((0, 0), " ", font=font)[2] - draw.textbbox((0, 0), " ", font=font)[0]
    )
    for word in words:
        w = draw.textbbox((0, 0), word, font=font)[2] - draw.textbbox((0, 0), word, font=font)[0]
        if current and current_width + space_width + w > max_width:
            lines.append(" ".join(current))
            current = [word]
            current_width = w
        else:
            current.append(word)
            current_width = current_width + (space_width if current else 0) + w
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_title_on_image(
    image_path: Path,
    title_lines: list[str],
    output_path: Path,
    max_width_frac: float = 0.85,
) -> None:
    """Draw title lines on image with PIL; word-wraps long lines. Dark text with light outline for visibility."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    max_width = int(img.width * max_width_frac)
    wrapped = []
    for line in title_lines:
        wrapped.extend(_wrap_line(draw, line, font, max_width))
    if not wrapped:
        img.convert("RGB").save(output_path, "PNG")
        return
    line_height = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
    total_h = len(wrapped) * line_height
    y = img.height - total_h - 80
    # Dark fill for visibility; light outline so it reads on any map background
    fill_dark = (25, 25, 25)
    outline_light = (255, 255, 255)
    outline_offset = 2
    for line in wrapped:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (img.width - tw) // 2
        for dx, dy in [
            (-outline_offset, -outline_offset),
            (outline_offset, -outline_offset),
            (-outline_offset, outline_offset),
            (outline_offset, outline_offset),
            (0, -outline_offset),
            (0, outline_offset),
            (-outline_offset, 0),
            (outline_offset, 0),
        ]:
            draw.text((x + dx, y + dy), line, font=font, fill=outline_light)
        draw.text((x, y), line, font=font, fill=fill_dark)
        y += line_height
    img.convert("RGB").save(output_path, "PNG")


def image_to_video(
    image_path: Path,
    output_path: Path,
    duration_sec: float,
    output_size: tuple[int, int] | None = None,
) -> None:
    """Create video from static image (no title). Used for minimal_pin mid-episode map."""
    video_input = image_path
    to_unlink: list[Path] = []
    if output_size:
        tw, th = output_size
        letterboxed = image_path.parent / f"{image_path.stem}_letterboxed_{tw}x{th}.png"
        _letterbox_image(image_path, letterboxed, tw, th)
        video_input = letterboxed
        to_unlink.append(letterboxed)
    (
        ffmpeg.input(str(video_input), loop=1, t=duration_sec, framerate=24)
        .output(str(output_path), vcodec="libx264", pix_fmt="yuv420p", r=24)
        .overwrite_output()
        .run(quiet=True)
    )
    for p in to_unlink:
        p.unlink(missing_ok=True)


def image_to_video_with_title(
    image_path: Path,
    output_path: Path,
    title_lines: list[str],
    duration_sec: float = INTRO_DURATION_SEC,
    output_size: tuple[int, int] | None = None,
) -> None:
    """Create video from static image. Title lines drawn with PIL (word-wrapped).
    If output_size is set (e.g. (720, 1280) for Shorts), letterbox the image to that size with black bars."""
    with_title = image_path.parent / f"{image_path.stem}_titled.png"
    _draw_title_on_image(image_path, title_lines, with_title)
    video_input = with_title
    to_unlink: list[Path] = [with_title]
    if output_size:
        tw, th = output_size
        letterboxed = image_path.parent / f"{image_path.stem}_letterboxed_{tw}x{th}.png"
        _letterbox_image(with_title, letterboxed, tw, th)
        video_input = letterboxed
        to_unlink.append(letterboxed)
    (
        ffmpeg.input(str(video_input), loop=1, t=duration_sec, framerate=24)
        .output(str(output_path), vcodec="libx264", pix_fmt="yuv420p", r=24)
        .overwrite_output()
        .run(quiet=True)
    )
    for p in to_unlink:
        p.unlink(missing_ok=True)


def _synthesize_date_tts(
    date_spoken: str, out_path: Path, tts: str, use_female_voice: bool = False
) -> float:
    """Synthesize date phrase to MP3; return duration in seconds. tts is 'openai' or 'pyttsx3'.
    When use_female_voice is True (e.g. episode has focus_topic), use female narrator to match narration-to-mp3."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    date_spoken = normalize_text_for_tts(date_spoken)
    if tts == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        voice = "nova" if use_female_voice else "onyx"
        response = client.audio.speech.create(
            model="tts-1-hd",
            voice=voice,
            input=date_spoken,
        )
        out_path.write_bytes(response.content)
    else:
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        if use_female_voice:
            for v in voices:
                if "en-gb" in v.id.lower() and "female" in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break
            else:
                for v in voices:
                    if "female" in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
        else:
            for v in voices:
                if "en-gb" in v.id.lower():
                    engine.setProperty("voice", v.id)
                    break
        engine.setProperty("rate", 150)
        engine.save_to_file(date_spoken, str(out_path))
        engine.runAndWait()
    info = ffmpeg.probe(str(out_path))
    return float(info["format"]["duration"])


def main():
    parser = __import__("argparse").ArgumentParser(
        description="Generate map intro clip for Lewis & Clark video."
    )
    parser.add_argument("date", help="Date identifier, e.g. 18030903")
    parser.add_argument(
        "--format",
        choices=["landscape", "shorts"],
        default="landscape",
        help="Video format: landscape (16:9) or shorts (9:16 vertical for YouTube Shorts)",
    )
    parser.add_argument(
        "--shorts",
        action="store_true",
        help="Same as --format shorts (convenience for YouTube Shorts)",
    )
    parser.add_argument(
        "--location-dates",
        default=None,
        help="Fallback path to location-dates.json when no journal geo exists (deprecated when journal entries have geoDecl).",
    )
    parser.add_argument(
        "--tts",
        choices=["pyttsx3", "openai"],
        default="openai",
        help="TTS for date phrase (should match narration-to-mp3). Default: openai",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="(Unused when date TTS is used; intro length follows date clip.)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Only save the map image (PNG), skip MP4 conversion and audio/durations updates",
    )
    args = parser.parse_args()
    if args.shorts:
        args.format = "shorts"

    # Always render map at 16:9 (same framing, US only). For Shorts we letterbox to 9:16 later.
    map_size = MAP_SIZE

    date_id = args.date
    date_str = f"{date_id[:4]}-{date_id[4:6]}-{date_id[6:8]}"
    project_root = Path(__file__).resolve().parent
    journal_entries_dir = project_root / "journal-entries"

    # Prefer journal geo: current entry first, then last known from earlier entries; fall back to location-dates.json (deprecated) + geocoding
    coords_and_label = best_geo_for_date(date_id, journal_entries_dir)
    if coords_and_label:
        lat, lon, location, source_date_id = coords_and_label
        if source_date_id == date_id:
            print(f"Using journal geo: {location} ({lat}, {lon})")
        else:
            print(
                f"Using journal geo from {source_date_id} (no geo for {date_id}): {location} ({lat}, {lon})"
            )
    else:
        location_dates_path = (
            Path(args.location_dates)
            if args.location_dates
            else LOCATION_DATA_DIR / "location-dates.json"
        )
        if not location_dates_path.exists():
            legacy = project_root / "journal-entries" / "location-dates.json"
            if legacy.exists():
                location_dates_path = legacy
        if not location_dates_path.exists():
            print(
                f"[ERROR] No journal geo found for {date_id} or earlier, and location-dates not found.",
                file=sys.stderr,
            )
            sys.exit(1)
        if location_dates_path.stat().st_size == 0:
            print(
                "[ERROR] location-dates.json is empty. Use journal entries with geoDecl or add waypoint data.",
                file=sys.stderr,
            )
            sys.exit(1)
        location_dates = _load_location_dates(location_dates_path)
        match = find_location_for_date(date_id, location_dates)
        if not match:
            print(f"[ERROR] No waypoint found for date {date_id}", file=sys.stderr)
            sys.exit(1)
        location, _ = match
        coords = geocode(location)
        if not coords:
            print(f"[ERROR] Geocoding failed: {location}", file=sys.stderr)
            sys.exit(1)
        lat, lon = coords
    d = datetime.strptime(date_str, "%Y-%m-%d")
    # Full month for TTS (narrator says "September 14, 1803")
    date_spoken = d.strftime("%B %d, %Y")
    # Short month for on-screen overlay only (e.g. "Sep 14, 1803")
    date_display = d.strftime("%b %d, %Y")

    # Use episode title and focus_topic from narration JSON if present
    narration_path = Path("narrations") / f"narration{date_id}.json"
    use_female_voice = False
    if narration_path.exists():
        try:
            nar = json.loads(narration_path.read_text(encoding="utf-8"))
            episode_title = (nar.get("title") or "").strip()
            use_female_voice = bool(nar.get("focus_topic"))
        except (json.JSONDecodeError, OSError):
            episode_title = ""
    else:
        episode_title = ""
    # Two-line overlay: short date, then location (optionally with episode). No lat/long on screen.
    line2 = f"{episode_title} · {location}" if episode_title else location
    title_lines = [date_display, line2]

    video_dir = Path("movie-images") / date_id
    video_dir.mkdir(parents=True, exist_ok=True)
    output_mp4 = video_dir / "00_intro.mp4"

    # Use cached map image (no title) for this location and size, or generate and cache it
    w, h = map_size
    map_png = _map_cache_path(lat, lon, w, h)
    if map_png.exists():
        print("Using cached map image...")
    else:
        print("Generating map image (network)...")
        render_map_png(lat, lon, map_png, map_size)
        log_file_created(map_png, map_png.stat().st_size)

    if args.debug:
        debug_png = video_dir / "map_intro_debug.png"
        cache_debug_png = MAP_CACHE_DIR / f"map_intro_debug_{date_id}.png"
        shutil.copy2(map_png, debug_png)
        shutil.copy2(map_png, cache_debug_png)
        log_file_created(debug_png, debug_png.stat().st_size)
        log_file_created(cache_debug_png, cache_debug_png.stat().st_size)
        print(f"[OK] Debug: saved map image to {debug_png} and {cache_debug_png}")
        return

    # Narrator says the date while the map is shown (date_spoken has full month, e.g. "September 5, 1803")
    audio_dir = Path(f"audio/{date_id}")
    intro_mp3 = audio_dir / "intro_date.mp3"
    if intro_mp3.exists():
        intro_duration_sec = float(ffmpeg.probe(str(intro_mp3))["format"]["duration"])
        print(f"Using existing intro date audio ({round(intro_duration_sec, 2)}s)")
    else:
        if use_female_voice:
            print(
                f'Synthesizing date for intro ({args.tts}, female voice for focus_topic): "{date_spoken}"...'
            )
        else:
            print(f'Synthesizing date for intro ({args.tts}): "{date_spoken}"...')
        intro_duration_sec = _synthesize_date_tts(
            date_spoken, intro_mp3, args.tts, use_female_voice=use_female_voice
        )
        log_file_created(intro_mp3, intro_mp3.stat().st_size)
    # Use date clip length so video and prepended audio stay in sync (--duration ignored when using date TTS)
    duration_sec = intro_duration_sec

    print("Creating intro video with title...")
    output_size = MAP_SIZE_SHORTS if args.format == "shorts" else None
    image_to_video_with_title(
        map_png, output_mp4, title_lines, duration_sec, output_size=output_size
    )
    log_file_created(output_mp4, output_mp4.stat().st_size)

    # Prepend intro to durations.json only (so segment count matches 00_intro + clips).
    # Do not prepend to final.mp3: assembly (videos-mp3-to-movie) builds full audio as intro_date.mp3 + final.mp3.
    durations_path = Path(f"audio/{date_id}/durations.json")
    if not durations_path.exists():
        print("[WARN] durations.json not found; run narration-to-mp3 first", file=sys.stderr)
        return
    durations = json.loads(durations_path.read_text(encoding="utf-8"))
    intro_entry = {"file": "intro", "duration": round(duration_sec, 3)}
    if not durations or durations[0].get("file") != "intro":
        durations.insert(0, intro_entry)
        durations_path.write_text(json.dumps(durations, indent=2))
        print(f"Prepended intro to {durations_path}")
    print(f"[OK] Map intro: {output_mp4}")


if __name__ == "__main__":
    main()
