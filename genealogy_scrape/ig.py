"""IrishGenealogy.ie scraper: civil (b/m/d) + church (baptism/marriage/burial)."""
import logging
import re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from genealogy_core.models import Person, Record

from . import settings
from .browser import build_url, looks_like_captcha, polite_pause, safe_goto, wait_for_user_after_captcha
from .cache import SearchCache
from .sites import IG_SEARCH_MATRIX, SITES


def scrape_ig_detail(page: Page, href: str, logger: logging.Logger) -> str:
    """Click into an IG record page. Returns terse field summary or ''."""
    if not href or not href.startswith("http"):
        return ""
    try:
        if not safe_goto(page, href, logger):
            return ""
        page.wait_for_selector("body", timeout=settings.PAGE_TIMEOUT_MS)
    except Exception:
        return ""

    wanted = {
        "father", "father's name",
        "mother", "mother's name", "mother's maiden name", "mother's maiden surname",
        "spouse", "spouse's name",
        "witness", "witnesses",
        "townland", "parish", "address",
        "occupation", "age",
        "informant",
    }

    bits: list[str] = []
    try:
        labels = page.query_selector_all("dt, strong, label, th")
        for lab in labels[:80]:
            key = (lab.inner_text() or "").strip().rstrip(":")
            if key.lower() not in wanted:
                continue
            sib = lab.evaluate_handle("el => el.nextElementSibling || el.parentElement")
            try:
                val = sib.evaluate("el => el ? (el.innerText || '').trim() : ''")
            except Exception:
                val = ""
            if val and len(val) < 200:
                bits.append(f"{key}: {' '.join(val.split())}")
    except Exception:
        pass

    return " | ".join(bits)[:800]


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
                   state: dict, logger: logging.Logger, deep: bool) -> list[Record]:
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

    detail_hrefs: list[tuple[int, str]] = []
    for item in items[: settings.MAX_RESULTS_PER_SEARCH]:
        link_el = item.query_selector("a")
        href = link_el.get_attribute("href") if link_el else ""
        if href.startswith("/"):
            href = "https://www.irishgenealogy.ie" + href
        summary_el = item.query_selector("h5")
        summary = (summary_el.inner_text() if summary_el else "").strip()
        detail_bits: list[str] = []
        for div in item.query_selector_all("div"):
            text = (div.inner_text() or "").strip()
            if text and ":" in text:
                detail_bits.append(text)

        year_match = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", summary)
        found_year = year_match.group(0) if year_match else ""

        place = person.county
        for bit in detail_bits:
            if any(key in bit for key in ("District", "Area", "Place", "Parish")):
                place = bit.split(":", 1)[-1].strip()
                break

        records.append(
            Record(
                person_searched=person.label(),
                site=cfg["name"],
                record_type=label,
                name_found=summary or f"{forename} {surname}".strip(),
                age_or_year=found_year,
                place=place,
                other_details=" | ".join(detail_bits),
                link=href or page.url,
            )
        )
        if deep and href and href.startswith("http"):
            detail_hrefs.append((len(records) - 1, href))

    if deep and detail_hrefs:
        logger.info("[IG %s] deep scrape: %d record pages", label, len(detail_hrefs))
        for idx, href in detail_hrefs:
            detail = scrape_ig_detail(page, href, logger)
            if detail:
                existing = records[idx].other_details
                records[idx].other_details = (existing + " || " + detail).strip(" |") if existing else detail
            polite_pause(logger, "ig")

    cache.put(cache_key, records)
    polite_pause(logger, "ig")
    return records


def search_irishgenealogy_civil(page: Page, person: Person, logger: logging.Logger,
                                state: dict, cache: SearchCache,
                                use_nicknames: bool, deep: bool) -> list[Record]:
    """Search IG across all six event matrices for every name variant."""
    records: list[Record] = []
    for surname in person.all_surnames():
        for forename in person.all_forenames(use_nicknames):
            for mode, flag, label in IG_SEARCH_MATRIX:
                if state.get("dead"):
                    return records
                records.extend(
                    _search_ig_one(page, person, surname, forename, mode, flag, label,
                                   cache, state, logger, deep)
                )
    return records
