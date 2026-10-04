"""Canonical per-segment plan built from merged narration JSON."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.broll_scene_anchor import scene_anchor_eligible_for_visual_mode
from pipeline.narration_utils import (
    narration_json_expects_dialogue_mode,
    narration_json_expects_long_conversation_mode,
)
from pipeline.narration_visual_mode import (
    VISUAL_MODE_B_ROLL,
    VISUAL_MODE_TALKING_HEAD,
    normalize_visual_mode,
)
from pipeline.phase1_speaker_dialogue_rules import segment_expects_one_cast_speaker_per_segment
from pipeline.tts_speaker_voice import segment_unique_cast_dialogue_speakers


def _index_1based(seg: dict[str, Any], fallback: int) -> int:
    raw = seg.get("segment_index")
    if isinstance(raw, int) and raw > 0:
        return raw
    try:
        if raw is not None:
            n = int(raw)
            if n > 0:
                return n
    except (TypeError, ValueError):
        pass
    return fallback


def _resolve_anchor_path(
    movie_dir: Path,
    segment_index: int,
    character_id: str,
) -> Path | None:
    try:
        from video_vendors.fal import FalVendor
    except ImportError:
        return None
    cid = (character_id or "").strip()
    if cid:
        p = FalVendor._existing_scene_anchor_path(movie_dir, segment_index, cid)
        if p is not None:
            return p
        try:
            from pipeline.conversation_omnihuman_mask import (
                existing_omnihuman_anchor_for_segment,
            )
            from pipeline.conversation_scene_anchor import (
                conversation_uses_shared_master_bookend,
                conversation_uses_shared_master_mask,
            )

            if conversation_uses_shared_master_mask():
                omni = existing_omnihuman_anchor_for_segment(movie_dir, segment_index, cid)
                if omni is not None:
                    return omni
            if conversation_uses_shared_master_bookend():
                from pipeline.conversation_bookend_anchor import (
                    existing_bookend_anchor_for_segment,
                )

                bookend_p = existing_bookend_anchor_for_segment(movie_dir, segment_index, cid)
                if bookend_p is not None:
                    return bookend_p
        except ImportError:
            pass
    return FalVendor._existing_segment_anchor_path(movie_dir, segment_index)


@dataclass(frozen=True)
class SegmentPlan:
    """One ``narration_script`` row plus resolved artifact paths and routing flags."""

    index: int
    visual_mode: str
    talking_head_subject: str
    reference_character_id: str
    cast_speakers: tuple[str, ...]
    expects_one_cast_speaker: bool
    scene_anchor_eligible: bool
    row: dict[str, Any]
    audio_mp3: Path
    talking_head_drive_mp3: Path
    clip_mp4: Path
    anchor_path: Path | None = None

    @property
    def is_talking_head(self) -> bool:
        return self.visual_mode == VISUAL_MODE_TALKING_HEAD

    @property
    def is_b_roll(self) -> bool:
        return self.visual_mode == VISUAL_MODE_B_ROLL

    @property
    def has_dialogue(self) -> bool:
        dlg = self.row.get("dialogue")
        if not isinstance(dlg, list):
            return False
        return any(isinstance(line, dict) and str(line.get("text") or "").strip() for line in dlg)

    @property
    def audio_mp3_exists(self) -> bool:
        return self.audio_mp3.is_file()

    @property
    def clip_mp4_exists(self) -> bool:
        return self.clip_mp4.is_file()

    @property
    def anchor_exists(self) -> bool:
        return self.anchor_path is not None and self.anchor_path.is_file()


@dataclass(frozen=True)
class EpisodeSegmentPlans:
    date_id: str
    dialogue_mode: bool
    long_conversation_mode: bool
    segments: tuple[SegmentPlan, ...]

    def __len__(self) -> int:
        return len(self.segments)

    def __iter__(self) -> Iterator[SegmentPlan]:
        return iter(self.segments)

    def by_index(self) -> dict[int, SegmentPlan]:
        return {plan.index: plan for plan in self.segments}

    def get(self, index: int) -> SegmentPlan | None:
        return self.by_index().get(index)

    def visual_modes(self) -> list[str]:
        return [plan.visual_mode for plan in self.segments]

    def indices_in_scope(self, segment_indices: set[int] | None) -> set[int]:
        if segment_indices is None:
            return {plan.index for plan in self.segments}
        return {plan.index for plan in self.segments if plan.index in segment_indices}

    def talking_head_indices(self, segment_indices: set[int] | None = None) -> set[int]:
        scope = self.indices_in_scope(segment_indices)
        return {
            plan.index for plan in self.segments if plan.index in scope and plan.is_talking_head
        }

    def b_roll_indices(self, segment_indices: set[int] | None = None) -> set[int]:
        scope = self.indices_in_scope(segment_indices)
        return {plan.index for plan in self.segments if plan.index in scope and plan.is_b_roll}

    def wan_indices(self, segment_indices: set[int] | None = None) -> set[int]:
        """Wan / B-roll clip indices (excludes ``talking_head``)."""
        return self.b_roll_indices(segment_indices)

    def in_scope(self, segment_indices: set[int] | None) -> tuple[SegmentPlan, ...]:
        if segment_indices is None:
            return self.segments
        return tuple(plan for plan in self.segments if plan.index in segment_indices)

    def scene_anchor_eligible(self) -> tuple[SegmentPlan, ...]:
        return tuple(plan for plan in self.segments if plan.scene_anchor_eligible)


def build_episode_segment_plans(
    narration: dict[str, Any],
    *,
    date_id: str,
    repo_root: Path | None = None,
    resolve_disk_paths: bool = True,
) -> EpisodeSegmentPlans:
    """
    Build ordered segment plans from merged narration JSON.

    ``repo_root`` defaults to ``Path.cwd()`` so paths align with pipeline scripts
    run from the project root.
    """
    root = (repo_root or Path.cwd()).resolve()
    script = narration.get("narration_script") or []
    if not isinstance(script, list):
        script = []

    long_conv = narration_json_expects_long_conversation_mode(narration)
    dialogue_mode = narration_json_expects_dialogue_mode(narration)

    audio_dir = root / "audio" / date_id / "segments"
    movie_dir = root / "movie-images" / date_id

    plans: list[SegmentPlan] = []
    for i, seg in enumerate(script, start=1):
        if not isinstance(seg, dict):
            continue
        idx = _index_1based(seg, i)
        mode = normalize_visual_mode(seg)
        if mode not in (VISUAL_MODE_B_ROLL, VISUAL_MODE_TALKING_HEAD):
            mode = VISUAL_MODE_B_ROLL

        th_subj = str(seg.get("talking_head_subject") or "").strip().lower()
        ref_char = str(seg.get("reference_character_id") or "").strip().lower()
        cast = tuple(segment_unique_cast_dialogue_speakers(seg))

        anchor_path: Path | None = None
        if resolve_disk_paths:
            anchor_path = _resolve_anchor_path(movie_dir, idx, th_subj or ref_char)

        plans.append(
            SegmentPlan(
                index=idx,
                visual_mode=mode,
                talking_head_subject=th_subj,
                reference_character_id=ref_char,
                cast_speakers=cast,
                expects_one_cast_speaker=segment_expects_one_cast_speaker_per_segment(
                    seg, long_conversation_mode=long_conv
                ),
                scene_anchor_eligible=scene_anchor_eligible_for_visual_mode(mode, segment_row=seg),
                row=seg,
                audio_mp3=audio_dir / f"{idx:02d}.mp3",
                talking_head_drive_mp3=audio_dir / f"{idx:02d}_talking_head_drive.mp3",
                clip_mp4=movie_dir / f"{idx:02d}.mp4",
                anchor_path=anchor_path,
            )
        )

    return EpisodeSegmentPlans(
        date_id=date_id,
        dialogue_mode=dialogue_mode,
        long_conversation_mode=long_conv,
        segments=tuple(plans),
    )
