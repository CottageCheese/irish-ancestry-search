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
from . import settings
from .sites import CENSUS_YEARS, NAI_LINK_BASE, NAI_READY_JS, SITES


def parse_nai_household(rows: list[list[str]], subject_name: str) -> list[str]:
    """Build co-resident name list from a NAI detail page household table.

    Each row is cell strings ordered like the results table (surname first,
    firstname second). Rows matching the subject (case-insensitive forename
    + surname both present) are dropped. Duplicates collapsed, order kept.
    """
    subject_l = (subject_name or "").lower().strip()
    subject_parts = {p for p in subject_l.split() if p}
    out: list[str] = []
    seen: set[str] = set()
    for cells in rows:
        if not cells or len(cells) < 2:
            continue
        surname = (cells[0] or "").strip()
        forename = (cells[1] or "").strip()
        if not surname and not forename:
            continue
        name = f"{forename} {surname}".strip()
        name_l = name.lower()
        if not name_l or name_l in seen:
            continue
        name_parts = {p for p in name_l.split() if p}
        if subject_parts and subject_parts.issubset(name_parts):
            continue
        seen.add(name_l)
        out.append(name)
    return out


def fetch_nai_household(page: Page, detail_url: str, subject_name: str,
                        cache: SearchCache, logger: logging.Logger) -> list[str]:
    """Load a NAI census individual detail page and return co-resident names.

    Returns [] on load failure or when household table absent. Results
    cached under household|{detail_url} so repeat runs don't re-fetch.
    """
    cache_key = cache.key("nai_household", detail_url)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    cfg = SITES["nai_census"]
    if not safe_goto(page, detail_url, logger):
        logger.debug("[NAI household] failed to load %s", detail_url)
        return []

    rows_els = page.query_selector_all(cfg["household_table_row"])
    rows: list[list[str]] = []
    for row_el in rows_els:
        tds = row_el.query_selector_all("td")
        if not tds:
            continue
        cells = [(td.inner_text() or "").strip() for td in tds]
        if any(cells):
            rows.append(cells)

    names = parse_nai_household(rows, subject_name)
    logger.debug("[NAI household] %s → %d co-residents", detail_url, len(names))
    cache.put(cache_key, names)
    polite_pause(logger, "nai")
    return names


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


def _search_nai_one(page: Page, person: Person, surname: str, forename: str,
                    year: str, cache: SearchCache, state: dict,
                    logger: logging.Logger) -> list[Record]:
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
    raw_rows = page.query_selector_all(cfg["results_row"])
    hrefs: list[str] = []
    for row in raw_rows[: settings.MAX_RESULTS_PER_SEARCH + 5]:
        tds = row.query_selector_all("td")
        if not tds:
            continue
        cells = [(td.inner_text() or "").strip() for td in tds]
        if not any(cells):
            continue
        link_el = row.query_selector("a[href]")
        href = link_el.get_attribute("href") if link_el else ""
        rec = parse_nai_row(cells, href, page.url, person, year, cfg["name"])
        rec.name_used = f"{forename} {surname}".strip()
        records.append(rec)
        hrefs.append(rec.link)

    logger.info("[NAI %s] parsed %d rows for %s %s", year, len(records), surname, forename)

    # Household grouping: fetch co-residents for the first HOUSEHOLD_FETCH_MAX
    # records that have a real detail URL (not just the search page URL).
    budget = settings.HOUSEHOLD_FETCH_MAX
    if budget > 0:
        search_url_prefix = page.url.split("#")[0]
        fetched = 0
        for rec in records:
            if fetched >= budget:
                break
            detail = rec.link
            if not detail or detail.startswith(search_url_prefix):
                continue
            rec.household = fetch_nai_household(
                page, detail, rec.name_found, cache, logger
            )
            fetched += 1

    cache.put(cache_key, records)
    polite_pause(logger, "nai")
    return records


def search_nai_census(page: Page, person: Person, logger: logging.Logger,
                      state: dict, cache: SearchCache, use_nicknames: bool) -> list[Record]:
    """NAI census (1901, 1911, 1926). SPA that reads URL-fragment params."""
    records: list[Record] = []
    year_filter = (settings.CENSUS_YEAR_FILTER or "").strip()
    years = [year_filter] if year_filter in CENSUS_YEARS else CENSUS_YEARS
    for surname in person.all_surnames():
        for forename in person.all_forenames(use_nicknames):
            for year in years:
                if state.get("dead"):
                    return records
                records.extend(
                    _search_nai_one(page, person, surname, forename, year, cache, state, logger)
                )
    return records
