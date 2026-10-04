"""Guards against duplicate and out-of-order YouTube uploads.

Pure filesystem / manifest helpers (no google API imports), shared by
``youtube_upload.py`` (CLI + ``upload_video``), ``run-daily.py`` (the ``--upload``
step), and ``pipeline_ui/youtube_view.py`` (upload-from-preview) so every route to
an upload enforces the same two rules:

1. **Idempotency.** A manifest that already carries ``youtube_video_id`` has been
   uploaded; uploading again just creates a duplicate video (nothing in the
   YouTube ``videos().insert`` path is idempotent). ``already_uploaded_video_id``
   reports the existing id so callers refuse unless the operator passes
   ``force`` / ``--force``.

2. **Sequence.** Episodes publish in journal-date order.
   ``earlier_unuploaded_episodes`` lists assembled ``output/*_video.mp4`` that sit
   between the most recent already-uploaded episode and the target, yet have no
   ``youtube_video_id``. Callers refuse (unless ``allow_gap`` / ``--allow-gap``)
   so a later episode cannot jump the queue. The high-water-mark lower bound keeps
   ragged pre-convention manifests in the deep back catalogue from tripping it.
"""

from __future__ import annotations

from pathlib import Path

import video_manifest
from pipeline.output_naming import (
    ANCHOR_PREVIEW_OUTPUT_PREFIX,
    date_id_from_output_video_stem,
    output_prefix_from_assembled_stem,
)


class DuplicateUploadError(RuntimeError):
    """The target video's manifest already records a youtube_video_id."""

    def __init__(self, date_id: str, video_id: str, manifest_path: Path | str):
        self.date_id = date_id
        self.video_id = video_id
        self.manifest_path = Path(manifest_path)
        super().__init__(
            f"{date_id or 'video'} already uploaded as youtube_video_id={video_id} "
            f"({self.manifest_path.name}). Re-uploading creates a duplicate; pass "
            f"force / --force to override."
        )


class UploadSequenceError(RuntimeError):
    """Earlier assembled episodes have not been uploaded yet."""

    def __init__(self, date_id: str, missing: list[str]):
        self.date_id = date_id
        self.missing = list(missing)
        shown = ", ".join(self.missing[:10]) + (
            f", +{len(self.missing) - 10} more" if len(self.missing) > 10 else ""
        )
        super().__init__(
            f"{date_id} would upload out of order: {len(self.missing)} earlier "
            f"episode(s) assembled but not on YouTube ({shown}). Upload those first, "
            f"or pass allow_gap / --allow-gap."
        )


def already_uploaded_video_id(manifest_path: Path | str | None) -> str | None:
    """Return the manifest's ``youtube_video_id`` (non-empty) or ``None``."""
    if not manifest_path:
        return None
    data = video_manifest.load(Path(manifest_path)) or {}
    vid = str(data.get("youtube_video_id") or "").strip()
    return vid or None


def _assembled_episodes(output_dir: Path) -> list[tuple[str, Path]]:
    """(date_id, mp4) for real episode outputs in ``output_dir`` — excludes the
    anchor-preview outputs, which share the ``<prefix>_<id>_video.mp4`` shape."""
    out: list[tuple[str, Path]] = []
    if not output_dir.is_dir():
        return out
    for mp4 in output_dir.glob("*_video.mp4"):
        did = date_id_from_output_video_stem(mp4.stem)
        if not did:
            continue
        prefix = output_prefix_from_assembled_stem(mp4.stem) or ""
        if prefix.lower().startswith(ANCHOR_PREVIEW_OUTPUT_PREFIX):
            continue
        out.append((did, mp4))
    return out


def earlier_unuploaded_episodes(
    date_id: str,
    *,
    repo_root: Path | str,
    output_dir: Path | str | None = None,
) -> list[str]:
    """``date_id``s in the *current publishing window* that are assembled but not
    yet on YouTube.

    The window is bounded below by the highest already-uploaded episode earlier
    than ``date_id`` (the "high-water mark"): only assembled-but-unuploaded
    episodes strictly between that mark and ``date_id`` are returned. This catches
    a fresh gap (e.g. uploading 0707 while 0704-0706 sit un-uploaded) without
    dragging in ragged pre-convention manifests from the deep back catalogue. If
    nothing earlier has been uploaded at all, there is no baseline and the list is
    empty.
    """
    root = Path(repo_root)
    odir = Path(output_dir) if output_dir is not None else root / "output"
    episodes = _assembled_episodes(odir)

    uploaded_below = [
        did
        for did, mp4 in episodes
        if did < date_id and already_uploaded_video_id(video_manifest.manifest_path_for_video(mp4))
    ]
    if not uploaded_below:
        return []
    floor = max(uploaded_below)

    missing = {
        did
        for did, mp4 in episodes
        if floor < did < date_id
        and not already_uploaded_video_id(video_manifest.manifest_path_for_video(mp4))
    }
    return sorted(missing)


def check_upload_allowed(
    video_path: Path | str,
    manifest_path: Path | str | None,
    *,
    repo_root: Path | str,
    force: bool = False,
    allow_gap: bool = False,
) -> None:
    """Raise :class:`DuplicateUploadError` or :class:`UploadSequenceError` unless
    the matching override is set. No-op when both checks pass."""
    video_path = Path(video_path)
    date_id = date_id_from_output_video_stem(video_path.stem) or ""
    if not force:
        vid = already_uploaded_video_id(manifest_path)
        if vid:
            raise DuplicateUploadError(date_id, vid, manifest_path or video_path)
    if not allow_gap and date_id:
        missing = earlier_unuploaded_episodes(date_id, repo_root=repo_root)
        if missing:
            raise UploadSequenceError(date_id, missing)
