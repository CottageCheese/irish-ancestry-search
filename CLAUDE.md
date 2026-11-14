# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

At session start, check `BACKLOG.md` for pending work and in-progress phases.

## Commands

```bash
# Install deps (first time)
python -m pip install playwright pytest
python -m playwright install chromium

# Run full tool (headed browser, reads ancestors.csv, writes results.csv/html)
python genealogy_search.py
python genealogy_search.py --headless                # no visible browser
python genealogy_search.py --skip nai_census         # skip one site
python genealogy_search.py --only "Furey,John"       # restrict to subset
python genealogy_search.py --no-cache                # ignore .search_cache.json
python genealogy_search.py --max-results 50          # override MAX_RESULTS_PER_SEARCH

# Tests
python -m pytest tests/ -q                           # full suite (fast, no browser)
python -m pytest tests/test_scoring.py -v            # one file
python -m pytest tests/ -k soundex                   # by keyword
python -m pytest tests/test_scoring.py::test_townland_match_adds_four  # one test
```

No build step. No linter configured.

## Architecture

Three layers with a one-way dependency chain:

```
genealogy_search.py  (CLI, I/O orchestration, writes CSV/HTML/JSON/GEDCOM)
        │
        ├── genealogy_scrape/   (Playwright scrapers, browser helpers, cache, run loop)
        │           │
        │           └── genealogy_core/   (pure logic — no Playwright, no I/O)
        │
        └── genealogy_core/
```

`genealogy_core` is pure: dataclasses (`Person`, `Record`), soundex, nickname expansion, scoring, kin cross-check, diff vs previous run, HTML rendering. Importable without Playwright. All unit tests live here.

`genealogy_scrape` owns everything browser-ish: `SearchCache`, per-site scrapers (`nai.py`, `ig.py`, `griffiths.py`), browser helpers (`safe_goto`, `polite_pause`, CAPTCHA detection), site config (`sites.py`), tunable settings (`settings.py`), and `runner.run_search` which drives the per-person × per-site loop.

`genealogy_search.py` is a thin CLI: arg parsing, CSV I/O, logging setup, and orchestration. It injects two callbacks into `run_search`:
- `incremental_save(snapshot)` — writes `results.csv` + `results.html` after each person so a crash leaves partial progress on disk.
- `progress(current, total, label)` — stderr progress bar.

This callback injection is what keeps `genealogy_scrape` free of HTML/CSV rendering imports.

### Key extraction patterns to preserve

- **Pure-function parsers**: `genealogy_scrape.nai.parse_nai_row(cells, href, fallback_url, person, year, site_label) -> Record` takes a list of cell strings (not a Playwright handle) so it's testable with fixture data. The scraper function extracts cells from the DOM then calls the pure parser. Apply the same shape to any new scraper.
- **Runtime toggles via `RUNTIME_FLAGS`**: `genealogy_core.config.RUNTIME_FLAGS` is a shared dict mutated by the CLI (`--no-soundex`, `--no-collapse-weak`). `Person.all_surnames()` reads it at call time. Don't hardcode flags; respect the dict.
- **CLI override for scraper settings**: `scrape_settings.set_max_results(n)` mutates `genealogy_scrape.settings.MAX_RESULTS_PER_SEARCH`. All scraper modules read `settings.MAX_RESULTS_PER_SEARCH` fresh each call so the override is visible everywhere. Do not shadow with local copies.
- **Dead-site circuit breaker**: `run_search` tracks `site_state[key]["fails"]`. After `MAX_CONSECUTIVE_FAILURES` consecutive load failures, the site is marked dead for the rest of the run. Scrapers increment `state["fails"]` on `safe_goto` failure and reset to 0 on success.
- **Cache keys**: `SearchCache.key(*parts)` lower-cases and pipe-joins. Reuse this — don't invent per-site key schemes.

### Dataflow per run

1. `read_people` → `list[Person]` from `ancestors.csv`.
2. `run_search` loops people × sites. Each site function yields `list[Record]`.
3. `annotate_records(person, found)` (in `genealogy_core.kin`) applies score, kin bonus, and confidence tier.
4. After each person: dedupe → optional sort → incremental save callback.
5. After run: `mark_new_records` tags unseen records (diff against snapshot of previous `results.csv`).
6. `write_csv` / `write_html` / `write_json` / `write_gedcom` / `write_summary` emit final artifacts.

### Site-specific notes

- **NAI census** is an SPA that reads URL-fragment params. `NAI_READY_JS` is a JS predicate polled until results appear (or "no results" text).
- **IrishGenealogy.ie** has six event matrices (civil Birth/Marriage/Death + church Baptism/Marriage/Burial). `_event_window` translates a person's birth window into an event-appropriate window (+18-60y for marriage, +20-100y for death/burial).
- **Griffith's Valuation** is behind Cloudflare. We emit a manual-search link only — no scraping.

### Etiquette

Script enforces 5-10s random delay per host (`polite_pause`). When adding a scraper, call `polite_pause(logger, host)` after every request. Don't shorten delays and don't attempt CAPTCHA bypass — `wait_for_user_after_captcha` pauses for manual solve instead.

## Files not tracked

See `.gitignore`. Scraper outputs (`results.*`, `run.log`, `.search_cache.json`, `.storage_state.json`, `results/`, `notes.md`) are ignored. `ancestors.csv` is **ignored** too (may contain real family PII); keep your real data at that path locally. A sample `ancestors.sample.csv` can be committed for new users.
