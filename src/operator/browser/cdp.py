"""Chrome DevTools Protocol (CDP) manager for persistent headed browser sessions."""

from __future__ import annotations

import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

logger = logging.getLogger(__name__)

DEFAULT_CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
DEFAULT_CDP_PORT = 9333


class CDPBrowserManager:
    """Manages system Chrome process and Playwright CDP connection."""

    def __init__(
        self,
        cdp_port: int = DEFAULT_CDP_PORT,
        chrome_path: str = DEFAULT_CHROME_PATH,
        user_data_dir: Path | str | None = None,
        headless: bool = False,
    ) -> None:
        self.cdp_port = cdp_port
        self.chrome_path = os.environ.get("CHROME_PATH", chrome_path)
        self.user_data_dir = Path(
            user_data_dir or os.environ.get("CHROME_PROFILE_DIR", "runs/browser_profile")
        ).resolve()
        self.headless = headless
        self._process: subprocess.Popen | None = None
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def is_cdp_available(self) -> bool:
        """Check if CDP endpoint is responding."""
        try:
            url = f"http://127.0.0.1:{self.cdp_port}/json/version"
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def launch_chrome(self) -> None:
        """Launch Chrome process with remote debugging port."""
        if self.is_cdp_available():
            logger.info("CDP already reachable on port %d, skipping process launch", self.cdp_port)
            return

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.chrome_path,
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self.headless:
            cmd.append("--headless=new")
        cmd.append("about:blank")

        logger.info("Starting Chrome process on port %d...", self.cdp_port)
        self._process = subprocess.Popen(cmd)

        # Wait up to 10 seconds for CDP endpoint to become ready
        for _ in range(20):
            time.sleep(0.5)
            if self.is_cdp_available():
                logger.info("Chrome CDP is ready on port %d", self.cdp_port)
                return

        raise RuntimeError(f"Chrome process launched but CDP port {self.cdp_port} did not become ready.")

    def connect(self) -> tuple[Browser, BrowserContext, Page]:
        """Connect Playwright over CDP to the Chrome instance."""
        if not self.is_cdp_available():
            self.launch_chrome()

        if self._pw is None:
            self._pw = sync_playwright().start()

        endpoint = f"http://127.0.0.1:{self.cdp_port}"
        logger.info("Connecting Playwright over CDP to %s", endpoint)
        self._browser = self._pw.chromium.connect_over_cdp(endpoint)

        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = self._browser.new_context()

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

        return self._browser, self._context, self._page

    def get_page(self) -> Page:
        """Return the current attached Page, connecting if not already connected."""
        if self._page is None or self._page.is_closed():
            _, _, page = self.connect()
            return page
        return self._page

    def disconnect(self) -> None:
        """Disconnect Playwright from Chrome without terminating Chrome itself."""
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            logger.debug("Error during CDP disconnect: %s", e)
        finally:
            self._browser = None
            self._context = None
            self._page = None
            self._pw = None

    def close_all(self) -> None:
        """Disconnect Playwright and terminate the launched Chrome process if managed."""
        self.disconnect()
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                self._process.kill()
            self._process = None
