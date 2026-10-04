"""Video-cue and narration-segments view payloads for pipeline_ui (Produce tab).

Split out of ``server.py`` (see ``ai-plans/pipeline-ui-server-split.md``, step 3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import video_manifest
from pipeline.narration_utils import (
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
)
from pipeline_ui.paths import (
    _date_id_from_output_video,
    _journal_date_to_date_id,
    _narrative_segment_num_from_duration_file,
    _safe_journal_date,
    latest_video_path,
)
from pipeline_ui.runtime import srv as _srv
from pipeline_ui.youtube_view import _output_video_path_for_date_id
from video_vendors.fal_avatar import portrait_path_for_talking_head


def _week_arc_marker(nar: dict[str, Any]) -> dict[str, Any]:
    """Week-arc marker fields for UI badges, from a narration JSON's ``week_arc_ref``."""
    ref = nar.get("week_arc_ref")
    if not isinstance(ref, dict):
        return {"week_arc_active": False, "week_arc_week_id": None, "week_arc_role": None}
    return {
        "week_arc_active": True,
        "week_arc_week_id": ref.get("week_id"),
        "week_arc_role": ref.get("role"),
    }


def _ui_text_preview(text: Any, max_len: int = 100) -> str:
    s = "" if text is None else str(text).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _segment_cue_fields_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Per-segment fields for video cue UI and narration-segments API."""
    vm_raw = row.get("visual_mode")
    visual_mode = str(vm_raw).strip() if vm_raw not in (None, "") else "b_roll"
    th_subj = row.get("talking_head_subject")
    ref_char = row.get("reference_character_id")
    narration_val = row.get("narration")
    if isinstance(narration_val, list):
        narration_text = " ".join(str(x) for x in narration_val)
    else:
        narration_text = "" if narration_val is None else str(narration_val)

    dialogue_rows: list[dict[str, str]] = []
    dlg = row.get("dialogue")
    if isinstance(dlg, list):
        for line in dlg:
            if not isinstance(line, dict):
                continue
            sp = line.get("speaker_id")
            tx = line.get("text")
            speaker_id = "" if sp is None else str(sp).strip()
            text = "" if tx is None else str(tx)
            dialogue_rows.append(
                {
                    "speaker_id": speaker_id or "?",
                    "text": text,
                    "preview": _ui_text_preview(text, 80),
                }
            )

    vp = row.get("video_prompt")
    thp = row.get("talking_head_prompt")
    of = row.get("opening_frame")
    return {
        "visual_mode": visual_mode,
        "talking_head_subject": "" if th_subj is None else str(th_subj).strip(),
        "reference_character_id": "" if ref_char is None else str(ref_char).strip(),
        "narration_preview": _ui_text_preview(narration_text, 100),
        "dialogue": dialogue_rows,
        "video_prompt": "" if vp is None else str(vp),
        "talking_head_prompt": "" if thp is None else str(thp),
        "opening_frame": "" if of is None else str(of),
    }


def video_preview_cues_payload(
    journal_date: str | None = None,
    *,
    require_output_video: bool = True,
) -> dict[str, Any]:
    """
    Timeline for an output video: cumulative audio/assembly durations from durations.json
    paired with video_prompt from narration JSON. Use journal_date YYYY-MM-DD or latest shortcut.

    When ``require_output_video`` is False (anchor preview), only ``durations.json`` and
    narration JSON are required for the picker date.
    """
    repo_root = _srv()._REPO_ROOT
    vpath: Path | None = None
    date_id: str | None = None
    if journal_date:
        safe = _safe_journal_date(journal_date.strip())
        if not safe:
            return {
                "ok": False,
                "error": "invalid_date",
                "message": "Use YYYY-MM-DD in expedition years.",
            }
        did = _journal_date_to_date_id(safe)
        if not did:
            return {"ok": False, "error": "invalid_date", "message": "Bad date_id."}
        if require_output_video:
            vpath = _output_video_path_for_date_id(did)
            if not vpath:
                return {
                    "ok": False,
                    "error": "no_video",
                    "date_id": did,
                    "message": f"No assembled video at output/*_{did}_video.mp4 for {safe}.",
                }
        else:
            vpath = _srv()._anchor_preview_video_path_for_date_id(did)
        date_id = did
    else:
        vpath = latest_video_path()
        if not vpath:
            return {"ok": False, "error": "no_video", "message": "No latest_video.url target."}
        date_id = _date_id_from_output_video(vpath.resolve())
        if not date_id:
            return {
                "ok": False,
                "error": "unknown_video_name",
                "message": f"Could not parse date id from filename: {vpath.name!r}",
            }
    dur_path = repo_root / "audio" / date_id / "durations.json"
    nar_path = repo_root / "narrations" / f"narration{date_id}.json"
    if not dur_path.is_file():
        return {
            "ok": False,
            "error": "no_durations",
            "date_id": date_id,
            "message": f"Missing audio/{date_id}/durations.json for this output video.",
        }
    try:
        durations = json.loads(dur_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": "durations_read_failed", "message": str(e)}
    if not isinstance(durations, list) or not durations:
        return {
            "ok": False,
            "error": "durations_invalid",
            "message": "durations.json is empty or not a list.",
        }

    script: list[dict[str, Any]] = []
    title = ""
    dialogue_mode = False
    long_conversation_mode = False
    focus_topic: str | None = None
    week_arc_marker = _week_arc_marker({})
    if nar_path.is_file():
        try:
            nar = json.loads(nar_path.read_text(encoding="utf-8"))
            raw_script = nar.get("narration_script")
            if isinstance(raw_script, list):
                script = [x for x in raw_script if isinstance(x, dict)]
            t = nar.get("title")
            if isinstance(t, str):
                title = t.strip()
            if isinstance(nar, dict):
                dialogue_mode = narration_json_expects_dialogue_mode(nar)
                long_conversation_mode = narration_json_expects_long_conversation_mode(nar)
                ft = nar.get("focus_topic")
                if ft is not None and str(ft).strip():
                    focus_topic = str(ft).strip()
                week_arc_marker = _week_arc_marker(nar)
        except (json.JSONDecodeError, OSError):
            pass

    parts: list[dict[str, Any]] = []
    t_cursor = 0.0
    for entry in durations:
        if not isinstance(entry, dict):
            continue
        try:
            dur = float(entry["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        file_key = entry.get("file")
        start = t_cursor
        end = t_cursor + dur
        if file_key == "intro":
            parts.append(
                {
                    "start_sec": round(start, 4),
                    "end_sec": round(end, 4),
                    "kind": "intro",
                    "narrative_segment_index": None,
                    "label": "Intro (map / date)",
                    "video_prompt": "",
                }
            )
        else:
            seg_n = _narrative_segment_num_from_duration_file(str(file_key) if file_key else "")
            cue_fields: dict[str, Any] = {
                "visual_mode": "b_roll",
                "talking_head_subject": "",
                "reference_character_id": "",
                "narration_preview": "",
                "dialogue": [],
                "video_prompt": "",
                "talking_head_prompt": "",
                "opening_frame": "",
            }
            if seg_n is not None and 1 <= seg_n <= len(script):
                cue_fields = _segment_cue_fields_from_row(script[seg_n - 1])
            label = f"Segment {seg_n}" if seg_n is not None else (str(file_key) or "Unknown")
            parts.append(
                {
                    "start_sec": round(start, 4),
                    "end_sec": round(end, 4),
                    "kind": "narrative" if seg_n is not None else "other",
                    "narrative_segment_index": seg_n,
                    "label": label,
                    **cue_fields,
                }
            )
        t_cursor = end

    root = repo_root.resolve()
    output_video_rel = ""
    manifest_rel = ""
    manifest_exists = False
    youtube_url = ""
    video_filename = ""
    if vpath is not None and vpath.is_file():
        vresolved = vpath.resolve()
        video_filename = vpath.name
        try:
            output_video_rel = str(vresolved.relative_to(root)).replace("\\", "/")
        except ValueError:
            output_video_rel = ""
        mpath = video_manifest.manifest_path_for_video(vresolved)
        try:
            manifest_rel = str(mpath.relative_to(root)).replace("\\", "/")
        except ValueError:
            manifest_rel = ""
        manifest_exists = mpath.is_file()
        if manifest_exists:
            md = video_manifest.load(mpath)
            if isinstance(md, dict):
                u = md.get("youtube_url")
                if isinstance(u, str) and u.strip():
                    youtube_url = u.strip()

    if long_conversation_mode:
        prompt_pack_hint = "lewis_clark_long_conversation"
    elif dialogue_mode:
        prompt_pack_hint = "lewis_clark_dialogue"
    else:
        prompt_pack_hint = "lewis_clark"

    return {
        "ok": True,
        "date_id": date_id,
        "title": title,
        "dialogue_mode": dialogue_mode,
        "long_conversation_mode": long_conversation_mode,
        "focus_topic": focus_topic,
        **week_arc_marker,
        "prompt_pack_hint": prompt_pack_hint,
        "total_duration_sec": round(t_cursor, 4),
        "parts": parts,
        "video_filename": video_filename,
        "output_video_rel": output_video_rel,
        "manifest_rel": manifest_rel,
        "manifest_exists": manifest_exists,
        "youtube_url": youtube_url,
    }


def narration_segments_payload(journal_date: str) -> dict[str, Any]:
    """
    Structured per-segment rows for the Produce tab (dialogue, talking_head, clip/anchor status).
    """
    repo_root = _srv()._REPO_ROOT
    safe = _safe_journal_date((journal_date or "").strip())
    if not safe:
        return {
            "ok": False,
            "error": "invalid_date",
            "message": "Use YYYY-MM-DD in expedition years.",
        }
    date_id = _journal_date_to_date_id(safe)
    if not date_id:
        return {"ok": False, "error": "invalid_date", "message": "Bad date_id."}
    narr_path = repo_root / "narrations" / f"narration{date_id}.json"
    if not narr_path.is_file():
        return {
            "ok": False,
            "error": "no_file",
            "journal_date": safe,
            "date_id": date_id,
            "message": f"No narrations/narration{date_id}.json for {safe}.",
        }
    try:
        data = json.loads(narr_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": "read_failed", "message": str(e)}
    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": "invalid_json",
            "message": "Top-level JSON must be an object.",
        }

    raw_script = data.get("narration_script")
    script: list[dict[str, Any]] = (
        [x for x in raw_script if isinstance(x, dict)] if isinstance(raw_script, list) else []
    )
    mi_dir = repo_root / "movie-images" / date_id
    audio_seg_dir = repo_root / "audio" / date_id / "segments"
    from pipeline.anchor_preview import check_tts_prerequisites, plan_preview_clip
    from video_vendors import build_prompts
    from video_vendors.fal import FalVendor

    prompts_fal = build_prompts(date_id, narrations_dir=repo_root / "narrations", vendor="fal")
    dur_path_seg = repo_root / "audio" / date_id / "durations.json"
    durs: list[dict[str, Any]] = []
    if dur_path_seg.is_file():
        try:
            raw_d = json.loads(dur_path_seg.read_text(encoding="utf-8"))
            if isinstance(raw_d, list):
                durs = [x for x in raw_d if isinstance(x, dict)]
        except (json.JSONDecodeError, OSError):
            durs = []
    n_intro_dur = 1 if durs and durs[0].get("file") == "intro" else 0

    from pipeline.conversation_scene_anchor import conversation_anchor_ui_enrichment

    conv_ui = conversation_anchor_ui_enrichment(data)
    conv_by_seg = conv_ui.get("conversation_by_segment") or {}

    segments: list[dict[str, Any]] = []
    for i, seg in enumerate(script, start=1):
        cue = _segment_cue_fields_from_row(seg)
        visual_mode = cue["visual_mode"]
        th_subj = cue.get("talking_head_subject") or ""
        ref_char = cue.get("reference_character_id") or ""
        # Full narration text for editors (preview may be truncated).
        narration_val = seg.get("narration")
        if isinstance(narration_val, list):
            narration_full = " ".join(str(x) for x in narration_val)
        else:
            narration_full = "" if narration_val is None else str(narration_val)

        clip_rel = f"movie-images/{date_id}/{i:02d}.mp4"
        clip_exists = (mi_dir / f"{i:02d}.mp4").is_file()
        seg_dur = 0.0
        for j, entry in enumerate(durs):
            if entry.get("file") == "intro":
                continue
            if j - n_intro_dur + 1 == i:
                try:
                    seg_dur = float(entry.get("duration") or 0.0)
                except (TypeError, ValueError):
                    seg_dur = 0.0
                break
        anchor_path = _srv()._anchor_image_path_for_segment(date_id, i)
        from pipeline.broll_scene_anchor import segment_eligible_for_scene_anchor

        scene_anchor_eligible = segment_eligible_for_scene_anchor(data, i)
        scene_anchor_reason = ""
        if not scene_anchor_eligible and visual_mode == "b_roll":
            scene_anchor_reason = "no_portrait_character"
        anchor_method = "t2i"
        if 0 < i <= len(prompts_fal):
            _rest, _cid, _scene_anchor = FalVendor._parse_markers(prompts_fal[i - 1])
            from video_vendors.fal import FalVendor as _FV

            if _cid and _FV._portrait_path_for_character(repo_root, _cid) is not None:
                anchor_method = "i2i"
            elif ref_char and anchor_method == "t2i":
                scene_anchor_reason = "no_portrait"
        plan = plan_preview_clip(
            repo_root=repo_root,
            date_id=date_id,
            segment_index=i,
            duration_sec=seg_dur,
            narr=data,
        )
        anchor_preview_source = plan.source
        portrait_status = "n/a"
        if visual_mode == "talking_head":
            try:
                portrait_path_for_talking_head(
                    repo_root,
                    th_subj,
                    ref_char or None,
                )
                portrait_status = "ok"
            except (ValueError, FileNotFoundError, OSError) as e:
                portrait_status = str(e)[:160]

        seg_mp3 = audio_seg_dir / f"{i:02d}.mp3"
        seg_out: dict[str, Any] = {
            "index": i,
            **cue,
            "segment_mp3_exists": seg_mp3.is_file(),
            "segment_mp3_rel": (
                str(seg_mp3.relative_to(repo_root)).replace("\\", "/")
                if seg_mp3.is_file()
                else f"audio/{date_id}/segments/{i:02d}.mp3"
            ),
            "narration_full": narration_full,
            "video_prompt_preview": _ui_text_preview(cue.get("video_prompt"), 100),
            "video_prompt_full": cue.get("video_prompt") or "",
            "talking_head_prompt_preview": _ui_text_preview(cue.get("talking_head_prompt"), 80),
            "talking_head_prompt_full": cue.get("talking_head_prompt") or "",
            "opening_frame_preview": _ui_text_preview(cue.get("opening_frame"), 80),
            "opening_frame_full": cue.get("opening_frame") or "",
            "clip_exists": clip_exists,
            "clip_rel": clip_rel if clip_exists else "",
            "anchor_exists": anchor_path is not None,
            "portrait_status": portrait_status,
            "scene_anchor_eligible": scene_anchor_eligible,
            "scene_anchor_eligible_reason": scene_anchor_reason,
            "anchor_method": anchor_method,
            "anchor_preview_source": anchor_preview_source,
        }
        conv_extra = conv_by_seg.get(str(i))
        if isinstance(conv_extra, dict):
            seg_out.update(conv_extra)
        segments.append(seg_out)

    focus_raw = data.get("focus_topic")
    focus_topic: str | None
    if focus_raw is None or str(focus_raw).strip() == "":
        focus_topic = None
    else:
        focus_topic = str(focus_raw).strip()

    dialogue_mode = narration_json_expects_dialogue_mode(data)
    long_conversation_mode = narration_json_expects_long_conversation_mode(data)
    if long_conversation_mode:
        prompt_pack_hint = "lewis_clark_long_conversation"
    elif dialogue_mode:
        prompt_pack_hint = "lewis_clark_dialogue"
    else:
        prompt_pack_hint = "lewis_clark"

    week_arc_marker = _week_arc_marker(data)

    rules: list[str] = []
    if week_arc_marker["week_arc_active"]:
        role = week_arc_marker.get("week_arc_role")
        role_suffix = f" (role: {role})" if role else ""
        rules.append(
            f"Week arc: week_{week_arc_marker['week_arc_week_id']}{role_suffix} — "
            "focus topic disabled for this episode."
        )
    if long_conversation_mode:
        rules.append(
            "Long conversation: denser cast dialogue; up to 12 consecutive talking_head; "
            "one dialogue line per talking_head segment."
        )
        conv_runs = conv_ui.get("conversation_runs") or []
        if conv_runs:
            rules.append(
                "Shared backdrop: "
                + str(len(conv_runs))
                + " talking_head run(s) use one composite master scene anchor (split per speaker). "
                "Prefer Produce → Anchor preview → Build anchors; Shots → Scene anchor rebuilds the whole run."
            )
    elif dialogue_mode:
        rules.append(
            "Standard dialogue: first talking_head at segment 3+; at most 2 consecutive talking_head."
        )
    if focus_topic:
        rules.append(
            "Focus topic set — new Phase 1 runs suppress dialogue (single-narrator deep dive)."
        )

    title = data.get("title")
    title_str = title.strip() if isinstance(title, str) else ""
    voice_sidecar = (repo_root / "narrations" / f"narration{date_id}_voice.json").is_file()
    visual_sidecar = (repo_root / "narrations" / f"narration{date_id}_visual.json").is_file()
    tts_ok, tts_err = check_tts_prerequisites(repo_root, date_id)
    final_mp3 = repo_root / "audio" / date_id / "final.mp3"
    seg_mp3_count = len(list(audio_seg_dir.glob("*.mp3"))) if audio_seg_dir.is_dir() else 0

    return {
        "ok": True,
        "journal_date": safe,
        "date_id": date_id,
        "title": title_str,
        "focus_topic": focus_topic,
        "dialogue_mode": dialogue_mode,
        "long_conversation_mode": long_conversation_mode,
        **week_arc_marker,
        "prompt_pack_hint": prompt_pack_hint,
        "rules_summary": rules,
        "talking_head_segment_count": sum(
            1 for s in segments if s.get("visual_mode") == "talking_head"
        ),
        "segment_count": len(segments),
        "sidecars": {"voice": voice_sidecar, "visual": visual_sidecar},
        "segments": segments,
        "narration_rel": f"narrations/narration{date_id}.json",
        "tts_ready": tts_ok,
        "tts_error": tts_err if not tts_ok else "",
        "final_mp3_exists": final_mp3.is_file(),
        "final_mp3_rel": (
            str(final_mp3.relative_to(repo_root)).replace("\\", "/")
            if final_mp3.is_file()
            else f"audio/{date_id}/final.mp3"
        ),
        "segment_mp3_count": seg_mp3_count,
        "conversation_runs": conv_ui.get("conversation_runs") or [],
    }
