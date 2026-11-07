"""Top-level orchestration: run every scraper for every person, dedupe,
incremental save, graceful per-site failure.

The incremental-save callback is injected so the renderer stays in
genealogy_core and this module has no HTML dependency.
"""
import logging
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, Page, Playwright

from genealogy_core.diff import RECORD_ID_RE
from genealogy_core.kin import annotate_records
from genealogy_core.models import Person, Record

from . import settings
from .cache import SearchCache
from .griffiths import search_griffiths
from .ig import search_irishgenealogy_civil
from .nai import search_nai_census

SearchFn = Callable[[Page, Person, logging.Logger, dict, SearchCache, bool, bool], list[Record]]

SEARCH_FUNCTIONS: list[tuple[str, SearchFn]] = [
    ("nai_census", search_nai_census),
    ("irishgenealogy_civil", search_irishgenealogy_civil),
    ("griffiths", search_griffiths),
]


def dedupe_records(records: list[Record]) -> list[Record]:
    """Drop duplicate hits. Same ancestor often arrives under multiple
    surname spellings or nickname variants. Key on (person, site,
    record_id) when a record_id parses out of the link, else fall back
    to (link, name).
    """
    seen: set[tuple] = set()
    out: list[Record] = []
    for r in records:
        m = RECORD_ID_RE.search(r.link or "")
        key = (r.person_searched, r.site, m.group(1) if m else (r.link, r.name_found))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def sort_by_score(records: list[Record]) -> list[Record]:
    """Per-person descending score sort, stable on original order. Output
    remains grouped by person_searched.
    """
    by_person: dict[str, list[Record]] = {}
    person_order: list[str] = []
    for r in records:
        if r.person_searched not in by_person:
            person_order.append(r.person_searched)
            by_person[r.person_searched] = []
        by_person[r.person_searched].append(r)
    out: list[Record] = []
    for p in person_order:
        out.extend(sorted(by_person[p], key=lambda r: r.score, reverse=True))
    return out


IncrementalSaver = Callable[[list[Record]], None]


def run_search(playwright: Playwright, people: list[Person], headless: bool,
               logger: logging.Logger, skip_sites: set[str],
               cache: SearchCache, use_nicknames: bool, deep: bool,
               score_sort: bool, storage_path: Path | None,
               incremental_save: IncrementalSaver | None = None,
               progress: Callable[[int, int, str], None] | None = None,
               ) -> list[Record]:
    browser: Browser = playwright.chromium.launch(headless=headless)
    ctx_kwargs: dict = {"user_agent": settings.USER_AGENT}
    if storage_path and storage_path.exists():
        ctx_kwargs["storage_state"] = str(storage_path)
        logger.info("Reusing session cookies from %s", storage_path.name)
    context = browser.new_context(**ctx_kwargs)
    context.set_default_timeout(settings.PAGE_TIMEOUT_MS)
    page = context.new_page()

    all_records: list[Record] = []
    site_state: dict[str, dict] = {k: {"fails": 0, "dead": False} for k, _ in SEARCH_FUNCTIONS}

    try:
        total = len(people)
        for idx, person in enumerate(people, start=1):
            if progress:
                progress(idx, total, f"{person.label()} ({person.county})")
            logger.info("====== Searching for: %s (%s) ======", person.label(), person.county)
            for site_key, fn in SEARCH_FUNCTIONS:
                if site_key in skip_sites:
                    logger.info("[%s] skipped by --skip flag", site_key)
                    continue
                if site_state[site_key]["dead"]:
                    logger.info("[%s] skipped - marked dead after %d failures",
                                site_key, settings.MAX_CONSECUTIVE_FAILURES)
                    continue
                try:
                    found = fn(page, person, logger, site_state[site_key], cache, use_nicknames, deep)
                    annotate_records(person, found)
                    logger.info("[%s] %d records for %s", site_key, len(found), person.label())
                    all_records.extend(found)
                except Exception as exc:
                    logger.exception("[%s] failed for %s: %s", site_key, person.label(), exc)
            snapshot = dedupe_records(all_records)
            if score_sort:
                snapshot = sort_by_score(snapshot)
            if incremental_save:
                incremental_save(snapshot)
            cache.save()
            logger.info("Incremental save: %d unique records so far", len(snapshot))
    finally:
        if storage_path:
            try:
                context.storage_state(path=str(storage_path))
                logger.info("Saved session cookies to %s", storage_path.name)
            except Exception as exc:
                logger.warning("Could not save session cookies: %s", exc)
        context.close()
        browser.close()
    final = dedupe_records(all_records)
    return sort_by_score(final) if score_sort else final
