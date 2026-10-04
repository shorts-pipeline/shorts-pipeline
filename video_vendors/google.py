"""Google Veo video vendor — Gemini API text-to-video."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline_logging import log_api_call_with_bodies, log_file_created


def _elapsed(start: float) -> str:
    s = int(time.monotonic() - start)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s}s"


# Retry settings for 429 rate limits
RETRY_MAX_ATTEMPTS = 5
RETRY_BASE_DELAY = 60

_print_lock = threading.Lock()


def _plog(msg: str) -> None:
    with _print_lock:
        print(msg)


def _google_obj_snip(obj: object, limit: int = 12_000) -> str:
    s = str(obj)
    return s if len(s) <= limit else s[: limit - 20] + " ... [truncated]"


def _capture_error(e: Exception) -> str:
    """Extract full error message from API exceptions."""
    msg = str(e)
    if hasattr(e, "message") and e.message:
        msg = str(e.message)
    if hasattr(e, "details") and e.details:
        msg += f" | details: {e.details}"
    if hasattr(e, "response") and e.response is not None:
        body = getattr(e.response, "body", None) or getattr(e.response, "text", None)
        if body:
            msg += f" | response: {body}"
    return msg


class GoogleVendor:
    """Generates one clip per prompt via Google Veo. Requires GOOGLE_API_KEY."""

    name = "google"

    def __init__(
        self,
        model: str = "veo-2.0-generate-001",
        resolution: str = "720p",
        duration_seconds: int = 8,
        **kwargs,
    ):
        self.model = model
        self.resolution = resolution
        self.duration_seconds = duration_seconds

    def generate(
        self,
        date_id: str,
        prompts: list[str],
        output_dir: Path,
        segment_indices: set[int] | None = None,
        **kwargs,
    ) -> list[Path]:
        """Generate one MP4 per prompt; saves to output_dir as 01.mp4, 02.mp4, ..."""
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise ImportError(
                "google-genai required for google vendor. Install with: pip install google-genai"
            ) from e

        api_key = (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_GEMINI_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not api_key:
            raise ValueError("Set GOOGLE_API_KEY environment variable for google vendor")

        client = genai.Client(api_key=api_key)
        default_duration = kwargs.get("duration_seconds", self.duration_seconds)
        model = kwargs.get("model", self.model)
        aspect_ratio = kwargs.get("aspect_ratio", "9:16")
        concurrency = max(1, int(kwargs.get("concurrency", 1)))
        # Per-segment durations from audio; Veo only supports 4, 6, 8 seconds
        target_durations: list[float] | None = kwargs.get("target_durations")

        def _veo_duration_seconds(sec: float) -> int:
            sec = min(10.0, max(0, sec))
            if sec <= 5:
                return 4
            if sec <= 7:
                return 6
            return 8

        output_dir.mkdir(parents=True, exist_ok=True)
        paths_by_index: dict[int, Path] = {}

        if segment_indices is None:
            indices = set(range(1, len(prompts) + 1))
        else:
            indices = set(segment_indices)
        work_items: list[tuple[int, str]] = []

        for idx, prompt in enumerate(prompts, start=1):
            if idx not in indices:
                if (output_dir / f"{idx:02d}.mp4").exists():
                    paths_by_index[idx] = output_dir / f"{idx:02d}.mp4"
                continue
            out_path = output_dir / f"{idx:02d}.mp4"
            if out_path.exists():
                _plog(f"   Skipping segment {idx} (already exists: {out_path.name})")
                paths_by_index[idx] = out_path
                continue
            work_items.append((idx, prompt))

        def _generate_one(idx: int, prompt: str) -> tuple[int, Path]:
            duration_sec = (
                _veo_duration_seconds(target_durations[idx - 1])
                if target_durations and idx <= len(target_durations)
                else default_duration
            )
            config = types.GenerateVideosConfig(
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_sec,
            )

            seg_start = time.monotonic()
            _plog(f"Google Veo generating segment {idx}/{len(prompts)}...")
            operation = None
            for attempt in range(RETRY_MAX_ATTEMPTS):
                try:
                    operation = client.models.generate_videos(
                        model=model,
                        prompt=prompt[:500],  # Truncate if needed
                        config=config,
                    )
                    log_api_call_with_bodies(
                        "google_veo",
                        "models.generate_videos",
                        request_body={
                            "model": model,
                            "prompt": prompt[:4000],
                            "config": {
                                "aspect_ratio": aspect_ratio,
                                "duration_seconds": duration_sec,
                            },
                        },
                        response_body={"operation": _google_obj_snip(operation)},
                        model=model,
                        extra={"segment": idx, "attempt": attempt + 1},
                    )
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    if "429" in err_str or "quota" in err_str or "rate" in err_str:
                        if attempt < RETRY_MAX_ATTEMPTS - 1:
                            delay = RETRY_BASE_DELAY * (2**attempt)
                            _plog(
                                f"   Rate limited, retrying in {delay}s (attempt {attempt + 1}/{RETRY_MAX_ATTEMPTS})..."
                            )
                            time.sleep(delay)
                        else:
                            raise RuntimeError(_capture_error(e)) from e
                    else:
                        raise RuntimeError(_capture_error(e)) from e

            assert operation is not None
            poll_count = 0
            while not operation.done:
                time.sleep(10)
                poll_count += 1
                if poll_count % 3 == 0:
                    _plog(
                        f"   Segment {idx}/{len(prompts)} still generating... (elapsed {_elapsed(seg_start)})"
                    )
                try:
                    prev_op = _google_obj_snip(operation)
                    operation = client.operations.get(operation)
                    log_api_call_with_bodies(
                        "google_veo",
                        "operations.get",
                        request_body={"operation_before": prev_op},
                        response_body={
                            "operation_after": _google_obj_snip(operation),
                            "done": bool(getattr(operation, "done", False)),
                        },
                        model=model,
                        extra={"segment": idx, "poll": poll_count},
                    )
                except Exception as e:
                    raise RuntimeError(_capture_error(e)) from e

            result = getattr(operation, "result", None) or getattr(operation, "response", None)
            if result is None or not getattr(result, "generated_videos", None):
                reasons = getattr(result, "rai_media_filtered_reasons", None) or []
                reasons_txt = "; ".join(reasons) if reasons else "unknown"
                raise RuntimeError(f"Video filtered (no output): {reasons_txt}")
            gen_video = result.generated_videos[0]
            client.files.download(file=gen_video.video)
            out_path = output_dir / f"{idx:02d}.mp4"
            gen_video.video.save(str(out_path))
            sz = out_path.stat().st_size if out_path.is_file() else 0
            log_api_call_with_bodies(
                "google_veo",
                "files.download",
                request_body={"file": _google_obj_snip(gen_video.video, limit=8000)},
                response_body={"saved_path": str(out_path), "bytes": sz},
                model=model,
                extra={"segment": idx},
            )
            log_file_created(out_path, out_path.stat().st_size)
            _plog(f"   Saved {out_path.name}")
            return idx, out_path

        if not work_items:
            paths = [paths_by_index[i] for i in sorted(paths_by_index)]
            print(f"[OK] Google Veo generated {len(paths)} clips in {output_dir}")
            return paths

        if concurrency == 1 or len(work_items) == 1:
            for idx, prompt in work_items:
                i, pth = _generate_one(idx, prompt)
                paths_by_index[i] = pth
        else:
            max_workers = min(concurrency, len(work_items))
            print(f"[INFO] google concurrency: {max_workers}")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                fut_to_idx = {
                    executor.submit(_generate_one, idx, prompt): idx for idx, prompt in work_items
                }
                for fut in as_completed(fut_to_idx):
                    idx, pth = fut.result()
                    paths_by_index[idx] = pth

        paths = [paths_by_index[i] for i in sorted(paths_by_index)]
        print(f"[OK] Google Veo generated {len(paths)} clips in {output_dir}")
        return paths
