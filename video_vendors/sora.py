"""Sora (ChatGPT) video vendor — uses Playwright to drive web UI."""

from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from pipeline_logging import log_api_call_with_bodies, log_file_created


class SoraVendor:
    """Generates one combined video via Sora web UI (requires browser login)."""

    name = "sora"

    def __init__(self, profile_dir: str = "sora-profile", headless: bool = False):
        self.profile_dir = profile_dir
        self.headless = headless

    def generate(
        self,
        date_id: str,
        prompts: list[str],
        output_dir: Path,
        **kwargs,
    ) -> list[Path]:
        """Generate a single combined video via Sora; saves to output_dir."""
        combined = "\n\n".join(prompts)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=kwargs.get("profile_dir", self.profile_dir),
                headless=kwargs.get("headless", self.headless),
                args=["--start-maximized"],
            )
            page = context.new_page()
            page.goto("https://sora.chatgpt.com/explore/videos")

            if page.locator("button:has-text('Sign in with Google')").is_visible():
                print("▶️ Please complete Google login in the opened window…")
                page.click("button:has-text('Sign in with Google')")
                context.wait_for_event("page")
                page.wait_for_url("**/dashboard", timeout=180000)

            # Fill form & submit
            page.fill("textarea#videoPrompt", combined)
            page.select_option("select#voice", "british_rhotic_male")
            page.select_option("select#resolution", "1280x720")
            page.click("button#createVideo")

            # Wait for download link
            page.wait_for_selector("a#downloadLink", timeout=120000)
            download_url = page.get_attribute("a#downloadLink", "href")
            context.close()

        # Download and save
        resp = requests.get(download_url, stream=True)
        dl_bytes = 0
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"video_{date_id}.mp4"
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(16_384):
                f.write(chunk)
                dl_bytes += len(chunk)
        log_api_call_with_bodies(
            "sora",
            "requests.get",
            request_body={"url": download_url},
            response_body={
                "status_code": resp.status_code,
                "bytes_written": dl_bytes,
                "path": str(out_path),
            },
            extra={"date_id": date_id, "source": "sora_download"},
        )
        log_file_created(out_path, out_path.stat().st_size)
        print(f"[OK] Downloaded Sora video to {out_path}")
        return [out_path]
