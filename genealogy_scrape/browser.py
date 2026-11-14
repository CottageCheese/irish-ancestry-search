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

# Optional hook so a UI can show countdowns during polite pauses.
# Signature: hook(host, seconds)
PAUSE_HOOK = None  # type: ignore[assignment]

# Optional hook returning True when the current run should abort.
CANCEL_CHECK = None  # type: ignore[assignment]


class CancelledError(Exception):
    """Raised when CANCEL_CHECK signals abort mid-run."""


def _check_cancel() -> None:
    if CANCEL_CHECK is not None:
        try:
            if CANCEL_CHECK():
                raise CancelledError("Search cancelled by user")
        except CancelledError:
            raise
        except Exception:
            pass


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
    if PAUSE_HOOK is not None:
        try:
            PAUSE_HOOK(host or "", seconds)
        except Exception:
            pass
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        _check_cancel()
        time.sleep(min(0.2, remaining))
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


# Common cookie-banner selectors and button-text patterns. First hit wins.
# Order: specific IDs > generic role/text. Case-insensitive text match via :has-text.
_COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#CybotCookiebotDialogBodyButtonAccept",
    "button#accept-cookies",
    "button.cookie-accept",
    "button[aria-label*='Accept' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Allow all')",
    "[role=button]:has-text('Accept')",
]


def dismiss_cookie_banner(page: Page, logger: logging.Logger) -> bool:
    """Best-effort cookie-banner dismissal. Tries a short list of common
    selectors; clicks the first visible one. Returns True if something was
    clicked. Silent on failure — missing banner is the happy path.
    """
    for sel in _COOKIE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                loc.click(timeout=1_500)
                logger.info("Dismissed cookie banner via %s", sel)
                try:
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


def _attempt_goto(page: Page, url: str, logger: logging.Logger) -> tuple[bool, bool]:
    """Single navigation attempt. Returns (ok, transient).
    `transient` means caller may retry (timeout or 5xx response).
    """
    try:
        response = page.goto(url, timeout=settings.PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        status = response.status if response is not None else None
        if status is not None and 500 <= status < 600:
            logger.error("load got HTTP %s", status)
            return False, True
        dismiss_cookie_banner(page, logger)
        return True, False
    except PlaywrightTimeoutError as exc:
        logger.error("load timeout: %s", exc)
        return False, True
    except Exception as exc:
        msg = str(exc).lower()
        if "interrupted by another navigation" in msg or "err_aborted" in msg:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=settings.PAGE_TIMEOUT_MS)
                dismiss_cookie_banner(page, logger)
                return True, False
            except Exception as inner:
                logger.error("post-redirect wait failed: %s", inner)
                return False, True
        logger.error("load failed: %s", exc)
        return False, False


def safe_goto(page: Page, url: str, logger: logging.Logger) -> bool:
    """Navigate to `url` tolerantly. Returns True if we ended up on some
    real page. A redirect that interrupts the first navigation is treated
    as success — we just wait for the follow-up load. Any visible cookie
    banner is auto-dismissed before returning so results selectors aren't
    occluded.

    Retries on transient failures (timeouts, HTTP 5xx) up to
    `settings.RETRY_ATTEMPTS` extra times with exponential backoff.
    """
    _check_cancel()
    total = 1 + max(0, settings.RETRY_ATTEMPTS)
    for attempt in range(1, total + 1):
        ok, transient = _attempt_goto(page, url, logger)
        if ok:
            return True
        if not transient or attempt == total:
            return False
        delay = settings.RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
        logger.warning("retrying load (%d/%d) after %.1fs: %s", attempt, total - 1, delay, url)
        time.sleep(delay)
    return False


def safe_text(page: Page, selector: str) -> str:
    try:
        return page.inner_text(selector, timeout=2_000).strip()
    except Exception:
        return ""
