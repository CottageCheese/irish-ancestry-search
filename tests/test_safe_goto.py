"""safe_goto retry behaviour. No real browser; fakes Playwright Page."""
import logging
from unittest.mock import MagicMock

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from genealogy_scrape import browser, settings


def _logger() -> logging.Logger:
    log = logging.getLogger("test_safe_goto")
    log.addHandler(logging.NullHandler())
    return log


class _FakePage:
    def __init__(self, responses):
        # responses: list of either an int status code, or an Exception to raise
        self._responses = list(responses)
        self.calls = 0
        self.locator = MagicMock(return_value=MagicMock(
            first=MagicMock(is_visible=MagicMock(return_value=False))
        ))

    def goto(self, url, timeout=None, wait_until=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        resp = MagicMock()
        resp.status = item
        return resp

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **kw):
        pass


def test_success_first_try(monkeypatch):
    monkeypatch.setattr(settings, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RETRY_BACKOFF_BASE", 0.0)
    page = _FakePage([200])
    assert browser.safe_goto(page, "https://example.com", _logger()) is True
    assert page.calls == 1


def test_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RETRY_BACKOFF_BASE", 0.0)
    page = _FakePage([503, 200])
    assert browser.safe_goto(page, "https://example.com", _logger()) is True
    assert page.calls == 2


def test_retries_on_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RETRY_BACKOFF_BASE", 0.0)
    page = _FakePage([PlaywrightTimeoutError("boom"), 200])
    assert browser.safe_goto(page, "https://example.com", _logger()) is True
    assert page.calls == 2


def test_gives_up_after_retry_budget(monkeypatch):
    monkeypatch.setattr(settings, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RETRY_BACKOFF_BASE", 0.0)
    page = _FakePage([503, 503, 503])
    assert browser.safe_goto(page, "https://example.com", _logger()) is False
    assert page.calls == 3


def test_non_transient_failure_does_not_retry(monkeypatch):
    monkeypatch.setattr(settings, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "RETRY_BACKOFF_BASE", 0.0)
    page = _FakePage([RuntimeError("net::ERR_NAME_NOT_RESOLVED")])
    assert browser.safe_goto(page, "https://example.com", _logger()) is False
    assert page.calls == 1
