"""Browser helpers: URL building, navigation, captcha detection, pauses.

Everything that touches Playwright but is site-agnostic lives here.
"""
import logging
import random
import time
import urllib.parse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from . import settings

_LAST_HIT: dict[str, float] = {}


def encode(value: str) -> str:
    return urllib.parse.quote_plus((value or "").strip())


def build_url(template: str, **params: str) -> str:
    encoded = {k: encode(str(v)) for k, v in params.items()}
    return template.format(**encoded)


def polite_pause(logger: logging.Logger, host: str | None = None) -> None:
    """Random pause between searches. With `host`, only sleep when the gap
    since the last hit to *that* host is too short — different hosts no
    longer block each other.
    """
    now = time.monotonic()
    min_gap = settings.DELAY_MIN_SECONDS
    max_gap = settings.DELAY_MAX_SECONDS
    if host:
        last = _LAST_HIT.get(host)
        if last is not None:
            elapsed = now - last
            if elapsed >= max_gap:
                _LAST_HIT[host] = now
                return
            seconds = random.uniform(max(0.5, min_gap - elapsed), max(1.0, max_gap - elapsed))
        else:
            seconds = random.uniform(min_gap, max_gap)
    else:
        seconds = random.uniform(min_gap, max_gap)
    logger.info("Pausing %.1f seconds%s", seconds, f" (host={host})" if host else "")
    time.sleep(seconds)
    _LAST_HIT[host or "_global"] = time.monotonic()


def looks_like_captcha(page: Page, hint: str) -> bool:
    try:
        body_text = page.inner_text("body", timeout=2_000).lower()
    except Exception:
        return False
    markers = [hint.lower(), "are you a robot", "verify you are human", "unusual traffic"]
    return any(m in body_text for m in markers)


def wait_for_user_after_captcha(logger: logging.Logger, page: Page) -> None:
    logger.warning("Possible CAPTCHA or block page on %s", page.url)
    print("\n*** A CAPTCHA or block page appeared. ***")
    print(f"URL: {page.url}")
    print("Please solve it in the browser window, then press ENTER here to continue.")
    try:
        input()
    except EOFError:
        time.sleep(30)


def safe_goto(page: Page, url: str, logger: logging.Logger) -> bool:
    """Navigate to `url` tolerantly. Returns True if we ended up on some
    real page. A redirect that interrupts the first navigation is treated
    as success — we just wait for the follow-up load.
    """
    try:
        page.goto(url, timeout=settings.PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        return True
    except PlaywrightTimeoutError as exc:
        logger.error("load timeout: %s", exc)
        return False
    except Exception as exc:
        msg = str(exc).lower()
        if "interrupted by another navigation" in msg or "err_aborted" in msg:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=settings.PAGE_TIMEOUT_MS)
                return True
            except Exception as inner:
                logger.error("post-redirect wait failed: %s", inner)
                return False
        logger.error("load failed: %s", exc)
        return False


def safe_text(page: Page, selector: str) -> str:
    try:
        return page.inner_text(selector, timeout=2_000).strip()
    except Exception:
        return ""
