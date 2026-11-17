"""Background worker: drives the scraper run on a daemon thread."""
from __future__ import annotations

import logging
import re as _re
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

from genealogy_core import (
    Record,
    apply_confirmations,
    extract_hints,
    load_confirmations,
    load_records_csv,
    load_previous_keys,
    mark_new_records,
    record_key,
)
from genealogy_scrape import SearchCache, run_search as _run_search
from genealogy_scrape import browser as scrape_browser
from genealogy_search import (
    CACHE_FILE,
    LOG_FILE,
    RESULTS_CSV,
    RESULTS_HTML,
    RESULTS_JSON,
    RESULTS_SUMMARY,
    STORAGE_FILE,
    read_people,
    setup_logging,
    write_csv,
    write_html,
    write_json,
    write_summary,
)

from .state import (
    CONFIRMS_FILE,
    CONFIRMS_LOCK,
    HERE,
    RUN,
    RUN_LOCK,
    UPLOAD_CSV,
)


class _CancelledError(Exception):
    pass


def _progress(current: int, total: int, label: str) -> None:
    with RUN_LOCK:
        if RUN.get("cancel_requested"):
            raise _CancelledError("Search cancelled by user")
        RUN["current"] = current
        RUN["total"] = total
        RUN["label"] = label


def _site_done(site_key: str, found: int, total_unique: int) -> None:
    with RUN_LOCK:
        RUN["last_site"] = f"{site_key} (+{found})"
        RUN["live_records"] = total_unique


def _pause(host: str, seconds: float) -> None:
    with RUN_LOCK:
        RUN["pause_host"] = host
        RUN["pause_seconds"] = seconds
        RUN["pause_until"] = time.time() + seconds


_STEP_RE = _re.compile(r"\[(NAI \d+|IG [^\]]+|Griff[^\]]*)\]")
_QUERY_RE = _re.compile(r"(?:cache hit: |parsed \d+ rows for |-> https?://\S+.*?surname__icontains=|for )([A-Za-z][A-Za-z'\- ]+?)(?:\s+->|\s*$| \()")


class _StepHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        m = _STEP_RE.search(msg)
        tag = m.group(1) if m else None
        q = None
        qm = _re.search(r"(?:cache hit:|parsed \d+ rows for) ([A-Za-z][A-Za-z\- ]+?)(?:\s*\(|$)", msg)
        if qm:
            q = qm.group(1).strip()
        else:
            qm2 = _re.search(r"\]\s+([A-Z][A-Za-z\-]+ [A-Za-z\-]+)\s+->", msg)
            if qm2:
                q = qm2.group(1).strip()
        if tag is None and q is None:
            return
        with RUN_LOCK:
            if tag:
                RUN["step"] = tag
            if q:
                RUN["current_query"] = q


def _build_hints() -> dict[str, dict]:
    """Load previous results + confirmations → per-person hints for scoring."""
    with CONFIRMS_LOCK:
        confs = load_confirmations(CONFIRMS_FILE)
    if not confs:
        return {}
    prev = load_records_csv(HERE / RESULTS_CSV)
    confirmed = [r for r in prev
                 if confs.get(record_key(r), {}).get("status") == "confirmed"]
    rejected = [r for r in prev
                if confs.get(record_key(r), {}).get("status") == "rejected"]
    return extract_hints(confirmed, rejected)


def _worker(opts: dict) -> None:
    logger = setup_logging(HERE / LOG_FILE)
    scrape_browser.PAUSE_HOOK = _pause
    scrape_browser.CANCEL_CHECK = lambda: RUN.get("cancel_requested", False)
    sh = _StepHandler()
    sh.setLevel(logging.INFO)
    logging.getLogger("genealogy").addHandler(sh)
    try:
        people = read_people(UPLOAD_CSV, logger=logger)
        if not people:
            raise RuntimeError(f"No people in {UPLOAD_CSV.name}")
        if not opts.get("use_surnames"):
            for p in people:
                p.surname_variants = []

        only_list = [s.strip() for s in opts["only"].split(",") if s.strip()]
        if only_list:
            people = [p for p in people if p.label() in only_list]
            if not people:
                raise RuntimeError("Nobody left after --only filter")

        sur_picks = opts.get("surname_picks") or []
        fore_picks = opts.get("forename_picks") or []
        if len(people) == 1 and (sur_picks or fore_picks):
            p = people[0]
            if sur_picks:
                p.surname_variants = [s for s in sur_picks if s.lower() != p.surname.lower()]
            if fore_picks:
                p.forename_variants = list(fore_picks)

        skip_sites = {s.strip() for s in opts["skip"].split(",") if s.strip()}
        with RUN_LOCK:
            RUN["total"] = len(people)
            if not RUN.get("label") and people:
                RUN["label"] = people[0].label()
        cache = SearchCache(HERE / CACHE_FILE, enabled=not opts["no_cache"])

        results_csv_path = HERE / RESULTS_CSV
        prev_keys = load_previous_keys(results_csv_path) if results_csv_path.exists() else set()

        def _save(snapshot: list[Record]) -> None:
            write_csv(snapshot, results_csv_path)
            write_html(snapshot, HERE / RESULTS_HTML)

        storage_path = HERE / STORAGE_FILE
        hints_by_person = _build_hints()
        if hints_by_person:
            logger.info("Loaded feedback hints for %d person(s): %s",
                        len(hints_by_person), ", ".join(hints_by_person.keys()))

        with sync_playwright() as pw:
            records = _run_search(
                pw, people, opts["headless"], logger, skip_sites,
                cache, opts.get("use_nicks", False), True, storage_path,
                incremental_save=_save,
                progress=_progress,
                on_site_done=_site_done,
                hints_by_person=hints_by_person,
            )

        new_count = mark_new_records(records, prev_keys)
        with CONFIRMS_LOCK:
            confs = load_confirmations(CONFIRMS_FILE)
        apply_confirmations(records, confs)
        write_csv(records, results_csv_path)
        write_html(records, HERE / RESULTS_HTML)
        write_json(records, HERE / RESULTS_JSON)
        write_summary(records, people, HERE / RESULTS_SUMMARY,
                      new_count, cache.hits, cache.misses)
        cache.save()

        with RUN_LOCK:
            RUN["state"] = "done"
            RUN["record_count"] = len(records)
            RUN["new_count"] = new_count
            RUN["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except (_CancelledError, scrape_browser.CancelledError):
        try:
            partial = load_records_csv(results_csv_path) if results_csv_path.exists() else []
            new_count = mark_new_records(partial, prev_keys)
            with CONFIRMS_LOCK:
                confs = load_confirmations(CONFIRMS_FILE)
            apply_confirmations(partial, confs)
            write_json(partial, HERE / RESULTS_JSON)
            write_summary(partial, people, HERE / RESULTS_SUMMARY, new_count, cache.hits, cache.misses)
            cache.save()
            with RUN_LOCK:
                RUN["state"] = "done"
                RUN["stopped"] = True
                RUN["record_count"] = len(partial)
                RUN["new_count"] = new_count
                RUN["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            with RUN_LOCK:
                RUN["state"] = "error"
                RUN["error"] = "Search stopped."
                RUN["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        logging.getLogger("genealogy").exception("Web run failed: %s", exc)
        with RUN_LOCK:
            RUN["state"] = "error"
            RUN["error"] = str(exc)
            RUN["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
