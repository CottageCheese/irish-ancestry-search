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

### Ideas — portal landing
- [ ] Landing rework: `/` idle branch becomes portal dashboard, not just quick-search form.
- [ ] Hero strip: counts (ancestors / confirmed / bookmarked) + progress ring + oldest-ancestor one-liner.
- [ ] Three tiles: Our Family · Bookmarks · Find Someone (current form lives behind tile 3 or collapsed below).
- [ ] Smart CTA: "Add first ancestor" / "Review N pending" / "Re-run search" based on state.
- [ ] Mystery-box: pending-review count framed as "N clues waiting".
- [ ] Map pin cloud: Ireland SVG, dots per confirmed-ancestor county, click filters Our Family.
- [ ] Era timeline ribbon: horizontal 1800s→now, coverage density; reuses `ERA_ORDER`.
- [ ] Recently-found strip: last 3-5 new records with thumbnails.
- [ ] Streak / last-activity line ("Last find: 3 days ago").
- [ ] Persistent top-bar search + help/tour button on every screen.
- [ ] First-visit 3-slide tour (extends existing welcome overlay).

### Ideas — UX / learning
- [ ] Mini family tree per person: draw boxes for father/mother/spouse from `ancestors.csv`, highlight ones with confirmed records.
- [ ] Mobile layout: cards stack better; conf-buttons full-width on <600px.
- [ ] Read-aloud mode: speech-synthesis button on each card so a young user can hear the sentence.
- [ ] Printable one-pager per ancestor: strip JS/filters, just confirmed records + sources.
- [ ] Progress stickers / badges: 🥇 First census found, 🏅 Three decades matched, 🎖️ Household unlocked.
- [ ] Live-log re-skin: hide "(remembered from earlier)" tech detail; friendlier phrasing on in-progress steps.
- [ ] Decade/exact toggle polish: persist last-used mode in `localStorage` so returning users skip re-choosing.

### Ideas — data
- [ ] "Export my family tree" button — build a GEDCOM from confirmed records across all ancestors with inferred relationships.
- [ ] Import GEDCOM to seed `ancestors.csv`.


## Done

### Era grouping on person page (Nov 2026)
- [x] `era_groups` built server-side in `person_page`: each record bucketed via `_era_for_year(record.year)`, undated records pinned last, within each era sort by year then tier priority.
- [x] New "By era / By confidence" toggle in `person.html`; era view is default. Existing tier blocks kept intact behind the second tab (anchors suffixed `-t` to avoid duplicate IDs with era-view anchors).
- [x] Each era card shows a small `.tier-chip` (stars + tier label, tier-coloured) so confidence is visible without the tier heading.

### Era groups + generation filter + progress rings on Our Family (Nov 2026)
- [x] `ERA_ORDER`/`ERA_RANK` and `_generation(year, youngest)` added to `helpers.py`; `GEN_LABELS` with emoji chips.
- [x] `ancestors_page` groups cards under chronological era headings (Famine, Land War, Rising…). Within each era: sort by confirmed desc, found desc, label asc.
- [x] Generation filter row (👶 Youngest · 👪 Parents · 👴 Grandparents · 🕰️ Great-grans · 📜 Further back) shown when ≥2 generations present; derived from each person's birth year vs youngest known. Two-axis filter (found × gen) with empty-era auto-hide.
- [x] SVG progress ring around each avatar (gold stroke, 226.2 circumference, `stroke-dasharray` animated) + `%` pill badge. Hidden when no records found.

### Split web.py into blueprint package (Nov 2026)
- [x] 3642-line `web.py` carved into `web_app/` package: `state.py` (RUN dict + locks + paths), `worker.py` (background thread + log regex), `helpers.py` (avatars, tiers, county coords, next-suggestions, rows I/O), four blueprints (`runs`, `people`, `ancestors`, `results`).
- [x] Six inline `*_HTML` string constants extracted to `web_app/templates/*.html`; `render_template_string` → `render_template`. Theme wrapper baked into the four templates that needed it at extraction time.
- [x] `web.py` reduced to 25-line shim (`create_app()` + `__main__`) so `python web.py` still boots; re-exports `_next_suggestions` + `CSV_COLUMNS` for existing tests.
- [x] One-way import chain: blueprints → worker → state. 145 tests pass; smoke GET on /, /status.json, /ancestors return 200.

### Results page: record-type visuals + source-aware links (Nov 2026)
- [x] `_record_category(record_type)` bucket helper → `rec-cat-{census,civil,church,griffith,manual}` class on each `.rec-card`.
- [x] Census + civil (official records) get parchment gradient bg, bronze inset stripe, "OFFICIAL ·" uppercase prefix on title. Church cream, Griffith earthy. Dark-mode variants. Tier left-border + confirm/save/reject status still win.
- [x] `_site_short(site)` maps raw site string → friendly name. Open button now `🔎 Open on National Archives ↗` / `IrishGenealogy` / `Griffith's`, `rel='noopener'`, tooltip shows full site.

