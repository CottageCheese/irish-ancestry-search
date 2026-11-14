"""
genealogy_search.py
-------------------
Beginner-friendly script that searches three free Irish genealogy websites
for the ancestors listed in `ancestors.csv` and saves everything it finds
to `results.csv`, `results.html` and `run.log`.

Sites searched:
  1. National Archives of Ireland census
       1901, 1911 and 1926: full automated search
       https://nationalarchives.ie/collections/search-the-census/
  2. IrishGenealogy.ie civil + church records
       https://www.irishgenealogy.ie
  3. Griffith's Valuation on Ask About Ireland
       Manual link only (Cloudflare blocks automated access)
       https://www.askaboutireland.ie/griffith-valuation/

Run:
    python genealogy_search.py                 # headed (visible browser)
    python genealogy_search.py --headless      # no browser window
    python genealogy_search.py --input my.csv  # a different input file
    python genealogy_search.py --no-cache      # ignore .search_cache.json
    python genealogy_search.py --no-nicknames  # skip forename nickname expansion
    python genealogy_search.py --no-score      # don't sort hits by relevance score
    python genealogy_search.py --no-soundex    # skip Soundex surname variants
    python genealogy_search.py --no-diff       # don't flag new vs previous run

ancestors.csv columns:
    surname, forename, surname_variants, birth_year_approx, year_range,
    county, townland, father, mother, spouse, notes
(only surname + forename are required; the rest sharpen matches)

If a site changes its layout, edit the SITES dictionary near the top of
this file - that is where every URL and CSS selector lives.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from genealogy_core import (
    RUNTIME_FLAGS,
    Person,
    Record,
    apply_confirmations,
    confidence_tier,
    annotate_records,
    extract_hints,
    load_confirmations,
    load_previous_keys,
    load_records_csv,
    mark_new_records,
    record_key,
    render_results_html,
    render_person_page,
    render_index,
    slugify,
)
from genealogy_scrape import SearchCache, run_search as _run_search
from genealogy_scrape import settings as scrape_settings

# Preserve old module-level alias so existing callers (and --max-results)
# keep working. scrape_settings.MAX_RESULTS_PER_SEARCH is the source of truth.
MAX_RESULTS_PER_SEARCH = scrape_settings.MAX_RESULTS_PER_SEARCH

# ---------------------------------------------------------------------------
# CONFIG - edit this block when a site changes
# ---------------------------------------------------------------------------
# Every selector, URL template and timing lives here so a non-programmer
# can tweak things without touching the search logic below.
# ---------------------------------------------------------------------------

# Scraper knobs (timings, timeouts, user agent, SITES config, CENSUS_YEARS,
# IG_SEARCH_MATRIX) all live in genealogy_scrape.settings / .sites and are
# imported above. Edit site selectors or URL templates there.


# The 32 counties of the island of Ireland (both jurisdictions). Used only
# to warn the user if an input value looks typo'd - we do not reject the row,
# because NAI accepts free-text and some pre-partition records use old names.
IRISH_COUNTIES = {
    "antrim", "armagh", "carlow", "cavan", "clare", "cork", "derry",
    "londonderry", "donegal", "down", "dublin", "fermanagh", "galway",
    "kerry", "kildare", "kilkenny", "laois", "queen's", "leitrim",
    "limerick", "longford", "louth", "mayo", "meath", "monaghan", "offaly",
    "king's", "roscommon", "sligo", "tipperary", "tyrone", "waterford",
    "westmeath", "wexford", "wicklow",
}


# soundex / expand_surnames moved to genealogy_core.soundex.
# RUNTIME_FLAGS moved to genealogy_core.config (imported above).


# Output files.
RESULTS_CSV = "results.csv"
RESULTS_HTML = "results.html"
RESULTS_JSON = "results.json"
RESULTS_GEDCOM = "results.ged"
RESULTS_SUMMARY = "run.summary.md"
RESULTS_PEOPLE_DIR = "results"
LOG_FILE = "run.log"
CACHE_FILE = ".search_cache.json"
STORAGE_FILE = ".storage_state.json"
CONFIRMS_FILE = "confirmations.json"

# ---------------------------------------------------------------------------
# End of CONFIG - normal users do not need to edit below this line
# ---------------------------------------------------------------------------


# Person, Record, score_record, kin_cross_check, annotate_records,
# confidence_tier all live in genealogy_core and are imported at the top.


def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("genealogy")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

    file_h = logging.FileHandler(log_path, encoding="utf-8")
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)

    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(fmt)
    logger.addHandler(console_h)

    return logger


def read_people(path: Path, logger: logging.Logger | None = None) -> list[Person]:
    """
    Read ancestors.csv and return a list of Person objects.

    Validates lightly and warns (does not reject) on:
      - blank surname AND forename  -> row skipped
      - birth_year_approx that isn't a 4-digit year
      - county that doesn't look like any of Ireland's 32
      - year_range that isn't a non-negative integer (0 = exact year)
    """
    def warn(msg: str) -> None:
        if logger:
            logger.warning(msg)
        else:
            print("WARNING:", msg)

    people: list[Person] = []
    skipped = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # start=2: row 1 is header
            surname = (row.get("surname") or "").strip()
            forename = (row.get("forename") or "").strip()
            if not surname and not forename:
                skipped += 1
                continue

            variants_raw = (row.get("surname_variants") or "").strip()
            variants = [v.strip() for v in variants_raw.split(";") if v.strip()]

            t_variants_raw = (row.get("townland_variants") or "").strip()
            t_variants = [v.strip() for v in t_variants_raw.split(";") if v.strip()]

            year_raw = (row.get("birth_year_approx") or "").strip()
            if year_raw and not (year_raw.isdigit() and 1500 <= int(year_raw) <= 2000):
                warn(f"row {i} ({surname},{forename}): birth_year_approx={year_raw!r} "
                     "is not a plausible 4-digit year; ignoring")
                birth_year = None
            else:
                birth_year = int(year_raw) if year_raw.isdigit() else None

            range_raw = (row.get("year_range") or "").strip()
            if range_raw and not (range_raw.isdigit() and 0 <= int(range_raw) <= 50):
                warn(f"row {i} ({surname},{forename}): year_range={range_raw!r} "
                     "is not an integer 0-50; using default 5")
                year_range = 5
            else:
                year_range = int(range_raw) if range_raw.isdigit() else 5

            county = (row.get("county") or "").strip()
            if county and county.lower() not in IRISH_COUNTIES:
                warn(f"row {i} ({surname},{forename}): county={county!r} does not match "
                     "a known Irish county; passing through anyway")

            people.append(
                Person(
                    surname=surname,
                    forename=forename,
                    surname_variants=variants,
                    birth_year_approx=birth_year,
                    year_range=year_range,
                    county=county,
                    townland=(row.get("townland") or "").strip(),
                    townland_variants=t_variants,
                    father=(row.get("father") or "").strip(),
                    mother=(row.get("mother") or "").strip(),
                    spouse=(row.get("spouse") or "").strip(),
                    notes=(row.get("notes") or "").strip(),
                )
            )
    if skipped and logger:
        logger.info("Skipped %d blank row(s) in %s", skipped, path.name)
    return people


# SearchCache moved to genealogy_scrape.cache (imported above).


def _set_terminal_title(text: str) -> None:
    """Emit OSC 0 escape so window/taskbar title reflects progress.
    Works in Windows Terminal, most Linux terminals, and macOS Terminal.
    Silently no-ops when stderr isn't a tty.
    """
    if not sys.stderr.isatty():
        return
    try:
        sys.stderr.write(f"\033]0;{text}\007")
        sys.stderr.flush()
    except Exception:
        pass


def print_progress(current: int, total: int, label: str) -> None:
    """
    One-line stderr progress bar. No external dep (tqdm optional, used only
    if present). Keeps output readable when stdout is also receiving the
    per-search log lines. Also updates the terminal title so progress stays
    visible when the window is minimised or scrolled.
    """
    _set_terminal_title(f"genealogy [{current}/{total}] {label[:60]}")
    try:
        from tqdm import tqdm  # noqa: F401
        # tqdm present: emit via stderr so it overwrites cleanly.
        print(f"[{current}/{total}] {label}", file=sys.stderr)
        return
    except Exception:
        pass
    width = 24
    done = int(width * current / total) if total else 0
    bar = "#" * done + "-" * (width - done)
    print(f"[{bar}] {current}/{total}  {label[:60]}", file=sys.stderr)


# Scrapers, orchestration, dedupe, sort moved to genealogy_scrape package.


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


# confidence_tier, load_previous_keys, mark_new_records moved to
# genealogy_core.models / genealogy_core.diff and imported at the top.


def write_csv(records: list[Record], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "person_searched",
                "site",
                "record_type",
                "name_found",
                "age_or_year",
                "place",
                "other_details",
                "link",
                "score",
                "tier",
                "kin_hits",
                "is_new",
                "score_breakdown",
                "household",
            ]
        )
        for r in records:
            bd_json = json.dumps(r.score_breakdown, ensure_ascii=False) if r.score_breakdown else ""
            hh_json = json.dumps(r.household, ensure_ascii=False) if r.household else ""
            writer.writerow(
                [
                    r.person_searched,
                    r.site,
                    r.record_type,
                    r.name_found,
                    r.age_or_year,
                    r.place,
                    r.other_details,
                    r.link,
                    r.score,
                    confidence_tier(r.score),
                    r.kin_hits,
                    "yes" if r.is_new else "",
                    bd_json,
                    hh_json,
                ]
            )


# _summarise_person moved to genealogy_core.render.summarise_person.


def write_html(records: list[Record], path: Path) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    collapse = RUNTIME_FLAGS.get("collapse_weak", True)
    path.write_text(render_results_html(records, stamp, collapse_weak=collapse),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Search free Irish genealogy sites for your ancestors.")
    p.add_argument("--input", default="ancestors.csv", help="Input CSV (default: ancestors.csv)")
    p.add_argument("--headless", action="store_true", help="Run without a visible browser window.")
    p.add_argument(
        "--skip",
        default="",
        help=("Comma-separated site keys to skip this run. Valid keys: "
              "nai_census, irishgenealogy_civil, griffiths. "
              "Example: --skip nai_census"),
    )
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore .search_cache.json and re-fetch every search.")
    p.add_argument("--no-nicknames", action="store_true",
                   help="Skip forename nickname expansion (John -> Jack/Sean/etc).")
    p.add_argument("--no-score", action="store_true",
                   help="Keep records in discovery order rather than sorting by score.")
    p.add_argument("--no-soundex", action="store_true",
                   help="Don't dedupe surname variants by Soundex code or use Soundex in scoring.")
    p.add_argument("--no-diff", action="store_true",
                   help="Don't compare this run to the previous results.csv (no NEW badges).")
    p.add_argument("--only", default="",
                   help="Comma-separated substrings; only search people whose label matches any. "
                        "Example: --only 'furey,mary kelly'")
    p.add_argument("--since-last", action="store_true",
                   help="Only search people who had zero records in the previous results.csv.")
    p.add_argument("--max-results", type=int, default=None,
                   help=f"Override max rows scraped per search (default {MAX_RESULTS_PER_SEARCH}).")
    p.add_argument("--color", action="store_true",
                   help="Colour the console log by level (green info / yellow warn / red error).")
    p.add_argument("--no-json", action="store_true",
                   help="Skip writing results.json alongside the CSV/HTML.")
    p.add_argument("--no-collapse-weak", action="store_true",
                   help="Don't collapse Weak-tier rows behind a <details> block in HTML.")
    p.add_argument("--per-person", action="store_true",
                   help="Also write one HTML page per person under results/ and turn "
                        "results.html into an index page.")
    p.add_argument("--export-gedcom", action="store_true",
                   help="Write results.ged (minimal GEDCOM 5.5.1, Possible-tier and up).")
    p.add_argument("--gedcom-confirmed-only", action="store_true",
                   help="With --export-gedcom, only include records marked confirmed in "
                        "confirmations.json. Ignored unless --export-gedcom is set.")
    p.add_argument("--no-storage", action="store_true",
                   help="Don't persist browser session cookies across runs.")
    p.add_argument("--no-summary", action="store_true",
                   help="Skip writing run.summary.md.")
    return p.parse_args()


def install_colour_console(logger: logging.Logger) -> None:
    """
    Replace the default stdout handler's formatter with one that wraps
    level + message in ANSI colours. Safe no-op if not a tty.
    """
    if not sys.stdout.isatty():
        return
    colours = {
        "DEBUG": "\033[90m",   # grey
        "INFO":  "\033[32m",   # green
        "WARNING": "\033[33m", # yellow
        "ERROR": "\033[31m",   # red
        "CRITICAL": "\033[41m",
    }
    reset = "\033[0m"

    class ColourFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            base = super().format(record)
            return colours.get(record.levelname, "") + base + reset

    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout:
            h.setFormatter(ColourFormatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))


def filter_people(
    people: list[Person],
    only_substrings: list[str],
    since_last: bool,
    prev_csv: Path,
    logger: logging.Logger,
) -> list[Person]:
    """
    Apply --only / --since-last filters to the input person list. Returns a
    (possibly empty) subset. Logs the reason each person was kept/dropped.
    """
    out = list(people)

    if only_substrings:
        needles = [s.strip().lower() for s in only_substrings if s.strip()]
        out = [p for p in out if any(n in p.label().lower() for n in needles)]
        logger.info("--only filter: kept %d person(s) matching %s", len(out), needles)

    if since_last:
        if not prev_csv.exists():
            logger.warning("--since-last: no previous %s found; searching everyone", prev_csv.name)
        else:
            per_person_counts: dict[str, int] = {}
            try:
                with prev_csv.open("r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        key = row.get("person_searched", "")
                        per_person_counts[key] = per_person_counts.get(key, 0) + 1
            except Exception as exc:
                logger.warning("--since-last: couldn't read %s (%s); searching everyone",
                               prev_csv.name, exc)
                per_person_counts = {}
            # Keep a person only if they had zero hits last time.
            before = len(out)
            out = [p for p in out if per_person_counts.get(p.label(), 0) == 0]
            logger.info("--since-last filter: %d -> %d (dropped those with prior hits)",
                        before, len(out))

    return out


# slugify moved to genealogy_core.render.


# All HTML CSS/JS/table rendering lives in genealogy_core.render. These
# wrappers just hand over the records + flags and write the resulting
# string to disk.


def write_person_html(name: str, recs: list[Record], path: Path) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    collapse = RUNTIME_FLAGS.get("collapse_weak", True)
    html_str = render_person_page(name, recs, stamp,
                                  back_href=f"../{RESULTS_HTML}",
                                  collapse_weak=collapse)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_str, encoding="utf-8")


def write_html_index(records: list[Record], rel_map: dict[str, str], path: Path) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(render_index(records, rel_map, stamp), encoding="utf-8")


def write_per_person_pages(records: list[Record], dir_path: Path) -> dict[str, str]:
    """Write one HTML page per person. Returns name -> relative-path map."""
    grouped: dict[str, list[Record]] = {}
    for r in records:
        grouped.setdefault(r.person_searched, []).append(r)
    dir_path.mkdir(parents=True, exist_ok=True)
    rel_map: dict[str, str] = {}
    for name, recs in grouped.items():
        filename = f"{slugify(name)}.html"
        write_person_html(name, recs, dir_path / filename)
        rel_map[name] = f"{dir_path.name}/{filename}"
    return rel_map


def write_gedcom(records: list[Record], people: list[Person], path: Path,
                 min_tier: str = "Possible",
                 confirmed_keys: set[str] | None = None) -> int:
    """
    Write a minimal GEDCOM 5.5.1 file. For each input Person we emit an INDI
    with name, birth year (if known), and residence/occupation lines pulled
    from their top-scored census hit (at or above `min_tier`). Known spouses
    become a FAM record. Returns the number of INDI records written.

    This is deliberately minimal - the goal is "importable into
    Ancestry/MyHeritage as a starting point", not "fully populated tree".
    """
    rank = {"Manual": 0, "Weak": 1, "Possible": 2, "Likely": 3}
    threshold = rank.get(min_tier, 2)

    lines: list[str] = [
        "0 HEAD",
        "1 SOUR genealogy_search.py",
        "1 CHAR UTF-8",
        "1 GEDC",
        "2 VERS 5.5.1",
        "2 FORM LINEAGE-LINKED",
    ]

    by_person: dict[str, list[Record]] = {}
    for r in records:
        if confirmed_keys is not None and record_key(r) not in confirmed_keys:
            continue
        by_person.setdefault(r.person_searched, []).append(r)

    indi_count = 0
    fam_count = 0
    spouse_indi: dict[str, str] = {}  # spouse display -> xref
    for i, person in enumerate(people, start=1):
        xref = f"I{i}"
        lines.append(f"0 @{xref}@ INDI")
        lines.append(f"1 NAME {person.forename} /{person.surname}/")
        if person.birth_year_approx:
            lines.append("1 BIRT")
            lines.append(f"2 DATE ABT {person.birth_year_approx}")
            if person.county:
                lines.append(f"2 PLAC {person.townland + ', ' if person.townland else ''}{person.county}, Ireland")
        # Enrich with the best-ranked record we have.
        best: Record | None = None
        for r in by_person.get(person.label(), []):
            t = rank.get(confidence_tier(r.score), 0)
            if t >= threshold and (best is None or r.score > best.score):
                best = r
        if best:
            if "Census" in best.record_type and best.age_or_year:
                lines.append("1 RESI")
                lines.append(f"2 DATE {best.age_or_year}")
                if best.place:
                    lines.append(f"2 PLAC {best.place}, Ireland")
            if "Occupation:" in (best.other_details or ""):
                m = re.search(r"Occupation:\s*([^|]+)", best.other_details)
                if m:
                    lines.append(f"1 OCCU {m.group(1).strip()}")
            lines.append(f"1 NOTE Source: {best.site} - {best.link}")
        if person.notes:
            lines.append(f"1 NOTE {person.notes}")
        indi_count += 1

        if person.spouse:
            spouse_xref = spouse_indi.get(person.spouse)
            if not spouse_xref:
                spouse_xref = f"I{len(people) + len(spouse_indi) + 1}"
                spouse_indi[person.spouse] = spouse_xref
                lines.append(f"0 @{spouse_xref}@ INDI")
                parts = person.spouse.strip().split(" ", 1)
                given = parts[0]
                sur = parts[1] if len(parts) > 1 else ""
                lines.append(f"1 NAME {given} /{sur}/")
            fam_xref = f"F{fam_count + 1}"
            fam_count += 1
            lines.append(f"0 @{fam_xref}@ FAM")
            lines.append(f"1 HUSB @{xref}@")
            lines.append(f"1 WIFE @{spouse_xref}@")

    lines.append("0 TRLR")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return indi_count


def write_summary(records: list[Record], people: list[Person], path: Path,
                  new_count: int, cache_hits: int, cache_misses: int) -> None:
    """Write run.summary.md with counts + notable (Likely-tier) finds."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    grouped: dict[str, list[Record]] = {}
    for r in records:
        grouped.setdefault(r.person_searched, []).append(r)

    lines: list[str] = [
        "# Genealogy search run summary",
        f"",
        f"Generated **{stamp}**.",
        "",
        f"- People searched: **{len(people)}**",
        f"- Unique records found: **{len(records)}**",
        f"- New since previous run: **{new_count}**",
        f"- Cache hits / misses: {cache_hits} / {cache_misses}",
        "",
        "## Per-person counts",
        "",
        "| Person | Likely | Possible | Weak | Manual | Total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for person in people:
        recs = grouped.get(person.label(), [])
        buckets = {"Likely": 0, "Possible": 0, "Weak": 0, "Manual": 0}
        for r in recs:
            buckets[confidence_tier(r.score)] += 1
        lines.append(
            f"| {person.label()} | {buckets['Likely']} | {buckets['Possible']} | "
            f"{buckets['Weak']} | {buckets['Manual']} | {len(recs)} |"
        )

    lines += ["", "## Notable (Likely-tier) finds", ""]
    any_notable = False
    for person in people:
        notable = [r for r in grouped.get(person.label(), [])
                   if confidence_tier(r.score) == "Likely"]
        if not notable:
            continue
        any_notable = True
        lines.append(f"### {person.label()}")
        for r in notable[:20]:
            kin = f" (kin: {r.kin_hits})" if r.kin_hits else ""
            lines.append(f"- **{r.record_type}** - {r.name_found} - {r.place} - "
                         f"{r.age_or_year} - score {r.score}{kin} - <{r.link}>")
        lines.append("")
    if not any_notable:
        lines.append("_No Likely-tier records this run._")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(records: list[Record], path: Path) -> None:
    """
    Sidecar JSON output: list of dicts grouped per person. Easier for
    downstream tools than parsing the CSV.
    """
    grouped: dict[str, list[dict]] = {}
    for r in records:
        d = asdict(r)
        d["tier"] = confidence_tier(r.score)
        grouped.setdefault(r.person_searched, []).append(d)
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "total": len(records),
        "people": [{"person": name, "records": recs} for name, recs in grouped.items()],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def preflight_playwright(logger: logging.Logger) -> bool:
    """
    Launch and immediately close a headless chromium to surface a missing
    browser binary before the real run starts. Prints a one-line install hint
    on failure so a beginner knows exactly what to type.
    """
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception as exc:
        msg = str(exc).lower()
        if "executable doesn't exist" in msg or "no such file" in msg or "browsertype.launch" in msg:
            print("ERROR: Playwright's Chromium browser is not installed.")
            print("Run this once, then re-run the script:")
            print("    python -m playwright install chromium")
        else:
            print(f"ERROR: Playwright failed to launch Chromium: {exc}")
        logger.error("Pre-flight Playwright launch failed: %s", exc)
        return False


def main() -> int:
    args = parse_args()
    here = Path(__file__).resolve().parent
    input_path = (here / args.input) if not Path(args.input).is_absolute() else Path(args.input)
    logger = setup_logging(here / LOG_FILE)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        print(f"ERROR: could not find {input_path}. Create ancestors.csv first.")
        return 1

    RUNTIME_FLAGS["soundex"] = not args.no_soundex
    RUNTIME_FLAGS["collapse_weak"] = not args.no_collapse_weak

    if args.color:
        install_colour_console(logger)

    if args.max_results is not None and args.max_results > 0:
        scrape_settings.set_max_results(args.max_results)
        global MAX_RESULTS_PER_SEARCH
        MAX_RESULTS_PER_SEARCH = args.max_results
        logger.info("MAX_RESULTS_PER_SEARCH overridden to %d by --max-results", args.max_results)

    people = read_people(input_path, logger=logger)
    if not people:
        logger.error("No people in %s", input_path)
        return 1

    only_list = [s for s in (args.only or "").split(",") if s.strip()]
    results_csv_path = here / RESULTS_CSV
    people = filter_people(people, only_list, args.since_last, results_csv_path, logger)
    if not people:
        logger.error("No people left after --only / --since-last filter.")
        print("Nothing to search after filters. Check --only / --since-last.")
        return 1

    skip_sites = {s.strip() for s in args.skip.split(",") if s.strip()}
    use_nicknames = not args.no_nicknames
    score_sort = not args.no_score
    cache = SearchCache(here / CACHE_FILE, enabled=not args.no_cache)
    logger.info("Loaded %d people from %s", len(people), input_path)
    logger.info(
        "Headless: %s | Skip: %s | Cache: %s | Nicknames: %s | "
        "Soundex: %s | Score sort: %s | Diff: %s",
        args.headless, sorted(skip_sites) or "none",
        "on" if not args.no_cache else "off",
        "on" if use_nicknames else "off",
        "on" if RUNTIME_FLAGS["soundex"] else "off",
        "on" if score_sort else "off",
        "on" if not args.no_diff else "off",
    )

    # Snapshot the previous results.csv (if any) so we can diff after the run
    # and tag records that didn't exist last time with a NEW badge.
    prev_keys: set[tuple] = set()
    if not args.no_diff and results_csv_path.exists():
        prev_keys = load_previous_keys(results_csv_path)
        try:
            (here / "results.prev.csv").write_bytes(results_csv_path.read_bytes())
        except Exception as exc:
            logger.warning("Could not snapshot previous results.csv: %s", exc)
        logger.info("Loaded %d record keys from previous run for diff", len(prev_keys))

    if not preflight_playwright(logger):
        return 3

    storage_path = None if args.no_storage else (here / STORAGE_FILE)

    confs_path = here / CONFIRMS_FILE
    confs = load_confirmations(confs_path)
    hints_by_person: dict[str, dict] = {}
    if confs:
        prev_records = load_records_csv(results_csv_path)
        confirmed = [r for r in prev_records
                     if confs.get(record_key(r), {}).get("status") == "confirmed"]
        rejected = [r for r in prev_records
                    if confs.get(record_key(r), {}).get("status") == "rejected"]
        hints_by_person = extract_hints(confirmed, rejected)
        if hints_by_person:
            logger.info("Loaded feedback hints for %d person(s): %s",
                        len(hints_by_person), ", ".join(hints_by_person.keys()))

    def _save_snapshot(snapshot: list[Record]) -> None:
        write_csv(snapshot, here / RESULTS_CSV)
        write_html(snapshot, here / RESULTS_HTML)

    try:
        with sync_playwright() as pw:
            records = _run_search(pw, people, args.headless, logger, skip_sites,
                                  cache, use_nicknames, score_sort,
                                  storage_path,
                                  incremental_save=_save_snapshot,
                                  progress=print_progress,
                                  hints_by_person=hints_by_person)
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        _set_terminal_title("genealogy error")
        cache.save()
        return 2

    new_count = mark_new_records(records, prev_keys)
    if confs:
        apply_confirmations(records, confs)
    write_csv(records, results_csv_path)
    # Card-based results.html gets heavy around ~1500 records in a browser;
    # auto-switch to the per-person index above that threshold.
    auto_split = len(records) > 1500
    if args.per_person or auto_split:
        if auto_split and not args.per_person:
            logger.info("Record count %d > 1500; auto-switching to per-person pages.",
                        len(records))
        rel_map = write_per_person_pages(records, here / RESULTS_PEOPLE_DIR)
        write_html_index(records, rel_map, here / RESULTS_HTML)
        logger.info("Wrote %d per-person pages under %s/", len(rel_map), RESULTS_PEOPLE_DIR)
    else:
        write_html(records, here / RESULTS_HTML)
    if not args.no_json:
        write_json(records, here / RESULTS_JSON)
    if args.export_gedcom:
        confirmed_keys = None
        if args.gedcom_confirmed_only:
            confirmed_keys = {k for k, v in confs.items()
                              if v.get("status") == "confirmed"}
            logger.info("GEDCOM filter: confirmed-only (%d key(s))", len(confirmed_keys))
        n = write_gedcom(records, people, here / RESULTS_GEDCOM,
                         confirmed_keys=confirmed_keys)
        logger.info("Wrote %d INDI records to %s", n, RESULTS_GEDCOM)
    if not args.no_summary:
        write_summary(records, people, here / RESULTS_SUMMARY,
                      new_count, cache.hits, cache.misses)
    cache.save()
    logger.info(
        "Done. %d unique records (%d new since last run). "
        "Cache hits: %d, misses: %d. See %s and %s",
        len(records), new_count, cache.hits, cache.misses, RESULTS_CSV, RESULTS_HTML,
    )
    _set_terminal_title(f"genealogy done — {len(records)} records")
    print(f"\nDone. {len(records)} unique record(s) saved to {RESULTS_CSV} and {RESULTS_HTML}.")
    if new_count:
        print(f"  {new_count} record(s) are new since the previous run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
