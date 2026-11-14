# Backlog

Working list. Tick when done. Add free-form.

## Open

### Ideas — scoring / matching
- [ ] ~~Feedback bonus tuning~~ — deferred. Revisit only if a real run shows confirmed records ranking poorly.

### Ideas — scraper coverage
- [ ] New site: RootsIreland parish records (paid, but searches are free).
- [ ] New site: Catholic Parish Registers (NLI) — digitised scans, no index.
- [ ] Handle 1821-1851 census fragments (held by NAI separately).
- [x] Household grouping: when a census row is found, also fetch other names living at same address and attach as kin suggestions.

### Ideas — UX / learning
- [ ] Mini family tree per person: draw boxes for father/mother/spouse from `ancestors.csv`, highlight ones with confirmed records.
- [ ] Mobile layout: cards stack better; conf-buttons full-width on <600px.
- [ ] Read-aloud mode: speech-synthesis button on each card so a young user can hear the sentence.
- [ ] Printable one-pager per ancestor: strip JS/filters, just confirmed records + sources.

### Ideas — data
- [ ] "Export my family tree" button — build a GEDCOM from confirmed records across all ancestors with inferred relationships.
- [ ] Import GEDCOM to seed `ancestors.csv`.


## Done

### Loading screen & stop button (Nov 2026)
- [x] Layout shift fix: fore-variants, rec-tip, search-quality use `visibility:hidden` not `display:none` — nothing moves when typing.
- [x] Stop button now cancels between sites (not just between people) — single-person quick search responds immediately.
- [x] On cancel: partial results saved to JSON/summary, cache flushed, state goes to `done` not `error`; yellow "stopped early" banner shown.
- [x] Loading screen driven by actual step (`s.step`) — per-step kid-friendly message + italic description replaces random rotating messages.
- [x] Live checklist builds up as steps complete: ✓ done in green, 🔍 spinning for current step.
- [x] Record counter shows "🎉 Found N records so far!" or "Nothing found yet — still looking…".
- [x] ⚡ Census only / 🔍 Full search preset buttons above filters — one click configures all toggles (3 vs 9+ searches).

### Scoring fix — forename mismatch penalty (Nov 2026)
- [x] Wrong forename scores -4; correct forename (or any nickname/variant) scores +3. Prevents surname+county+year alone reaching "Possible".

### Search form & results improvements (Nov 2026)
- [x] Exact year toggle on quick-search: "Approx (±5 years)" vs "Exact year" radio; writes `year_range=0`; fixed validator to accept 0.
- [x] "What we found" button opens `results.html` directly; `/narrative` redirects there; duplicate button removed.
- [x] Records filter: chronological order (Baptisms → Births → Marriages → Deaths → Burials → Land records); hint text: civil registration started 1864 (marriages 1845).
- [x] M/F/Unknown sex radio on quick-search form; stored in `ancestors.csv`; auto-rejects obvious wrong-sex records in `annotate_records` using MALE_NAMES/FEMALE_NAMES sets built from NICKNAMES.
- [x] Live record count ("N records found so far") shown on animated loader screen during search.
- [x] "Reject rest" button per person in `results.html`: one click rejects all non-Likely, non-confirmed records for that person.

### Quick-search filter polish (Nov 2026)
- [x] "All" chip prepended to Census + Records filter rows; default-active, deactivates when any sibling is ticked, clears siblings on click.
- [x] Hint line under filters: "Pick some to search faster, or leave on *All* for everything."
- [x] Server-side `skip_sites` derivation in `/quick-search`: when any filter is set, unselected site groups are genuinely skipped (fixes census-only run still firing all six IG matrices because `IG_EVENT_FILTER=()` means "no filter").
- [x] Browser smoke test at `scripts/browser_smoke.py` (untracked) verifies All-chip toggling + multi-select behaviour.