### "Who next?" cards on done screen (Nov 2026)
- [x] `_next_suggestions(person_label, rows)` in `web.py`: reads subject's father/mother/spouse from ancestors.csv; when the relative is unrecorded, emits a `prefill_only` slot instead of nothing so there's always a next step. Spouse only shown when explicitly named (too speculative otherwise). Dad slot inherits subject's surname; mum surname stays blank (maiden name unknown); parent birth year defaults to subject − 30, spouse to subject's year.
- [x] `/next-suggestions.json` endpoint returns suggestions for `RUN.label` when state=='done' and not stopped; served via `fetch` from the done-panel JS after the poll detects completion (server-render only fires on fresh page loads, so JS covers the mid-poll case).
- [x] Done scroll renders a `.next-cards` block: named relatives → POST form auto-launching `/quick-search`; prefill slots → GET link to `/?forename=&surname=…` landing user on a prefilled form with cursor in the forename field.
- [x] Quick-search form inputs honour `request.args` for forename/surname/county/townland/birth_year/sex so the prefill link round-trips cleanly.
- [x] 12 tests in `tests/test_next_suggestions.py` covering missing subject, generic-slot fallback, surname inheritance, dedupe against existing CSV rows, bad birth year, "(saved)" label suffix.

### `:has()` fallback for filter pills (Nov 2026)
- [x] `.tog-btn` census/record pills gained a `.on` class synced by JS on load, change, and preset click. CSS selector widened to `.tog-btn:has(input:checked), .tog-btn.on` so browsers without `:has()` (older Firefox, pre-105 Chrome) still show pre-checked pills as moss-green instead of white-on-white, which made the preset button look selected while the pills looked unchecked.

### Story card + fun facts per person (Nov 2026)
- [x] New `genealogy_core/story.py` pure module: `build_story(person, records)` returns chronological kid-friendly sentences from confirmed records (falls back to Likely tier if nothing confirmed).
- [x] Life events: birth/baptism, marriage, death/burial, census (year pulled from `record_type`), Griffith's. Deduped by (kind, year).
- [x] `STORY_EVENTS` timeline (Famine, Titanic, Easter Rising, WWs, census years) → up to 2 "✨ William was 23 when the Titanic sank (1912)" facts; skipped when their year overlaps a story event.
- [x] Rendered in `_person_hero` as collapsible `<details class='story-box' open>` with parchment styling + dark-mode support.
- [x] `render_results_html` / `render_person_page` accept optional `persons` map; `write_html` / `write_person_html` auto-load `ancestors.csv` so birth year powers fun facts.
- [x] 15 tests in `tests/test_story.py` covering empty, fallback, chronology, dedupe, fun-fact caps, rejected-exclusion.

### "See who we found" button rename (Nov 2026)
- [x] Done-screen CTA renamed from "Open your treasure" → "See who we found" — kid-friendly, honest about content (records list, not a map).

### Unfurling-scroll done screen (Nov 2026)
- [x] Celebration banner rebuilt as parchment scroll between two wooden rods on `/` done panel; `.scroll-parchment` animates max-height 0 → 900px on `.unfurled` class (1.2s cubic-bezier).
- [x] JS adds `.unfurled` once per `state === 'done'` transition via double-rAF, then smooth-scrolls `#st-done` into view after 200ms. Class cleared on error/running so next run replays.
- [x] Big gold "🗝️ Open your treasure" CTA inside scroll → `results.html`; pulsing box-shadow keyframes. Replaces old "What we found" btn.

### Quick-search year mode + kid-friendly done screen (Nov 2026)
- [x] Segmented `[Decade] [Exact year] [Not sure]` toggle on quick-search; swaps visible panel, hidden `birth_year`/`year_mode` contract unchanged.
- [x] Dev telemetry line (`dbg: state=…`) removed from status panel.
- [x] On done, hide "State / Progress / Now searching / Records so far" rows; show celebration banner "🎉 You found N clues about <Name>!" + "N new since last time."
- [x] Download buttons renamed "💾 Save (CSV)/(JSON)"; `run.log` + `confirmations.json` tucked behind collapsible "Grown-up files" disclosure.

### Late-1800s Irish cottage scene (Nov 2026)
- [x] Both SVG scenes (loader 800×200, ancestors banner 800×130) redrawn: whitewashed limewashed walls, thatched roof with fan-texture lines + ridge, stone chimney at gable with smoke curl, split half-door (dutch), four-pane deep-set windows, stone foundation base.
- [x] Added drystone field wall (grey stones), turf/peat stack, haycock, hawthorn tree, brown hen with red comb, woollier sheep (wool-curl circles + face).
- [x] Softer Irish sky gradient (blue → warm cream) with cloud wisps; paler sun with halo ring. Dropped the floating book emoji.

### Loader step sync fix (Nov 2026)
- [x] `_StepHandler` relaxed — updates `RUN["step"]` on any `_STEP_RE` match, not only lines containing " -> " or "cache hit".
- [x] STEP_INFO keys corrected: log tags use spaces (`IG Civil Birth`) not dashes (`IG Civil-Birth`); loader was bailing on `!info` so screen stuck on "Getting ready…".
- [x] Griffith entry added to STEP_INFO.

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
