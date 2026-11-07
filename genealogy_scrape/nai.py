"""National Archives of Ireland census scraper (1901, 1911, 1926).

parse_nai_row is pure — takes a cell list and returns a Record. Scrapers
feed it Playwright-extracted cells but the function itself is unit-testable
without a browser.
"""
import logging

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from genealogy_core.models import Person, Record

from . import settings
from .browser import build_url, looks_like_captcha, polite_pause, safe_goto, wait_for_user_after_captcha
from .cache import SearchCache
from .sites import CENSUS_YEARS, NAI_LINK_BASE, NAI_READY_JS, SITES


def parse_nai_row(cells: list[str], href: str, fallback_url: str,
                  person: Person, year: str, site_label: str) -> Record:
    """Build a Record from one NAI results table row.

    Column order observed live: surname, firstname, census_year, age,
    townland, county, DED, occupation, sex, birthplace, ...
    """
    full_href = href
    if full_href and full_href.startswith("/"):
        full_href = NAI_LINK_BASE + full_href
    name = f"{cells[1]} {cells[0]}".strip() if len(cells) > 1 else (cells[0] if cells else "")
    age = cells[3] if len(cells) > 3 else year
    place = ", ".join(filter(None, cells[4:7])) if len(cells) > 4 else person.county
    other = " | ".join(filter(None, cells[7:])) if len(cells) > 7 else ""
    return Record(
        person_searched=person.label(),
        site=f"{site_label} ({year})",
        record_type=f"Census {year}",
        name_found=name,
        age_or_year=age,
        place=place,
        other_details=other,
        link=full_href or fallback_url,
    )


def scrape_nai_detail(page: Page, href: str, logger: logging.Logger) -> tuple[str, str]:
    """Click into a NAI record page. Returns (details, thumbnail_url).
    Empty strings on any load or parse failure.
    """
    if not href or not href.startswith("http"):
        return "", ""
    try:
        if not safe_goto(page, href, logger):
            return "", ""
        page.wait_for_function(NAI_READY_JS, timeout=settings.PAGE_TIMEOUT_MS)
    except Exception:
        return "", ""

    thumb = ""
    try:
        img = page.query_selector(
            "img[src*='scan'], img[src*='image'], img[src*='.jpg'], "
            "img[src*='.jpeg'], img[src*='.png']"
        )
        if img:
            src = img.get_attribute("src") or ""
            if src.startswith("/"):
                src = NAI_LINK_BASE + src
            if src.startswith("http"):
                thumb = src
    except Exception:
        pass

    bits: list[str] = []
    try:
        rows = page.query_selector_all("table tr")
        people_rows: list[str] = []
        for row in rows[1:15]:
            text = (row.inner_text() or "").strip()
            if text and len(text) < 300:
                people_rows.append(" ".join(text.split()))
        if people_rows:
            bits.append("Household: " + " ; ".join(people_rows[:10]))
    except Exception:
        pass

    try:
        labels = page.query_selector_all("dt, strong")
        for lab in labels[:30]:
            key = (lab.inner_text() or "").strip().rstrip(":")
            if key.lower() in {"religion", "occupation", "relation to head", "marital status", "birthplace"}:
                sib = lab.evaluate_handle("el => el.nextElementSibling || el.parentElement")
                try:
                    val = sib.evaluate("el => el ? (el.innerText || '').trim() : ''")
                except Exception:
                    val = ""
                if val:
                    bits.append(f"{key}: {val}")
    except Exception:
        pass

    return " | ".join(bits)[:800], thumb


def _search_nai_one(page: Page, person: Person, surname: str, forename: str,
                    year: str, cache: SearchCache, state: dict,
                    logger: logging.Logger, deep: bool) -> list[Record]:
    cfg = SITES["nai_census"]
    cache_key = cache.key("nai", surname, forename, person.county, year)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("[NAI %s] cache hit: %s %s (%d records)", year, surname, forename, len(cached))
        return cached

    template = cfg["search_url_1926"] if year == "1926" else cfg["search_url"]
    url = build_url(
        template,
        census_year=year,
        surname=surname,
        forename=forename,
        county=person.county,
        townland=person.townland,
        limit=str(settings.MAX_RESULTS_PER_SEARCH),
    )
    logger.info("[NAI %s] %s %s -> %s", year, surname, forename, url)

    if not safe_goto(page, url, logger):
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] >= settings.MAX_CONSECUTIVE_FAILURES:
            state["dead"] = True
            logger.error("[NAI] %d consecutive failures - skipping site for rest of run",
                         state["fails"])
        polite_pause(logger, "nai")
        return []
    state["fails"] = 0

    if looks_like_captcha(page, cfg["captcha_hint"]):
        wait_for_user_after_captcha(logger, page)

    try:
        page.wait_for_function(NAI_READY_JS, timeout=settings.PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.info("[NAI %s] page never resolved for %s %s", year, surname, forename)
        polite_pause(logger, "nai")
        return []

    records: list[Record] = []
    rows = page.query_selector_all(cfg["results_row"])
    detail_hrefs: list[tuple[int, str]] = []
    for row in rows[: settings.MAX_RESULTS_PER_SEARCH + 5]:
        tds = row.query_selector_all("td")
        if not tds:
            continue
        cells = [(td.inner_text() or "").strip() for td in tds]
        if not any(cells):
            continue
        link_el = row.query_selector("a[href]")
        href = link_el.get_attribute("href") if link_el else ""
        rec = parse_nai_row(cells, href, page.url, person, year, cfg["name"])
        records.append(rec)
        if deep and rec.link.startswith("http"):
            detail_hrefs.append((len(records) - 1, rec.link))

    logger.info("[NAI %s] parsed %d rows for %s %s", year, len(records), surname, forename)

    if deep and detail_hrefs:
        logger.info("[NAI %s] deep scrape: %d record pages", year, len(detail_hrefs))
        for idx, href in detail_hrefs[: settings.MAX_RESULTS_PER_SEARCH]:
            detail, thumb = scrape_nai_detail(page, href, logger)
            if detail:
                existing = records[idx].other_details
                records[idx].other_details = (existing + " || " + detail).strip(" |") if existing else detail
            if thumb:
                records[idx].thumbnail = thumb
            polite_pause(logger, "nai")

    cache.put(cache_key, records)
    polite_pause(logger, "nai")
    return records


def search_nai_census(page: Page, person: Person, logger: logging.Logger,
                      state: dict, cache: SearchCache, use_nicknames: bool,
                      deep: bool) -> list[Record]:
    """NAI census (1901, 1911, 1926). SPA that reads URL-fragment params."""
    records: list[Record] = []
    for surname in person.all_surnames():
        for forename in person.all_forenames(use_nicknames):
            for year in CENSUS_YEARS:
                if state.get("dead"):
                    return records
                records.extend(
                    _search_nai_one(page, person, surname, forename, year, cache, state, logger, deep)
                )
    return records
