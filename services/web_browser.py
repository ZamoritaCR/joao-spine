"""JOAO web browsing service — fetch, screenshot, and navigate pages via Playwright."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

# playwright is imported lazily inside each function below so this module
# can be loaded on environments where playwright isn't installed (e.g.
# Railway without the browser layer). /browse endpoints will raise
# ModuleNotFoundError at call-time if that's the case, which is loud
# enough without blocking app startup.

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("/tmp/joao_browse_cache")
_CACHE_TTL_S = 300  # 5 minutes
_TIMEOUT_MS = 30_000
_USER_AGENT = "JOAO/1.0 (+https://joao.theartofthepossible.io)"


def _cache_key(url: str) -> Path:
    return _CACHE_DIR / hashlib.sha256(url.encode()).hexdigest()


def _read_cache(url: str) -> str | None:
    path = _cache_key(url)
    if path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_TTL_S:
        return path.read_text(encoding="utf-8")
    return None


def _write_cache(url: str, content: str) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_key(url).write_text(content, encoding="utf-8")


async def fetch_and_read(url: str) -> dict:
    """Fetch URL and return clean readable text via readability + bs4."""
    cached = _read_cache(url)
    if cached:
        import json
        return json.loads(cached)

    from playwright.async_api import async_playwright
    t0 = time.time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_USER_AGENT)
        page = await ctx.new_page()
        await page.goto(url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
        title = await page.title()
        html = await page.content()
        await browser.close()

    from readability import Document
    from bs4 import BeautifulSoup

    doc = Document(html)
    clean_html = doc.summary()
    soup = BeautifulSoup(clean_html, "html.parser")
    clean_text = soup.get_text(separator="\n", strip=True)

    # Fallback for list-heavy pages (e.g., Hacker News) where readability may return sparse text.
    if len(clean_text) < 1000:
        full_soup = BeautifulSoup(html, "html.parser")
        fallback = full_soup.get_text(separator="\n", strip=True)
        if len(fallback) > len(clean_text):
            clean_text = fallback

    result = {
        "title": title or doc.short_title(),
        "clean_text": clean_text,
        "html_len": len(html),
        "fetch_time_ms": int((time.time() - t0) * 1000),
    }

    import json
    _write_cache(url, json.dumps(result, ensure_ascii=False))
    return result


async def screenshot(url: str, full_page: bool = True) -> bytes:
    """Take a PNG screenshot of the URL."""
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 720},
        )
        page = await ctx.new_page()
        await page.goto(url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
        png = await page.screenshot(full_page=full_page)
        await browser.close()
    return png


async def navigate_and_extract(url: str, actions: list[dict]) -> dict:
    """Navigate to URL, execute a sequence of actions, return final page state.

    Supported actions:
        {"click": "selector"}
        {"fill": ["selector", "value"]}
        {"wait": milliseconds}
    """
    from playwright.async_api import async_playwright
    t0 = time.time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_USER_AGENT)
        page = await ctx.new_page()
        await page.goto(url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")

        action_log: list[str] = []
        for action in actions:
            if "click" in action:
                await page.click(action["click"], timeout=_TIMEOUT_MS)
                action_log.append(f"clicked {action['click']}")
            elif "fill" in action:
                sel, val = action["fill"]
                await page.fill(sel, val, timeout=_TIMEOUT_MS)
                action_log.append(f"filled {sel}")
            elif "wait" in action:
                await page.wait_for_timeout(int(action["wait"]))
                action_log.append(f"waited {action['wait']}ms")

        title = await page.title()
        html = await page.content()
        await browser.close()

    from readability import Document
    from bs4 import BeautifulSoup

    doc = Document(html)
    soup = BeautifulSoup(doc.summary(), "html.parser")
    clean_text = soup.get_text(separator="\n", strip=True)

    return {
        "title": title or doc.short_title(),
        "clean_text": clean_text,
        "html_len": len(html),
        "actions_executed": action_log,
        "fetch_time_ms": int((time.time() - t0) * 1000),
    }
