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
from genealogy_core.render import slugify

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
    """Drop duplicate hits. Two passes:

    1. Exact: (person, site, record_id) when record_id parses, else
       (person, site, link, name) — catches same URL arriving under
       multiple surname/nickname variants.
    2. Semantic: (person, site, record_type, name_found, place,
       age_or_year) — collapses near-identical IG rows that each have
       distinct record_ids but describe the same event (e.g. 91 copies
       of a single birth index entry). Keeps highest-scoring instance.
    """
    seen_exact: set[tuple] = set()
    first_pass: list[Record] = []
    for r in records:
        m = RECORD_ID_RE.search(r.link or "")
        key = (r.person_searched, r.site, m.group(1) if m else (r.link, r.name_found))
        if key in seen_exact:
            continue
        seen_exact.add(key)
        first_pass.append(r)

    best: dict[tuple, int] = {}  # semantic key -> index in first_pass
    for i, r in enumerate(first_pass):
        skey = (
            r.person_searched,
            r.site,
            r.record_type,
            (r.name_found or "").strip().lower(),
            (r.place or "").strip().lower(),
            (r.age_or_year or "").strip().lower(),
        )
        prev = best.get(skey)
        if prev is None or first_pass[prev].score < r.score:
            best[skey] = i
    keep = set(best.values())
    return [r for i, r in enumerate(first_pass) if i in keep]


def _dump_debug_html(page: Page, site_key: str, person: Person,
                     logger: logging.Logger) -> None:
    """Save page.content() under results/debug/ when a scraper returned 0
    but the page loaded cleanly. Opt-in via settings.DEBUG_DUMP_HTML."""
    try:
        debug_dir = Path("results") / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        slug = slugify(person.label())
        out = debug_dir / f"{site_key}_{slug}.html"
        out.write_text(page.content(), encoding="utf-8")
        logger.info("[%s] dumped 0-result page to %s", site_key, out)
    except Exception as exc:
        logger.warning("[%s] debug dump failed: %s", site_key, exc)


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
SiteDoneCb = Callable[[str, int, int], None]  # (site_key, found_for_person, total_unique)


def run_search(playwright: Playwright, people: list[Person], headless: bool,
               logger: logging.Logger, skip_sites: set[str],
               cache: SearchCache, use_nicknames: bool,
               score_sort: bool, storage_path: Path | None,
               incremental_save: IncrementalSaver | None = None,
               progress: Callable[[int, int, str], None] | None = None,
               on_site_done: SiteDoneCb | None = None,
               hints_by_person: dict[str, dict] | None = None,
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
                progress(idx - 1, total, f"{person.label()} ({person.county})")
            logger.info("====== Searching for: %s (%s) ======", person.label(), person.county)
            for site_key, fn in SEARCH_FUNCTIONS:
                if site_key in skip_sites:
                    logger.info("[%s] skipped by --skip flag", site_key)
                    continue
                if site_state[site_key]["dead"]:
                    logger.info("[%s] skipped - marked dead after %d failures",
                                site_key, settings.MAX_CONSECUTIVE_FAILURES)
                    continue
                fails_before = site_state[site_key]["fails"]
                try:
                    found = fn(page, person, logger, site_state[site_key], cache, use_nicknames)
                    person_hints = (hints_by_person or {}).get(person.label())
                    annotate_records(person, found, hints=person_hints)
                    logger.info("[%s] %d records for %s", site_key, len(found), person.label())
                    all_records.extend(found)
                except Exception as exc:
                    logger.exception("[%s] failed for %s: %s", site_key, person.label(), exc)
                    found = []
                if (settings.DEBUG_DUMP_HTML and not found
                        and site_state[site_key]["fails"] == fails_before):
                    _dump_debug_html(page, site_key, person, logger)
                if on_site_done:
                    on_site_done(site_key, len(found), len(dedupe_records(all_records)))
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
