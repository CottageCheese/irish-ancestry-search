# Backlog

Working list. Tick when done. Add free-form.

## Next up

- [x] Wire CLI (`genealogy_search.py`) to `_build_hints` so command-line runs also benefit from feedback loop (web.py already does).
- [x] Re-render `results.html` on every `/confirm` POST so colours persist across page reload without needing a fresh run.
- [x] `filter_rejected` toggle in web UI: checkbox "hide rejected" that switches rendering path.

## Scoring / matching quality

- [ ] Feedback bonus tuning: current weights (place +2, kin +1, year +2) are guesses. Check whether confirmed records consistently top sort after a second run.
- [ ] Add `known_townland_variants` column to `ancestors.csv` so user can spell variants (Ballybeg / Ballybeag) before any search.
- [x] Negative feedback: rejected records' places should *penalise* similar records (not just filter_rejected drop).
- [ ] Soundex on townlands too, not just surnames.

## Scraper robustness

- [x] Fixture-based tests for IG + Griffiths parsers (currently only NAI has `parse_nai_row` with fixture test).
- [ ] Capture + save failing HTML to `results/debug/` when a scraper returns 0 but page clearly loaded.
- [x] Retry wrapper around `safe_goto` for transient Cloudflare 503s.
- [ ] Fix or delete `--deep` mode. Status unclear.

## UX

- [ ] Status line / taskbar progress in CLI (tail of run.log to stdout live).
- [ ] Per-person page (`render_person_page`) has no "back to index" nav yet when opened from HTML results page.
- [ ] Download confirmations.json as backup button.
- [ ] GEDCOM export of confirmed records only.

## Tooling / dev

- [ ] Ruff / black pre-commit hook.
- [ ] GitHub Actions CI running pytest on push.
- [ ] Document the 3-layer architecture visually (mermaid in README).

## Done

- [x] Extract scrapers out of `genealogy_search.py` into `genealogy_scrape/`.
- [x] Flask web UI with background thread + polling.
- [x] Cookie-banner auto-dismiss in `safe_goto`.
- [x] Live site-done progress + log tail in UI.
- [x] Confirm/reject per record (sidecar `confirmations.json`).
- [x] Feedback loop: confirmed → hints → scoring bonus on next run.