### Navigation & home page (Nov 2026)
- [x] `/ancestors` page: card grid with avatar initials, pastel colours, confirmed/found badges, sorted by confirmed count.
- [x] `/person/<slug>` page: per-person records grouped by confidence tier, back nav to `/ancestors`.
- [x] Home: family pill link above form, visible without scrolling. SVG scene moved to `/ancestors` banner.
- [x] `/ancestors` page: landscape SVG banner, confirmed-card gold glow, summary bar (N confirmed · N found · N unsearched).
- [x] Remove × button on ancestor cards to delete mistyped entries from `ancestors.csv`.
- [x] Animated loader (sheep bounce + rotating messages) always shown when run is in progress; cancel button.
- [x] Census year + record type filter buttons on home form; multi-select checkboxes (none = search all).
- [x] `CENSUS_YEAR_FILTER` updated to support a set of years; NAI scraper reads set or string.

### Household grouping (Nov 2026)
- [x] `fetch_nai_household` loads NAI detail page, extracts co-residents via `parse_nai_household` (pure fn, surname-first column order, subject excluded, deduped).
- [x] `_search_nai_one` fetches household for first `HOUSEHOLD_FETCH_MAX` (default 10) records per search; cached under `nai_household|{url}`.
- [x] `Record.household: list[str]` field; serialised as JSON in CSV; parsed back in `load_records_csv`.
- [x] `kin_cross_check` haystack extended to include household names → auto-triggers existing +5 kin bonus.
- [x] "🏠 Lived with: …" chip on card; truncates to 4 names + count; light/dark styled.

### Census age parser (Nov 2026)
- [x] `_parse_age` + `_census_year` helpers in `scoring.py`; for Census rows compute implied birth year = `census_year - age`, match against `person.year_window()` with ±2y tolerance → `census_age: 3` bonus. Skips `year_window` path when census-age hits. 6 new tests.

### Score transparency (Nov 2026)
- [x] `score_record` returns `(total, breakdown)`; `Record.score_breakdown` dict field; `annotate_records` merges kin+feedback into same dict.
- [x] "Why this score?" expandable chip on each card showing signed delta per component with human labels.
- [x] CSV column `score_breakdown` (JSON blob); `load_records_csv` parses it back.

### Dedupe follow-up (Nov 2026)
- [x] Semantic dedupe second pass in `runner.dedupe_records`: collapse by `(person, site, record_type, name_found, place, age_or_year)`, keep highest-scoring row. Fixes 91-copy IG birth bloat.

### Kid-friendly form polish (Nov 2026)
- [x] Sex field moved above county/year; replaced radios with emoji pill buttons (👦 Boy/Man · 👧 Girl/Woman · 🤷 Don't know).
- [x] Emoji labels on all form fields (👤 First name, 👪 Last name, 🗺️ Where in Ireland?, 🎂 When were they born?).
- [x] Record type tooltips: hover/focus any Records button → golden one-sentence kid-friendly explanation panel.
- [x] Search quality stars: live ⭐☆☆→⭐⭐⭐ as fields filled, with nudge hint ("Add a birth year to narrow it down").
- [x] Birth year helper: 🎲 "Help me guess" button → generation picker (Parent/Grandparent/Great-grandparent/Further back) fills year automatically.
- [x] Births button moved before Baptisms in record filter.

### Nickname variant UI (Nov 2026)
- [x] Typing a first name on "Who do you want to find?" shows nickname chips (e.g. William → will · bill · billy · liam) using embedded NICKNAMES dict in JS.
- [x] "Search all these names too ✨" checkbox (default checked) controls `use_nicks` passed to the search worker; unchecked = exact name only.
- [x] Results page gains client-side "Who do you want to find?" name filter: filters person-blocks by first+last name with same variant-aware toggle.

### Kid-mode quick wins (Nov 2026)
- [x] Nicknames default-on in `/quick-search` worker opts (`use_nicks=True`). Michael → Mick/Mike automatic.
- [x] Sheep spinner + "Asking the old books…" on quick-search submit; button disables.
- [x] "Try another name" button (styled `go`) replaces plain "New run" on done/error panel.

### Kid-mode landing (Nov 2026)
- [x] Replace idle branch of `/` with themed hero form (forename/surname/county/birth_year only).
- [x] `/quick-search` POST: dedupe append to `ancestors.csv` on (forename+surname), auto-start worker scoped via `only=`.
- [x] Irish palette + Palatino serif + inline SVG scene (hills, cottage, sheep, sun) — no external image licences.
- [x] End-to-end tested: Michael Murphy, Cork, 1870 → 176 records (84 Likely / 91 Possible).

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
