# Backlog

Working list. Tick when done. Add free-form.

## Open

### Ideas — scoring / matching
- [ ] ~~Feedback bonus tuning~~ — deferred. Revisit only if a real run shows confirmed records ranking poorly.
- [ ] "Why this score?" expandable chip on each card — list which bits matched (surname / soundex / year / county / kin). Needs `score_record` to return a breakdown, not just an int.
- [ ] Age-field parser for census rows: convert "26" (age in 1901) → implied birth year ~1875, feed into year-window check.

### Ideas — scraper coverage
- [ ] New site: RootsIreland parish records (paid, but searches are free).
- [ ] New site: Catholic Parish Registers (NLI) — digitised scans, no index.
- [ ] Handle 1821-1851 census fragments (held by NAI separately).
- [ ] Household grouping: when a census row is found, also fetch other names living at same address and attach as kin suggestions.

### Ideas — UX / learning
- [ ] Mini family tree per person: draw boxes for father/mother/spouse from `ancestors.csv`, highlight ones with confirmed records.
- [ ] Mobile layout: cards stack better; conf-buttons full-width on <600px.
- [ ] Read-aloud mode: speech-synthesis button on each card so a young user can hear the sentence.
- [ ] Printable one-pager per ancestor: strip JS/filters, just confirmed records + sources.

### Ideas — data
- [ ] "Export my family tree" button — build a GEDCOM from confirmed records across all ancestors with inferred relationships.
- [ ] Import GEDCOM to seed `ancestors.csv`.

## Done

### Kid-friendly UI rework
- [x] Phase 1 — Scrub live log of technical terms (JS phrase dictionary in `web.py`).
- [x] Phase 2 — Primary vs variant tagging (`Record.name_used`; scrapers stamp per call; render splits display).
- [x] Phase 3 — "What we found" narrative panel (`/narrative` route; cards grouped by tier; natural sentences + star ratings).
- [x] Phase 4 — Kid-friendly `results.html` (card layout; emoji icons per record type; star ratings; person hero with avatar + confirm progress bar; learning tooltips; confetti on confirm; chip row).

### Scoring / matching
- [x] `townland_variants` column in `ancestors.csv`.
- [x] Negative feedback penalises places from rejected records.
- [x] Soundex on townlands.
- [x] Per-event-type year windows (marriage +18-45, death/burial +30-100, census +0-100).
- [x] `Person.forename_variants`.

### Scraper robustness
- [x] Fixture-based tests for IG + Griffiths parsers.
- [x] Save failing HTML to `results/debug/` when 0 results but page loaded (`settings.DEBUG_DUMP_HTML`).
- [x] Retry wrapper around `safe_goto` for Cloudflare 503s.
- [x] `CENSUS_YEAR_FILTER` + `IG_EVENT_FILTER` settings so UI/CLI can limit per-run scope.
- [x] `PAUSE_HOOK` in `browser.polite_pause` so UI can show countdowns.

### UX
- [x] CLI status line / taskbar progress.
- [x] "Back to index" nav on per-person pages.
- [x] Download `confirmations.json` backup button.
- [x] GEDCOM export of confirmed records only (`--gedcom-confirmed-only`).
- [x] Wire CLI to `_build_hints` so command-line runs use feedback loop.
- [x] Re-render `results.html` on every `/confirm` POST.
- [x] `filter_rejected` toggle in web UI (hide rejected checkbox).

### Tooling / dev
- [x] Ruff / black pre-commit hook.
- [x] GitHub Actions CI running pytest on push.
- [x] Mermaid architecture diagram in README.
- [x] Untrack `ancestors.csv` (PII); gitignore it.
- [x] `ancestors.sample.csv` template for new users; README copy step.
- [x] Perf bench on 2000 cards (18ms server render, 2.3 MB HTML); auto-switch to per-person pages above 1500 records.
- [x] First-visit welcome tour overlay with `?` help button and `localStorage` dismiss.

### Foundation
- [x] Extract scrapers into `genealogy_scrape/`.
- [x] Flask web UI with background thread + polling.
- [x] Cookie-banner auto-dismiss in `safe_goto`.
- [x] Live site-done progress + log tail in UI.
- [x] Confirm/reject per record (sidecar `confirmations.json`).
- [x] Feedback loop: confirmed → hints → scoring bonus on next run.
