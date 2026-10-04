"""Base interface for AI video generation vendors."""

from abc import ABC, abstractmethod
from pathlib import Path


class VideoVendor(ABC):
    """Abstract base class for video generation vendors."""

    name: str = "base"

    @abstractmethod
    def generate(
        self,
        date_id: str,
        prompts: list[str],
        output_dir: Path,
        **kwargs,
    ) -> list[Path]:
        """
        Generate video(s) from prompts.

        Args:
            date_id: Date identifier (e.g. 18030830).
            prompts: List of video_prompt strings, one per narration segment.
            output_dir: Directory to save output MP4(s).
            **kwargs: Vendor-specific options (resolution, model, etc.).

        Returns:
            List of paths to generated MP4 files, one per prompt (or one combined for Sora).
        """
        ...
