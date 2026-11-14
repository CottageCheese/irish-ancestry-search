"""IrishGenealogy.ie scraper: civil (b/m/d) + church (baptism/marriage/burial).

parse_ig_item is pure — takes extracted summary + detail-bit strings and
returns a Record. Scrapers feed it Playwright-extracted strings; the
function itself is unit-testable without a browser.
"""
import logging
import re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from genealogy_core.models import Person, Record

from . import settings
from .browser import build_url, looks_like_captcha, polite_pause, safe_goto, wait_for_user_after_captcha
from .cache import SearchCache
from .sites import IG_SEARCH_MATRIX, SITES

IG_BASE = "https://www.irishgenealogy.ie"
_PLACE_KEYS = ("District", "Area", "Place", "Parish")
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")


def parse_ig_item(summary: str, detail_bits: list[str], href: str,
                  fallback_url: str, person: Person, forename: str,
                  surname: str, site_label: str, record_type: str) -> Record:
    """Build a Record from one IG result list item.

    summary is the h5 text ("John Doe 1872 Civil Birth ..."). detail_bits is
    the list of child-div strings that contain a ':' separator. href is the
    raw link attribute (may be relative or empty).
    """
    full_href = href or ""
    if full_href.startswith("/"):
        full_href = IG_BASE + full_href

    year_match = _YEAR_RE.search(summary or "")
    found_year = year_match.group(0) if year_match else ""

    place = person.county
    for bit in detail_bits:
        if any(key in bit for key in _PLACE_KEYS):
            place = bit.split(":", 1)[-1].strip()
            break

    name = summary.strip() if summary else f"{forename} {surname}".strip()

    return Record(
        person_searched=person.label(),
        site=site_label,
        record_type=record_type,
        name_found=name,
        age_or_year=found_year,
        place=place,
        other_details=" | ".join(detail_bits),
        link=full_href or fallback_url,
    )


def _event_window(label: str, year_window: tuple[int, int] | None,
                  mode: str) -> tuple[int, int]:
    """Translate a person's birth window into an event window. Marriages
    happen 18-60 years after birth; deaths 20-100. Births/baptisms match
    directly. When the person has no window, open the whole site range.
    """
    if year_window is None:
        return (1700 if mode == "church" else 1864), 1950
    base_from, base_to = year_window
    if "Marriage" in label:
        return base_from + 18, base_to + 60
    if "Death" in label or "Burial" in label:
        return base_from + 20, base_to + 100
    return base_from, base_to


def _search_ig_one(page: Page, person: Person, surname: str, forename: str,
                   mode: str, flag: str, label: str, cache: SearchCache,
                   state: dict, logger: logging.Logger) -> list[Record]:
    cfg = SITES["irishgenealogy_civil"]
    year_from, year_to = _event_window(label, person.year_window(), mode)

    cache_key = cache.key("ig", surname, forename, person.county, label, str(year_from), str(year_to))
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("[IG %s] cache hit: %s %s (%d records)", label, surname, forename, len(cached))
        return cached

    # IG has one free-text location field. Prefer townland if supplied,
    # fall back to county.
    url = build_url(
        cfg["search_url"],
        forename=forename,
        surname=surname,
        year_from=str(year_from),
        year_to=str(year_to),
        event_flag=flag,
        mode=mode,
        county=person.townland or person.county,
    )
    logger.info("[IG %s] %s %s -> %s", label, surname, forename, url)

    if not safe_goto(page, url, logger):
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] >= settings.MAX_CONSECUTIVE_FAILURES:
            state["dead"] = True
            logger.error("[IG] %d consecutive failures - skipping site for rest of run",
                         state["fails"])
        polite_pause(logger, "ig")
        return []
    state["fails"] = 0

    if looks_like_captcha(page, cfg["captcha_hint"]):
        wait_for_user_after_captcha(logger, page)

    try:
        page.wait_for_selector(cfg["results_container"], timeout=settings.PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning("[IG %s] #results container never appeared", label)
        polite_pause(logger, "ig")
        return []

    records: list[Record] = []
    items = page.query_selector_all(cfg["results_row"])
    logger.info("[IG %s] %d result items on page", label, len(items))

    for item in items[: settings.MAX_RESULTS_PER_SEARCH]:
        link_el = item.query_selector("a")
        href = link_el.get_attribute("href") if link_el else ""
        summary_el = item.query_selector("h5")
        summary = (summary_el.inner_text() if summary_el else "").strip()
        detail_bits: list[str] = []
        for div in item.query_selector_all("div"):
            text = (div.inner_text() or "").strip()
            if text and ":" in text:
                detail_bits.append(text)

        rec = parse_ig_item(summary, detail_bits, href, page.url,
                            person, forename, surname, cfg["name"], label)
        rec.name_used = f"{forename} {surname}".strip()
        records.append(rec)

    cache.put(cache_key, records)
    polite_pause(logger, "ig")
    return records


def search_irishgenealogy_civil(page: Page, person: Person, logger: logging.Logger,
                                state: dict, cache: SearchCache,
                                use_nicknames: bool) -> list[Record]:
    """Search IG across all six event matrices for every name variant."""
    records: list[Record] = []
    event_filter = settings.IG_EVENT_FILTER or ()
    matrix = [r for r in IG_SEARCH_MATRIX if not event_filter or r[1] in event_filter]
    for surname in person.all_surnames():
        for forename in person.all_forenames(use_nicknames):
            for mode, flag, label in matrix:
                if state.get("dead"):
                    return records
                records.extend(
                    _search_ig_one(page, person, surname, forename, mode, flag, label,
                                   cache, state, logger)
                )
    return records
