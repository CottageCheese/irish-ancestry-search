# Backlog

Working list. Tick when done. Add free-form.

## Kid-friendly UI rework

Goal: web.py from technical scraper surface → learning tool a child can use. Phases incremental, lowest risk first.

- [x] **Phase 1 — Scrub live log of technical terms** (~30 min)
  Replace `nai_census`, `[IG Civil Birth]`, `cache hit`, `polite_pause`, raw URLs with kid phrases. JS phrase dictionary in the `log_json` consumer inside `web.py` template. No backend change.

- [x] **Phase 2 — Primary vs variant tagging** (~1 h)
  Add `name_used: str` (or `is_primary: bool`) to `genealogy_core/models.py::Record`. Scrapers (`nai.py`, `ig.py`) set it per call. Log display + results split "main name" vs "variant spellings".

- [x] **Phase 3 — "What we found" narrative panel** (~1 h)
  Status page after `state=done`: read `results.json`, render cards grouped by confidence tier. Natural sentences, star ratings not numeric scores. Implemented as `/narrative` route in `web.py`, linked from done block.

- [x] **Phase 4 — Kid-friendly results.html** (2–3 h)
  Replaced table in `genealogy_core/render.py` with cards. Emoji icons per record type (📜 census, 👶 birth, 💍 marriage, ⛪ baptism, ⚰ burial, 🏡 Griffiths). Star ratings (★★★/★★/★/🔍). Grouped per person → tier. Person hero with avatar initials + confirm progress bar. Learning tooltips on record icons. Celebratory confetti burst on confirm. Natural-sentence summary + chip row (📍 place, 📅 year, ✨ NEW, 👨‍👩‍👧 kin, 🔀 variant).

## Next up

- [x] Wire CLI (`genealogy_search.py`) to `_build_hints` so command-line runs also benefit from feedback loop (web.py already does).
- [x] Re-render `results.html` on every `/confirm` POST so colours persist across page reload without needing a fresh run.
- [x] `filter_rejected` toggle in web UI: checkbox "hide rejected" that switches rendering path.

## Scoring / matching quality

- [ ] ~~Feedback bonus tuning~~ — deferred. Revisit only if a real run shows confirmed records ranking poorly.
- [x] Add `townland_variants` column to `ancestors.csv` so user can spell variants (Ballybeg / Ballybeag) before any search.
- [x] Negative feedback: rejected records' places should *penalise* similar records (not just filter_rejected drop).
- [x] Soundex on townlands too, not just surnames.

## Scraper robustness

- [x] Fixture-based tests for IG + Griffiths parsers (currently only NAI has `parse_nai_row` with fixture test).
- [x] Capture + save failing HTML to `results/debug/` when a scraper returns 0 but page clearly loaded. Opt-in via `settings.DEBUG_DUMP_HTML`.
- [x] Retry wrapper around `safe_goto` for transient Cloudflare 503s.

## UX

- [x] Status line / taskbar progress in CLI (tail of run.log to stdout live).
- [x] Per-person page (`render_person_page`) has no "back to index" nav yet when opened from HTML results page.
- [x] Download confirmations.json as backup button.
- [x] GEDCOM export of confirmed records only (`--gedcom-confirmed-only`).

## Tooling / dev

- [x] Ruff / black pre-commit hook.
- [x] GitHub Actions CI running pytest on push.
- [x] Document the 3-layer architecture visually (mermaid in README).

## Done

- [x] Extract scrapers out of `genealogy_search.py` into `genealogy_scrape/`.
- [x] Flask web UI with background thread + polling.
- [x] Cookie-banner auto-dismiss in `safe_goto`.
- [x] Live site-done progress + log tail in UI.
- [x] Confirm/reject per record (sidecar `confirmations.json`).
- [x] Feedback loop: confirmed → hints → scoring bonus on next run.
