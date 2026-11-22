"""Постоянная видимая сессия Chromium для Энергика (Playwright).

Браузер запускается только при первом обращении к сети и живёт до завершения программы.
Профиль хранится отдельно в browser_profile/, поэтому cookies и вкладка сохраняются между репликами.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse


class BrowserError(RuntimeError):
    pass


class BrowserSession:
    def __init__(self, cfg: dict, root: Path):
        section = cfg.get("browser", {})
        self.enabled = bool(section.get("enabled", True))
        self.headless = bool(section.get("headless", False))
        self.timeout_ms = int(section.get("timeout_ms", 15000))
        self.max_chars = int(section.get("max_chars", 7000))
        self.profile_dir = Path(root) / section.get("profile_dir", "browser_profile")
        self._playwright = None
        self._context = None
        self._page = None

    @property
    def is_open(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    @property
    def url(self) -> str:
        return self._page.url if self.is_open else ""

    @property
    def title(self) -> str:
        if not self.is_open:
            return ""
        try:
            return self._page.title()
        except Exception:
            return ""

    def _ensure(self):
        if not self.enabled:
            raise BrowserError("браузер отключён в config.toml")
        if self.is_open:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserError("не установлен Playwright. Выполни: pip install playwright, затем playwright install chromium") from e
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1365, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._context.set_default_timeout(self.timeout_ms)
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
        except Exception as e:
            self.close()
            detail = str(e)
            if "Executable doesn't exist" in detail or "Please run" in detail:
                raise BrowserError("Chromium для Playwright не установлен. Выполни: python -m playwright install chromium") from e
            raise BrowserError(f"не удалось запустить Chromium: {detail[:220]}") from e

    @staticmethod
    def _validate_url(url: str) -> str:
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise BrowserError("разрешены только полные HTTP/HTTPS-адреса")
        parsed = urlparse(url)
        if not parsed.hostname:
            raise BrowserError("в адресе нет имени сайта")
        return url

    def _extract(self) -> str:
        self._ensure()
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        except Exception:
            pass
        try:
            self._page.locator("body").wait_for(timeout=5000)
            text = self._page.locator("body").inner_text(timeout=5000)
        except Exception as e:
            raise BrowserError(f"не удалось прочитать страницу: {str(e)[:160]}") from e
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:self.max_chars]

    def open(self, url: str) -> tuple[str | None, str | None]:
        try:
            self._ensure()
            self._page.goto(self._validate_url(url), wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                self._page.wait_for_load_state("networkidle", timeout=3500)
            except Exception:
                pass
            text = self._extract()
            if len(text) < 80:
                return None, "страница открылась, но на ней почти нет доступного текста"
            return text, None
        except Exception as e:
            return None, str(e)[:220] or type(e).__name__

    def read_current(self) -> tuple[str | None, str | None]:
        if not self.is_open:
            return None, "нет открытой страницы"
        try:
            return self._extract(), None
        except Exception as e:
            return None, str(e)[:180]

    def click_link(self, text: str) -> tuple[str | None, str | None]:
        """Переходит только по ссылке с видимым текстом; не нажимает кнопки отправки/покупки."""
        if not text.strip():
            return None, "не указано название ссылки"
        try:
            self._ensure()
            links = self._page.locator("a:visible").filter(has_text=re.compile(re.escape(text.strip()), re.I))
            if links.count() == 0:
                return None, f"ссылка «{text[:70]}» на текущей странице не найдена"
            href = links.first.get_attribute("href")
            if not href:
                return None, "у ссылки нет адреса"
            from urllib.parse import urljoin
            target = urljoin(self._page.url, href)
            return self.open(target)
        except Exception as e:
            return None, str(e)[:180]

    def scroll(self, direction: str = "down") -> tuple[str | None, str | None]:
        if not self.is_open:
            return None, "нет открытой страницы"
        try:
            self._page.evaluate("direction => window.scrollBy(0, Math.max(500, window.innerHeight * 0.8) * (direction === 'up' ? -1 : 1))", direction)
            return self._extract(), None
        except Exception as e:
            return None, str(e)[:180]

    def back(self) -> tuple[str | None, str | None]:
        if not self.is_open:
            return None, "нет открытой страницы"
        try:
            self._page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
            return self._extract(), None
        except Exception as e:
            return None, str(e)[:180]

    def close(self):
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        self._page = self._context = None
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
