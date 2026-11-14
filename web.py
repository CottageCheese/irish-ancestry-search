"""Tiny Flask front-end: upload ancestors.csv, start a run, poll progress,
view results.html when done.

One run at a time. State held in a module-level dict (RUN). Scraper runs in
a background thread so the HTTP request returns immediately.

    pip install flask
    python web.py
    # open http://127.0.0.1:8000

CAPTCHAs: the Playwright browser window opens on the machine running this
script. Solve it there, press Enter in the terminal, then refresh the
status page.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

import csv

from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from playwright.sync_api import sync_playwright

from genealogy_core.nicknames import expand_forename
from genealogy_core.soundex import soundex
from genealogy_core import (
    Record,
    RUNTIME_FLAGS,
    VALID_STATUSES,
    annotate_records,
    apply_confirmations,
    extract_hints,
    filter_rejected,
    load_confirmations,
    load_records_csv,
    load_previous_keys,
    mark_new_records,
    record_key,
    save_confirmations,
    set_status,
)
from genealogy_scrape import SearchCache, run_search as _run_search
from genealogy_scrape import settings as scrape_settings
from genealogy_scrape.sites import SITES
from genealogy_scrape import browser as scrape_browser
import time

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

HERE = Path(__file__).resolve().parent
UPLOAD_CSV = HERE / "ancestors.csv"
CONFIRMS_FILE = HERE / "confirmations.json"
CONFIRMS_LOCK = threading.Lock()

app = Flask(__name__)

RUN: dict = {
    "state": "idle",      # idle | running | done | error
    "current": 0,
    "total": 0,
    "label": "",
    "last_site": "",
    "live_records": 0,
    "started": None,
    "finished": None,
    "error": "",
    "new_count": 0,
    "record_count": 0,
    "pause_host": "",
    "pause_seconds": 0.0,
    "pause_until": 0.0,
    "server_now": 0.0,
    "step": "",
}
RUN_LOCK = threading.Lock()


INDEX_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>Irish genealogy search</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
  label { display: block; margin: .6rem 0 .2rem; font-weight: 600; }
  input[type=file], input[type=text] { width: 100%; padding: .4rem; box-sizing: border-box; }
  .row { display: flex; gap: 1rem; flex-wrap: wrap; }
  .row label { font-weight: 400; }
  button { padding: .6rem 1.2rem; font-size: 1rem; cursor: pointer; }
  button[disabled] { opacity: .5; cursor: not-allowed; }
  .state { margin: 1rem 0; padding: 1rem; background: #f5f5f5; border-radius: 6px; }
  .bar { height: 10px; background: #e0e0e0; border-radius: 5px; overflow: hidden; margin-top: .4rem; }
  .bar > div { height: 100%; background: #4a90e2; transition: width .3s; }
  a.btn { display: inline-block; margin-top: .6rem; padding: .4rem .8rem; background: #4a90e2; color: white; text-decoration: none; border-radius: 4px; }
</style>

<h1>Irish genealogy search</h1>

{% if run.state == 'idle' %}
<details class="state" style="background:#fff7e0;border:1px solid #f0d08a">
  <summary style="cursor:pointer;font-weight:600;font-size:1rem">&#128214; What is this? (click to open)</summary>
  <div style="margin-top:.5rem;font-size:.95rem;line-height:1.45">
    A free search of Ireland's old records. Pick someone from your family, and we'll
    look them up in three different places on the internet:
    <ul style="margin:.4rem 0 .4rem 1.2rem;padding:0">
      <li>&#128203; <strong>The census</strong> &mdash; a big head-count the government did in <strong>1901, 1911 and 1926</strong>, listing every person in every house.</li>
      <li>&#128220; <strong>Birth, marriage &amp; death records</strong> &mdash; and church baptisms, weddings and burials going back to the 1800s.</li>
      <li>&#127969; <strong>Griffith's Valuation</strong> &mdash; a list of who rented or owned land in the <strong>1850s</strong>. Older than the census!</li>
    </ul>
    <small style="color:#666">Tip: start with just one person and one record type. Add more only after you've seen some results.</small>
  </div>
</details>

<form method="post" action="{{ url_for('start') }}" enctype="multipart/form-data">

  <div class="state" style="background:#eef6ff">
    <strong>Who to search for</strong>
    <div style="font-size:.85rem;color:#444;margin-top:.2rem">
      Pick an ancestor from your list, or leave as {All} to search everyone.
      <a href="{{ url_for('people_page') }}">Edit the list &rarr;</a>
    </div>
    <div style="display:flex;gap:.6rem;align-items:center;margin-top:.4rem;font-size:.85rem">
      <span style="color:#555">Sort by:</span>
      <div id="sort-group" role="group" style="display:inline-flex;border:1px solid #bcd;border-radius:999px;overflow:hidden;background:#fff">
        <button type="button" class="sort-btn" data-v="sur"     style="border:0;padding:.3rem .8rem;cursor:pointer;background:transparent;font:inherit">Surname</button>
        <button type="button" class="sort-btn" data-v="full"    style="border:0;padding:.3rem .8rem;cursor:pointer;background:transparent;font:inherit;border-left:1px solid #bcd">Forename</button>
        <button type="button" class="sort-btn" data-v="entered" style="border:0;padding:.3rem .8rem;cursor:pointer;background:transparent;font:inherit;border-left:1px solid #bcd">As entered</button>
      </div>
    </div>
    <select name="pick" id="pick" style="margin-top:.4rem;padding:.4rem;width:100%;font-size:1rem">
      <option value="" data-idx="-1">{All} ({{ people|length }} people)</option>
      {% for p in people %}
      <option value="{{ p.forename }} {{ p.surname }}"
              data-idx="{{ loop.index0 }}"
              data-fore="{{ p.forename }}"
              data-sur="{{ p.surname }}"
              data-var="{{ p.surname_variants }}"
              data-year="{{ p.birth_year_approx }}"
              data-county="{{ p.county }}"
              data-townland="{{ p.townland }}">
        {{ p.forename }} {{ p.surname }}{% if p.birth_year_approx %} c.{{ p.birth_year_approx }}{% endif %}{% if p.townland %} — {{ p.townland }}{% endif %}{% if p.county %}, {{ p.county }}{% endif %}
      </option>
      {% endfor %}
    </select>

    <div id="search-preview" style="margin-top:.8rem;padding:.6rem .8rem;background:#fff;border:1px solid #cde;border-radius:4px;font-size:.95rem">
      <div style="font-size:.75rem;color:#666;text-transform:uppercase;letter-spacing:.05em">Will search for</div>
      <div id="sp-main" style="font-size:1.15rem;margin-top:.2rem"><em style="color:#888">Pick someone above.</em></div>
      <div id="sp-extra" style="margin-top:.3rem;font-size:.85rem;color:#555"></div>
    </div>
  </div>

  <div class="state">
    <label style="margin-top:0">Where to look</label>
    <input type="hidden" name="census_year" id="census_year" value="">
    <input type="hidden" name="ig_event" id="ig_event" value="">
    <div style="font-size:.85rem;margin:.2rem 0 .6rem;color:#555">
      Three different websites, three different kinds of records. Tick the ones to search.
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:.8rem">

      <div class="site-card" data-site="nai_census"
           style="border:2px solid #bcd;border-radius:8px;padding:.7rem .9rem;background:#fff">
        <label style="display:flex;align-items:center;gap:.5rem;font-weight:600;cursor:pointer;margin:0">
          <input type="checkbox" name="use_site" value="nai_census" checked style="width:1.1rem;height:1.1rem">
          <span style="font-size:1.3rem">&#128203;</span>
          <span>Census head-count</span>
        </label>
        <div style="margin-top:.3rem;font-size:.85rem;color:#444;line-height:1.35">
          A list of <em>every person</em> living in <em>every house</em> in Ireland on one night.
          Shows ages, jobs and who shared the room.
        </div>
        <div style="margin-top:.5rem">
          <div style="font-size:.75rem;color:#666;margin-bottom:.2rem">Which year?</div>
          <div id="year-group" role="group" style="display:inline-flex;border:1px solid #bcd;border-radius:999px;overflow:hidden;background:#fff;font-size:.85rem">
            <button type="button" class="year-btn" data-v=""     style="border:0;padding:.3rem .7rem;cursor:pointer;background:transparent;font:inherit">All</button>
            <button type="button" class="year-btn" data-v="1901" style="border:0;padding:.3rem .7rem;cursor:pointer;background:transparent;font:inherit;border-left:1px solid #bcd">1901</button>
            <button type="button" class="year-btn" data-v="1911" style="border:0;padding:.3rem .7rem;cursor:pointer;background:transparent;font:inherit;border-left:1px solid #bcd">1911</button>
            <button type="button" class="year-btn" data-v="1926" style="border:0;padding:.3rem .7rem;cursor:pointer;background:transparent;font:inherit;border-left:1px solid #bcd">1926</button>
          </div>
        </div>
      </div>

      <div class="site-card" data-site="irishgenealogy_civil"
           style="border:2px solid #bcd;border-radius:8px;padding:.7rem .9rem;background:#fff">
        <label style="display:flex;align-items:center;gap:.5rem;font-weight:600;cursor:pointer;margin:0">
          <input type="checkbox" name="use_site" value="irishgenealogy_civil" checked style="width:1.1rem;height:1.1rem">
          <span style="font-size:1.3rem">&#128220;</span>
          <span>Births, marriages &amp; deaths</span>
        </label>
        <div style="margin-top:.3rem;font-size:.85rem;color:#444;line-height:1.35">
          Government records for when someone was <em>born</em>, got <em>married</em>, or <em>died</em>.
          Also church baptisms, weddings and burials.
        </div>
        <div style="margin-top:.5rem">
          <div style="font-size:.75rem;color:#666;margin-bottom:.2rem">Which event?</div>
          <div id="event-group" role="group" style="display:flex;flex-wrap:wrap;gap:.25rem;font-size:.8rem">
            <button type="button" class="ev-btn" data-v=""               style="border:1px solid #bcd;border-radius:999px;padding:.2rem .6rem;cursor:pointer;background:#fff;font:inherit">All</button>
            <button type="button" class="ev-btn" data-v="event-birth"    style="border:1px solid #bcd;border-radius:999px;padding:.2rem .6rem;cursor:pointer;background:#fff;font:inherit">&#127793; Birth</button>
            <button type="button" class="ev-btn" data-v="event-baptism"  style="border:1px solid #bcd;border-radius:999px;padding:.2rem .6rem;cursor:pointer;background:#fff;font:inherit">&#128118; Baptism</button>
            <button type="button" class="ev-btn" data-v="event-marriage" style="border:1px solid #bcd;border-radius:999px;padding:.2rem .6rem;cursor:pointer;background:#fff;font:inherit">&#128141; Marriage</button>
            <button type="button" class="ev-btn" data-v="event-death"    style="border:1px solid #bcd;border-radius:999px;padding:.2rem .6rem;cursor:pointer;background:#fff;font:inherit">&#9752;&#65039; Death</button>
            <button type="button" class="ev-btn" data-v="event-burial"   style="border:1px solid #bcd;border-radius:999px;padding:.2rem .6rem;cursor:pointer;background:#fff;font:inherit">&#128367;&#65039; Burial</button>
          </div>
        </div>
      </div>

      <div class="site-card" data-site="griffiths"
           style="border:2px solid #bcd;border-radius:8px;padding:.7rem .9rem;background:#fff">
        <label style="display:flex;align-items:center;gap:.5rem;font-weight:600;cursor:pointer;margin:0">
          <input type="checkbox" name="use_site" value="griffiths" checked style="width:1.1rem;height:1.1rem">
          <span style="font-size:1.3rem">&#127969;</span>
          <span>Griffith's Valuation</span>
        </label>
        <div style="margin-top:.3rem;font-size:.85rem;color:#444;line-height:1.35">
          A list from the <strong>1850s</strong> of who rented or owned every bit of land in Ireland.
          Great for finding your great-great-grandparents' farm.
        </div>
        <div style="margin-top:.5rem;font-size:.75rem;color:#888;font-style:italic">
          Covers 1847&ndash;1864 &mdash; before any census you can read online today.
        </div>
      </div>

    </div>

    <div id="variants-wrap" style="margin-top:1rem">
      <label style="margin-top:0">Name variations <small style="font-weight:400;color:#666">(off by default — clearest search first)</small></label>
      <div id="variants-gate" style="font-size:.9rem;padding:.6rem .8rem;background:#fffbe6;border-left:3px solid #e0a800;border-radius:4px;color:#555">
        Pick one person above to enable name variations.
      </div>
      <div id="variants-body" style="display:none">
        <div style="font-size:.85rem;margin:.1rem 0 .5rem;color:#555">
          Tick to also search spelling/name variations. More variants = more site calls.
        </div>
        <div class="row">
          <label><input type="checkbox" id="opt-surnames">
            <strong>Surname spellings</strong></label>
          <label><input type="checkbox" id="opt-nicks">
            <strong>Forename variants</strong></label>
        </div>

        <div id="sur-panel" style="display:none;margin-top:.6rem;padding:.6rem .8rem;background:#fff;border:1px solid #e0d090;border-radius:4px">
          <div style="font-size:.8rem;color:#666;margin-bottom:.3rem">Surnames to search</div>
          <div id="sur-boxes"></div>
        </div>

        <div id="fore-panel" style="display:none;margin-top:.6rem;padding:.6rem .8rem;background:#fff;border:1px solid #e0d090;border-radius:4px">
          <div style="font-size:.8rem;color:#666;margin-bottom:.3rem">Forenames to search</div>
          <div id="fore-boxes"></div>
        </div>

        <div id="cost-panel" style="margin-top:.5rem;font-size:.85rem;color:#555"></div>
      </div>
    </div>

    <label for="csv" style="margin-top:1rem">Upload a different ancestors CSV <small style="font-weight:400;color:#666">(optional)</small></label>
    <input type="file" name="csv" id="csv" accept=".csv">

    <div class="row" style="margin-top:.8rem">
      <label><input type="checkbox" name="headless" checked> Headless (no visible browser)</label>
      <label><input type="checkbox" name="no_cache"> Ignore cache</label>
    </div>
  </div>

  <input type="hidden" name="only" id="only-hidden" value="">
  <div id="pick-hidden-fields"></div>
  <button type="submit" style="margin-top:.4rem">Start search</button>
</form>

<script>
const pick = document.getElementById('pick');
const only = document.getElementById('only-hidden');
const spMain = document.getElementById('sp-main');
const spExtra = document.getElementById('sp-extra');
const optSur = document.getElementById('opt-surnames');
const optNick = document.getElementById('opt-nicks');
const ySel = document.getElementById('census_year');
const yearGroup = document.getElementById('year-group');
function paintYearButtons() {
  yearGroup.querySelectorAll('.year-btn').forEach(btn => {
    const active = btn.dataset.v === ySel.value;
    btn.style.background = active ? '#4a90e2' : 'transparent';
    btn.style.color = active ? '#fff' : '#333';
    btn.style.fontWeight = active ? '600' : '400';
  });
}
yearGroup.querySelectorAll('.year-btn').forEach(btn => {
  btn.addEventListener('click', () => { ySel.value = btn.dataset.v; paintYearButtons(); renderPreview(); });
});
paintYearButtons();

const eSel = document.getElementById('ig_event');
const eventGroup = document.getElementById('event-group');
function paintEventButtons() {
  eventGroup.querySelectorAll('.ev-btn').forEach(btn => {
    const active = btn.dataset.v === eSel.value;
    btn.style.background = active ? '#4a90e2' : '#fff';
    btn.style.color = active ? '#fff' : '#333';
    btn.style.fontWeight = active ? '600' : '400';
    btn.style.borderColor = active ? '#4a90e2' : '#bcd';
  });
}
eventGroup.querySelectorAll('.ev-btn').forEach(btn => {
  btn.addEventListener('click', () => { eSel.value = btn.dataset.v; paintEventButtons(); updateCost(); renderPreview(); });
});
paintEventButtons();

// Fade site filter areas when their site is unticked
function paintSiteCards() {
  document.querySelectorAll('.site-card').forEach(card => {
    const cb = card.querySelector('input[name="use_site"]');
    card.style.opacity = cb && cb.checked ? '1' : '.5';
  });
}
document.querySelectorAll('input[name="use_site"]').forEach(cb => cb.addEventListener('change', paintSiteCards));
paintSiteCards();

const vGate = document.getElementById('variants-gate');
const vBody = document.getElementById('variants-body');
const surPanel = document.getElementById('sur-panel');
const forePanel = document.getElementById('fore-panel');
const surBoxes = document.getElementById('sur-boxes');
const foreBoxes = document.getElementById('fore-boxes');
const costPanel = document.getElementById('cost-panel');
const hiddenFields = document.getElementById('pick-hidden-fields');
let CACHE = {forenames: [], surnames: []};

const pickedSurnames = () => Array.from(surBoxes.querySelectorAll('input:checked')).map(i => i.value);
const pickedForenames = () => Array.from(foreBoxes.querySelectorAll('input:checked')).map(i => i.value);
const activeSites = () => Array.from(document.querySelectorAll('input[name="use_site"]:checked')).map(i => i.value);

function updateCost() {
  if (!pick.value) { costPanel.innerHTML = ''; return; }
  const surCount = optSur.checked ? Math.max(1, pickedSurnames().length) : 1;
  const foreCount = optNick.checked ? Math.max(1, pickedForenames().length) : 1;
  const yearCount = ySel.value ? 1 : 3;
  const sites = activeSites();
  const parts = [];
  let total = 0;
  if (sites.includes('nai_census')) { const n = surCount * foreCount * yearCount; parts.push(`${n} NAI`); total += n; }
  if (sites.includes('irishgenealogy_civil')) {
    // event-marriage matches both civil + church marriage rows (2 events); others match 1.
    const evMult = (eSel.value === '') ? 6 : (eSel.value === 'event-marriage' ? 2 : 1);
    const n = surCount * foreCount * evMult;
    parts.push(`${n} IrishGenealogy`); total += n;
  }
  if (sites.includes('griffiths')) { const n = surCount * foreCount; parts.push(`${n} Griffith\\'s`); total += n; }
  const colour = total > 50 ? '#b00' : (total > 20 ? '#a60' : '#060');
  const warn = total > 20 ? ' &mdash; polite 5-10s pauses between each' : '';
  costPanel.innerHTML = `<span style="color:${colour}">&#9888; ~${total} site calls (${parts.join(', ')})</span>${warn}`;
}

function renderVariantBoxes(container, items, primary, defaultTicked) {
  container.innerHTML = '';
  (items || []).forEach(name => {
    const isPrimary = (name || '').toLowerCase() === (primary || '').toLowerCase();
    const checked = isPrimary || defaultTicked;
    container.insertAdjacentHTML('beforeend',
      `<label style="display:inline-block;margin:.1rem .6rem .1rem 0;font-weight:400">
         <input type="checkbox" value="${name}" ${checked?'checked':''} ${isPrimary?'disabled':''}>
         ${name}${isPrimary?' <small style="color:#888">(primary)</small>':''}
       </label>`);
  });
  container.querySelectorAll('input').forEach(i => i.addEventListener('change', () => { updateCost(); syncHidden(); renderPreview(); }));
}

async function loadCandidates() {
  const opt = pick.options[pick.selectedIndex];
  if (!opt || !pick.value) { CACHE = {forenames:[], surnames:[]}; return; }
  const fore = opt.dataset.fore || '';
  const sur  = opt.dataset.sur  || '';
  const vars_ = opt.dataset.var  || '';
  const q = `forename=${encodeURIComponent(fore)}&surname=${encodeURIComponent(sur)}&variants=${encodeURIComponent(vars_)}`;
  const d = await (await fetch(`{{ url_for('api_preview') }}?${q}`)).json();
  CACHE.forenames = d.forenames || [fore];
  CACHE.surnames = (d.surnames || []).map(s => s.name);
  if (!CACHE.surnames.includes(sur)) CACHE.surnames.unshift(sur);
  renderVariantBoxes(surBoxes, CACHE.surnames, sur, true);
  renderVariantBoxes(foreBoxes, CACHE.forenames, fore, false);
}

function syncHidden() {
  hiddenFields.innerHTML = '';
  if (!pick.value) return;
  const add = (n, v) => {
    const h = document.createElement('input');
    h.type = 'hidden'; h.name = n; h.value = v;
    hiddenFields.appendChild(h);
  };
  if (optSur.checked) { pickedSurnames().forEach(v => add('surname_pick', v)); add('surnames','1'); }
  if (optNick.checked) { pickedForenames().forEach(v => add('forename_pick', v)); add('nicks','1'); }
}

async function renderPreview() {
  const opt = pick.options[pick.selectedIndex];
  only.value = pick.value;

  if (!pick.value) {
    spMain.innerHTML = '<em style="color:#888">{All} — every person in your list will be searched.</em>';
    spExtra.innerHTML = '';
    vGate.style.display = 'block'; vBody.style.display = 'none';
    optSur.checked = false; optNick.checked = false;
    surPanel.style.display = 'none'; forePanel.style.display = 'none';
    syncHidden(); updateCost();
    return;
  }

  vGate.style.display = 'none'; vBody.style.display = 'block';

  const fore = opt.dataset.fore || '';
  const sur  = opt.dataset.sur  || '';
  const year = opt.dataset.year || '';
  const county = opt.dataset.county || '';
  const townland = opt.dataset.townland || '';

  let main = `<strong>${fore}</strong> <strong>${sur}</strong>`;
  const bits = [];
  if (year) bits.push(`born c.${year}`);
  if (townland) bits.push(townland);
  if (county) bits.push(`Co. ${county}`);
  if (bits.length) main += ` <span style="color:#555">— ${bits.join(', ')}</span>`;
  spMain.innerHTML = main;

  const yr = ySel.value || 'All years (1901, 1911, 1926)';
  const lines = [`Census year: <strong>${yr}</strong>`];
  if (optSur.checked) lines.push(`Surnames: <strong>${pickedSurnames().join(', ') || '(none ticked)'}</strong>`);
  if (optNick.checked) lines.push(`Forenames: <strong>${pickedForenames().join(', ') || '(none ticked)'}</strong>`);
  if (!optSur.checked && !optNick.checked) lines.push('<span style="color:#060">Exact names only — no variations.</span>');
  spExtra.innerHTML = lines.map(l => `<div>${l}</div>`).join('');

  syncHidden(); updateCost();
}

async function onPickChange() {
  if (pick.value) await loadCandidates();
  renderPreview();
}

const sortGroup = document.getElementById('sort-group');
const sortSel = { value: 'sur' };
function paintSortButtons() {
  sortGroup.querySelectorAll('.sort-btn').forEach(btn => {
    const active = btn.dataset.v === sortSel.value;
    btn.style.background = active ? '#4a90e2' : 'transparent';
    btn.style.color = active ? '#fff' : '#333';
    btn.style.fontWeight = active ? '600' : '400';
  });
}
sortGroup.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => { sortSel.value = btn.dataset.v; paintSortButtons(); applySort(); });
});
const everyoneOpt = pick.querySelector('option[value=""]');
const peopleOpts = Array.from(pick.querySelectorAll('option')).filter(o => o.value);
function optionLabel(o, mode) {
  const fore = o.dataset.fore || '';
  const sur  = o.dataset.sur  || '';
  const yr   = o.dataset.year || '';
  const tl   = o.dataset.townland || '';
  const co   = o.dataset.county || '';
  const head = (mode === 'sur') ? (sur + ', ' + fore) : (fore + ' ' + sur);
  const bits = [];
  if (yr) bits.push('c.' + yr);
  if (tl) bits.push(tl);
  if (co) bits.push('Co. ' + co);
  return head.trim() + (bits.length ? ' — ' + bits.join(', ') : '');
}
function applySort() {
  const mode = sortSel.value;
  let sorted = peopleOpts.slice();
  if (mode === 'full') {
    sorted.sort((a, b) => (a.dataset.fore + ' ' + a.dataset.sur).localeCompare(b.dataset.fore + ' ' + b.dataset.sur));
  } else if (mode === 'sur') {
    sorted.sort((a, b) => (a.dataset.sur + ' ' + a.dataset.fore).localeCompare(b.dataset.sur + ' ' + b.dataset.fore));
  } else {
    sorted.sort((a, b) => (+a.dataset.idx) - (+b.dataset.idx));
  }
  const cur = pick.value;
  pick.innerHTML = '';
  pick.appendChild(everyoneOpt);
  sorted.forEach(o => { o.textContent = optionLabel(o, mode); pick.appendChild(o); });
  pick.value = cur;
}
paintSortButtons();
applySort();

optSur.addEventListener('change', () => {
  surPanel.style.display = optSur.checked ? 'block' : 'none';
  renderPreview();
});
optNick.addEventListener('change', () => {
  forePanel.style.display = optNick.checked ? 'block' : 'none';
  renderPreview();
});
pick.addEventListener('change', onPickChange);
document.querySelectorAll('input[name="use_site"]').forEach(cb => cb.addEventListener('change', updateCost));
onPickChange();
</script>
{% else %}
<div class="state">
  <strong>State:</strong> <span id="st-state">{{ run.state }}</span><br>
  <strong>Progress:</strong> <span id="st-current">{{ run.current }}</span> / <span id="st-total">{{ run.total }}</span>
  &mdash; <span id="st-label">{{ run.label }}</span>
  <div class="bar"><div id="st-bar" style="width: {{ (100 * run.current / run.total) if run.total else 0 }}%"></div></div>
  <div style="margin-top:.4rem">
    <strong>Now searching:</strong> <code id="st-step">{{ run.step or '—' }}</code>
  </div>
  <div style="margin-top:.4rem">
    <strong>Records so far:</strong> <span id="st-live">{{ run.live_records }}</span>
    &nbsp;|&nbsp; <strong>Last site:</strong> <span id="st-site">{{ run.last_site or '—' }}</span>
  </div>
  <div id="st-debug" style="margin-top:.4rem;font-size:.75rem;color:#888;font-family:monospace"></div>
  <div id="st-pause" style="display:none;margin-top:.6rem;padding:.5rem .7rem;background:#fff6d8;border-left:3px solid #e0a800;border-radius:4px">
    <span style="font-size:1.1rem">&#9203;</span>
    <strong>Being polite:</strong> waiting
    <strong id="st-pause-remain">0.0</strong>s before next request to
    <code id="st-pause-host">—</code>
    <div class="bar" style="margin-top:.3rem;height:6px"><div id="st-pause-bar" style="width:0%;background:#e0a800"></div></div>
    <small style="color:#555">5–10s per host so we don't get blocked.</small>
  </div>
  {% if run.started %}<div><small>Started {{ run.started }}</small></div>{% endif %}
  <div><small id="st-finished">{% if run.finished %}Finished {{ run.finished }}{% endif %}</small></div>
  <div id="st-error" style="color:#b00">{% if run.error %}<strong>Error:</strong> {{ run.error }}{% endif %}</div>

  <details open style="margin-top:.8rem">
    <summary>Live log</summary>
    <pre id="st-log" style="background:#111;color:#bbb;padding:.8rem;border-radius:4px;max-height:420px;overflow:auto;font-size:1rem;line-height:1.35;margin:.4rem 0 0;white-space:normal;word-break:break-word">Waiting to start…</pre>
  </details>

  <div id="st-done" style="display:{{ 'block' if run.state == 'done' else 'none' }}">
    <div><strong><span id="st-rc">{{ run.record_count }}</span></strong> unique record(s).
         <span id="st-new">{{ run.new_count }}</span> new since previous run.</div>
    <a class="btn" href="{{ url_for('narrative') }}" target="_blank">What we found</a>
    <a class="btn" href="{{ url_for('results_html') }}" target="_blank">Open results.html</a>
    <a class="btn" href="{{ url_for('download', name='results.csv') }}">Download CSV</a>
    <a class="btn" href="{{ url_for('download', name='results.json') }}">Download JSON</a>
    <a class="btn" href="{{ url_for('download', name='run.log') }}">run.log</a>
    <a class="btn" href="{{ url_for('download', name='confirmations.json') }}">confirmations.json</a>
  </div>

  <form id="st-reset" method="post" action="{{ url_for('reset') }}" style="margin-top:1rem;display:{{ 'block' if run.state in ('done','error') else 'none' }}">
    <button type="submit">New run</button>
  </form>
</div>

<script>
let PAUSE = null;  // {host, seconds, until_client_ms}
function updatePause(s) {
  const dbg = document.getElementById('st-debug');
  if (dbg) dbg.textContent = `dbg: state=${s.state} host=${s.pause_host||'-'} secs=${s.pause_seconds||0} until=${s.pause_until||0} now=${s.server_now||0} remain=${((s.pause_until||0)-(s.server_now||0)).toFixed(2)}`;
  if (s.state === 'running' && s.pause_until && s.server_now && s.pause_until > s.server_now) {
    const remainServer = s.pause_until - s.server_now;
    PAUSE = {
      host: s.pause_host || '—',
      seconds: s.pause_seconds || remainServer,
      until: Date.now() + remainServer * 1000,
    };
  } else {
    PAUSE = null;
  }
  renderPause();
}
function renderPause() {
  const panel = document.getElementById('st-pause');
  if (!PAUSE) { panel.style.display = 'none'; return; }
  const remain = Math.max(0, (PAUSE.until - Date.now()) / 1000);
  if (remain <= 0) { panel.style.display = 'none'; PAUSE = null; return; }
  panel.style.display = 'block';
  document.getElementById('st-pause-remain').textContent = remain.toFixed(1);
  document.getElementById('st-pause-host').textContent = PAUSE.host;
  const pct = PAUSE.seconds > 0 ? (100 * (PAUSE.seconds - remain) / PAUSE.seconds) : 100;
  document.getElementById('st-pause-bar').style.width = Math.min(100, pct) + '%';
}
setInterval(renderPause, 100);

async function tick() {
  try {
    const [s, l] = await Promise.all([
      fetch('{{ url_for("status_json") }}').then(r => r.json()),
      fetch('{{ url_for("log_json") }}').then(r => r.json()),
    ]);
    document.getElementById('st-state').textContent = s.state;
    document.getElementById('st-current').textContent = s.current;
    document.getElementById('st-total').textContent = s.total;
    document.getElementById('st-label').textContent = s.label || '—';
    document.getElementById('st-live').textContent = s.live_records;
    document.getElementById('st-site').textContent = s.last_site || '—';
    const stepEl = document.getElementById('st-step'); if (stepEl) stepEl.textContent = s.step || '—';
    document.getElementById('st-bar').style.width = (s.total ? (100 * s.current / s.total) : 0) + '%';
    document.getElementById('st-finished').textContent = s.finished ? ('Finished ' + s.finished) : '';
    document.getElementById('st-error').innerHTML = s.error ? ('<strong>Error:</strong> ' + s.error) : '';
    (function(){
      const logEl = document.getElementById('st-log');
      const lines = l.lines || [];
      if (!lines.length) { logEl.textContent = 'Waiting to start…'; return; }
      const esc = t => t.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
      const IG_PHRASE = {
        'Civil-Birth': 'government birth records',
        'Civil-Marriage': 'government marriage records',
        'Civil-Death': 'government death records',
        'Church-Baptism': 'church baptism records',
        'Church-Marriage': 'church marriage records',
        'Church-Burial': 'church burial records',
      };
      const prettySite = raw => {
        let m;
        if ((m = raw.match(/\\[NAI (\\d+)\\]/))) return 'old census books (' + m[1] + ')';
        if ((m = raw.match(/\\[IG ([^\\]]+)\\]/))) {
          const key = m[1];
          return IG_PHRASE[key] || key.replace(/-/g,' ').toLowerCase() + ' records';
        }
        if (/\\[Griff|\\[griffiths\\]/.test(raw)) return "old land records";
        return '';
      };
      const stripUrls = t => t.replace(/https?:\\/\\/\\S+/g, '').replace(/\\s+/g,' ').trim();
      const items = lines.map(raw => {
        if (/\\[(nai_census|irishgenealogy_civil|irishgenealogy_church|griffiths)\\]/.test(raw)) return '';
        if (/Incremental save|storage_state|cookies|polite_pause|safe_goto|cache key|\\.search_cache/i.test(raw)) return '';
        const site = prettySite(raw);
        let parsed = raw.match(/parsed (\\d+) rows/);
        let cached = raw.match(/cache hit:.*\\((\\d+) records\\)/);
        let generic = raw.match(/\\] (\\d+) records for/);
        const zero = /\\b0 records\\b/.test(raw) || /no results/i.test(raw);
        const hits = parsed ? +parsed[1] : (cached ? +cached[1] : (generic ? +generic[1] : (zero ? 0 : -1)));
        const who = (raw.match(/for ([A-Z][^\\n,]+?)(?:\\s+\\(|$)/) || [,''])[1];
        const whoBit = who ? ' <span style="color:#9cf">' + esc(who) + '</span>' : '';
        const fromCache = !!cached;
        if (hits > 0) {
          const cacheTag = fromCache ? ' <span style="color:#9c9;font-weight:400;font-size:.9rem">(remembered from earlier)</span>' : '';
          return '<div style="background:#063;color:#dfd;padding:.4rem .6rem;margin:.25rem 0;border-radius:4px;font-weight:700;font-size:1.1rem">&#127881; Found ' + hits + ' record' + (hits===1?'':'s') + ' &mdash; ' + esc(site || 'search') + whoBit + cacheTag + '</div>';
        }
        if (hits === 0) {
          return '<div style="color:#888;padding:.15rem .6rem">&#128270; Nothing here &mdash; ' + esc(site || 'search') + whoBit + '</div>';
        }
        let pm = raw.match(/Pausing ([\\d.]+) seconds(?:.*host=([^)]+))?/);
        if (pm) {
          return '<div style="color:#e0a800;padding:.15rem .6rem">&#9203; Being polite &mdash; wait ' + Math.round(+pm[1]) + 's' + (pm[2]?' before next '+esc(pm[2]):'') + '</div>';
        }
        if (/Searching|Loading|goto/i.test(raw) && site) {
          return '<div style="color:#cfc;padding:.15rem .6rem">&#128269; Looking in ' + esc(site) + whoBit + '…</div>';
        }
        if (/ERROR|failed/i.test(raw)) {
          const cleaned = stripUrls(raw.replace(/^[\\d\\-: ]+/, '').replace(/\\[[^\\]]+\\]/g,'').replace(/safe_goto|polite_pause/g,''));
          return '<div style="color:#f99;padding:.15rem .6rem">&#9888; Something went wrong &mdash; ' + esc(cleaned) + '</div>';
        }
        if (/CAPTCHA|captcha/.test(raw)) {
          return '<div style="background:#602;color:#fdd;padding:.4rem .6rem;margin:.25rem 0;border-radius:4px;font-weight:700">&#9940; CAPTCHA &mdash; solve in browser then press Enter in terminal</div>';
        }
        return '';
      }).filter(Boolean);
      logEl.innerHTML = items.slice(-80).join('');
      logEl.scrollTop = logEl.scrollHeight;
    })();
    updatePause(s);
    const doneBlock = document.getElementById('st-done');
    const resetBlock = document.getElementById('st-reset');
    if (s.state === 'done') {
      doneBlock.style.display = 'block';
      resetBlock.style.display = 'block';
      document.getElementById('st-rc').textContent = s.record_count;
      document.getElementById('st-new').textContent = s.new_count;
    } else if (s.state === 'error') {
      doneBlock.style.display = 'none';
      resetBlock.style.display = 'block';
    } else {
      doneBlock.style.display = 'none';
      resetBlock.style.display = 'none';
    }
    if (s.state === 'running') setTimeout(tick, 2000);
  } catch (e) { setTimeout(tick, 3000); }
}
tick();
</script>
{% endif %}
"""


def _progress(current: int, total: int, label: str) -> None:
    with RUN_LOCK:
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


import re as _re
_STEP_RE = _re.compile(r"\[(NAI \d+|IG [^\]]+|Griff[^\]]*)\]")


class _StepHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        m = _STEP_RE.search(msg)
        if not m:
            return
        tag = m.group(1)
        if " -> " in msg or "cache hit" in msg:
            with RUN_LOCK:
                RUN["step"] = tag


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
    except Exception as exc:
        logging.getLogger("genealogy").exception("Web run failed: %s", exc)
        with RUN_LOCK:
            RUN["state"] = "error"
            RUN["error"] = str(exc)
            RUN["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.route("/")
def index():
    try:
        with PEOPLE_LOCK:
            people_rows = _read_rows()
    except Exception:
        people_rows = []
    sites = [{"key": k, "name": v.get("name", k)} for k, v in SITES.items()]
    return render_template_string(INDEX_HTML, run=RUN, people=people_rows, sites=sites)


@app.route("/start", methods=["POST"])
def start():
    with RUN_LOCK:
        if RUN["state"] == "running":
            return redirect(url_for("index"))
        # reset
        RUN.update({
            "state": "running",
            "current": 0, "total": 0, "label": "",
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished": None, "error": "",
            "new_count": 0, "record_count": 0,
        })

    upload = request.files.get("csv")
    if upload and upload.filename:
        upload.save(UPLOAD_CSV)

    if not UPLOAD_CSV.exists():
        with RUN_LOCK:
            RUN["state"] = "error"
            RUN["error"] = "No ancestors.csv found and none uploaded."
        return redirect(url_for("index"))

    want_surnames = bool(request.form.get("surnames"))
    want_nicks = bool(request.form.get("nicks"))
    surname_picks = [s.strip() for s in request.form.getlist("surname_pick") if s.strip()]
    forename_picks = [s.strip() for s in request.form.getlist("forename_pick") if s.strip()]
    # Explicit picks override: don't let Soundex add extra surnames beyond what
    # the user ticked. Only turn on Soundex expansion when user ticked
    # "surnames" without specifying any picks (i.e. legacy sweep).
    RUNTIME_FLAGS["soundex"] = want_surnames and not surname_picks
    RUNTIME_FLAGS["collapse_weak"] = True

    year = (request.form.get("census_year") or "").strip()
    scrape_settings.CENSUS_YEAR_FILTER = year if year in {"1901", "1911", "1926"} else ""

    ev = (request.form.get("ig_event") or "").strip()
    valid_events = {"event-birth", "event-marriage", "event-death",
                    "event-baptism", "event-burial"}
    scrape_settings.IG_EVENT_FILTER = (ev,) if ev in valid_events else ()

    used = set(request.form.getlist("use_site"))
    skip_keys = [k for k in SITES.keys() if k not in used] if used else []
    opts = {
        "headless": bool(request.form.get("headless")),
        "no_cache": bool(request.form.get("no_cache")),
        "use_surnames": want_surnames,
        "use_nicks": want_nicks,
        "surname_picks": surname_picks,
        "forename_picks": forename_picks,
        "only": request.form.get("only", ""),
        "skip": ",".join(skip_keys),
    }
    app.logger.warning("START opts=%r form_keys=%r", opts, list(request.form.keys()))

    threading.Thread(target=_worker, args=(opts,), daemon=True).start()
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    with RUN_LOCK:
        if RUN["state"] == "running":
            return redirect(url_for("index"))
        RUN.update({
            "state": "idle",
            "current": 0, "total": 0, "label": "",
            "started": None, "finished": None, "error": "",
            "new_count": 0, "record_count": 0,
        })
    return redirect(url_for("index"))


TIER_ORDER = ["Likely", "Possible", "Weak", "Manual"]
TIER_STARS = {"Likely": "★★★", "Possible": "★★", "Weak": "★", "Manual": "🔍"}
TIER_BLURB = {
    "Likely": "Looks like a strong match.",
    "Possible": "Could be them — worth a look.",
    "Weak": "Shaky match. Check carefully.",
    "Manual": "Needs a human eye.",
}
TIER_COLOR = {
    "Likely": "#0a7d2b", "Possible": "#b57b00",
    "Weak": "#9a6f6f", "Manual": "#555",
}


def _narrate(rec: dict) -> str:
    """Natural sentence describing one record."""
    name = (rec.get("name_found") or "").strip() or "Someone"
    rtype = (rec.get("record_type") or "").strip()
    place = (rec.get("place") or "").strip()
    year = (rec.get("age_or_year") or "").strip()
    site = (rec.get("site") or "").strip()
    bits = [f"<strong>{name}</strong>"]
    if rtype:
        bits.append(f"in a {rtype.lower()} record")
    if year:
        bits.append(f"({year})")
    if place:
        bits.append(f"at {place}")
    tail = f"— from {site}" if site else ""
    return " ".join(bits) + (" " + tail if tail else "") + "."


NARRATIVE_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>What we found</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color:#222; }
  h1 { margin-bottom: .2rem; }
  .sub { color:#666; margin-bottom:1.5rem; }
  .person { margin: 2rem 0; }
  .person h2 { border-bottom: 2px solid #eee; padding-bottom:.3rem; }
  .tier { margin: 1rem 0; }
  .tier-head { font-weight: 700; font-size:1.05rem; margin-bottom:.4rem; }
  .card { background:#f7f7f9; border-left:4px solid #ccc; padding:.6rem .9rem; margin:.4rem 0; border-radius:4px; }
  .stars { font-size:1.1rem; margin-right:.4rem; }
  .blurb { color:#555; font-size:.9rem; margin-bottom:.4rem; }
  a.src { color:#06c; font-size:.9rem; margin-left:.5rem; }
  .nav { margin-bottom:1rem; }
  .nav a { margin-right:1rem; }
  .empty { color:#888; font-style:italic; }
</style>
<div class="nav">
  <a href="{{ url_for('index') }}">← Back</a>
  <a href="{{ url_for('results_html') }}">Full table</a>
</div>
<h1>What we found</h1>
<div class="sub">Generated {{ generated }} · {{ total }} record(s) across {{ people|length }} ancestor(s).</div>
{% if not people %}
  <p class="empty">No records yet. Run a search first.</p>
{% endif %}
{% for p in people %}
  <section class="person">
    <h2>{{ p.person }}</h2>
    {% for tier in tier_order %}
      {% set recs = p.tiers.get(tier, []) %}
      {% if recs %}
        <div class="tier">
          <div class="tier-head" style="color:{{ tier_color[tier] }}">
            <span class="stars">{{ tier_stars[tier] }}</span>{{ tier }} ({{ recs|length }})
          </div>
          <div class="blurb">{{ tier_blurb[tier] }}</div>
          {% for r in recs %}
            <div class="card" style="border-left-color:{{ tier_color[tier] }}">
              {{ r.sentence|safe }}
              {% if r.link %}<a class="src" href="{{ r.link }}" target="_blank">open source ↗</a>{% endif %}
              {% if r.status == 'confirmed' %}<span style="color:#0a7d2b;font-weight:700">✓ confirmed</span>{% endif %}
            </div>
          {% endfor %}
        </div>
      {% endif %}
    {% endfor %}
  </section>
{% endfor %}
"""


@app.route("/narrative")
def narrative():
    """Kid-friendly 'What we found' panel: cards grouped by confidence tier."""
    import json as _json
    p = HERE / RESULTS_JSON
    if not p.exists():
        return render_template_string(
            NARRATIVE_HTML, generated="—", total=0, people=[],
            tier_order=TIER_ORDER, tier_stars=TIER_STARS,
            tier_blurb=TIER_BLURB, tier_color=TIER_COLOR,
        )
    try:
        payload = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        payload = {"generated": "—", "total": 0, "people": []}
    people_out = []
    for entry in payload.get("people", []):
        tiers: dict[str, list[dict]] = {t: [] for t in TIER_ORDER}
        for rec in entry.get("records", []):
            if rec.get("status") == "rejected":
                continue
            tier = rec.get("tier") or "Manual"
            if tier not in tiers:
                tiers[tier] = []
            tiers[tier].append({
                "sentence": _narrate(rec),
                "link": rec.get("link", ""),
                "status": rec.get("status", ""),
            })
        if any(tiers[t] for t in TIER_ORDER):
            people_out.append({"person": entry.get("person", "(unknown)"), "tiers": tiers})
    return render_template_string(
        NARRATIVE_HTML,
        generated=payload.get("generated", "—"),
        total=payload.get("total", 0),
        people=people_out,
        tier_order=TIER_ORDER, tier_stars=TIER_STARS,
        tier_blurb=TIER_BLURB, tier_color=TIER_COLOR,
    )


@app.route("/results.html")
def results_html():
    p = HERE / RESULTS_HTML
    if not p.exists():
        abort(404)
    return send_file(p)


@app.route("/download/<name>")
def download(name: str):
    allowed = {RESULTS_CSV, RESULTS_JSON, RESULTS_HTML, LOG_FILE, RESULTS_SUMMARY,
               "confirmations.json"}
    if name not in allowed:
        abort(404)
    p = HERE / name
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True)


@app.route("/confirm", methods=["POST"])
def confirm() -> Response:
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    status = (data.get("status") or "").strip()
    person = (data.get("person") or "").strip()
    if not key:
        return Response("missing key", status=400)
    if status and status not in VALID_STATUSES:
        return Response("bad status", status=400)
    with CONFIRMS_LOCK:
        confs = load_confirmations(CONFIRMS_FILE)
        set_status(confs, key, status, person)
        save_confirmations(CONFIRMS_FILE, confs)
    _rerender_results_html(confs)
    return Response(status=204)


def _rerender_results_html(confs: dict[str, dict]) -> None:
    """Re-render results.html with current confirmations so colours persist
    across page reloads without a fresh run."""
    results_csv_path = HERE / RESULTS_CSV
    if not results_csv_path.exists():
        return
    try:
        records = load_records_csv(results_csv_path)
        if not records:
            return
        apply_confirmations(records, confs)
        write_html(records, HERE / RESULTS_HTML)
    except Exception:
        logging.getLogger("genealogy").exception("Re-render after /confirm failed")


@app.route("/status.json")
def status_json() -> Response:
    with RUN_LOCK:
        snap = dict(RUN)
    snap["server_now"] = time.time()
    return Response(
        __import__("json").dumps(snap, default=str),
        mimetype="application/json",
    )


@app.route("/log.json")
def log_json() -> Response:
    """Return the last ~15 lines of run.log for live display."""
    import json as _json
    p = HERE / LOG_FILE
    lines: list[str] = []
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = raw[-15:]
        except Exception:
            lines = []
    return Response(_json.dumps({"lines": lines}), mimetype="application/json")


@app.route("/api/preview")
def api_preview() -> Response:
    fore = (request.args.get("forename") or "").strip()
    sur_raw = (request.args.get("surname") or "").strip()
    variants_raw = (request.args.get("variants") or "").strip()
    parts: list[str] = []
    for chunk in (sur_raw, variants_raw):
        for s in chunk.replace(",", ";").split(";"):
            s = s.strip()
            if s and s.lower() not in {p.lower() for p in parts}:
                parts.append(s)
    surnames = [{"name": s, "soundex": soundex(s)} for s in parts]
    codes = {s["soundex"] for s in surnames if s["soundex"]}
    return jsonify({
        "forenames": expand_forename(fore) if fore else [],
        "surnames": surnames,
        "soundex_match": len(codes) == 1 and len(surnames) > 1,
        "surname": sur_raw,
        "soundex": soundex(sur_raw) if sur_raw else "",
    })


CSV_COLUMNS = [
    "surname", "forename", "surname_variants", "birth_year_approx",
    "year_range", "county", "townland", "townland_variants",
    "father", "mother", "spouse", "notes",
]
PEOPLE_LOCK = threading.Lock()


def _read_rows() -> list[dict]:
    if not UPLOAD_CSV.exists():
        return []
    with UPLOAD_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        return [{k: (r.get(k) or "") for k in CSV_COLUMNS} for r in csv.DictReader(f)]


def _write_rows(rows: list[dict]) -> None:
    with UPLOAD_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            cells = [str(r.get(k) or "") for k in CSV_COLUMNS]
            while cells and cells[-1] == "":
                cells.pop()
            w.writerow(cells)


PEOPLE_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>Ancestors</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 1200px; margin: 1.5rem auto; padding: 0 1rem; }
  table { border-collapse: collapse; width: 100%; font-size: .85rem; }
  th, td { border: 1px solid #ddd; padding: .3rem .4rem; text-align: left; vertical-align: top; }
  th { background: #f0f0f0; position: sticky; top: 0; }
  td input { width: 100%; border: 0; padding: .2rem; font: inherit; background: transparent; }
  td input:focus { background: #fffbd8; outline: 1px solid #4a90e2; }
  .del { color: #b00; background: none; border: 0; cursor: pointer; font-size: 1rem; }
  .add { padding: .4rem .8rem; margin: .8rem 0; cursor: pointer; }
  .chips { font-size: .75rem; color: #555; }
  .chip { display:inline-block; padding:.05rem .3rem; margin:.05rem; border-radius:8px; background:#e8f0ff; }
</style>

<h1>Ancestors <small style="font-weight:400"><a href="{{ url_for('index') }}">&larr; back</a></small></h1>
<p>Edits save automatically when a field loses focus. Columns: forename and surname drive the search. Separate multiple variants with <code>;</code>. Townland unknown is fine — leave it blank and the search just uses the county (e.g. Galway).</p>

<table id="tbl">
  <thead>
    <tr>
      {% for c in cols %}<th>{{ c }}</th>{% endfor %}
      <th>Preview</th><th></th>
    </tr>
  </thead>
  <tbody>
  {% for row in rows %}
    <tr data-i="{{ loop.index0 }}">
      {% for c in cols %}<td><input name="{{ c }}" value="{{ row[c] }}"></td>{% endfor %}
      <td class="chips"></td>
      <td><button class="del" title="Delete">&times;</button></td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<button class="add" id="add">+ Add person</button>

<script>
const COLS = {{ cols|tojson }};

function rowPreview(tr) {
  const fore = tr.querySelector('[name=forename]').value.trim();
  const sur = tr.querySelector('[name=surname]').value.trim();
  const vars_ = tr.querySelector('[name=surname_variants]').value.trim();
  const out = tr.querySelector('.chips');
  if (!fore && !sur && !vars_) { out.innerHTML = ''; return; }
  const q = `forename=${encodeURIComponent(fore)}&surname=${encodeURIComponent(sur)}&variants=${encodeURIComponent(vars_)}`;
  fetch(`{{ url_for('api_preview') }}?${q}`)
    .then(r => r.json())
    .then(d => {
      const parts = [];
      if (d.forenames && d.forenames.length > 1) {
        parts.push(d.forenames.slice(1).map(n => `<span class="chip">${n}</span>`).join(''));
      }
      if (d.surnames && d.surnames.length) {
        parts.push(d.surnames.map(s => `<span class="chip" style="background:#ffe9c2">${s.name}&rarr;${s.soundex}</span>`).join(''));
        if (d.soundex_match) parts.push('<span style="color:#060">&#10003;</span>');
        else if (d.surnames.length > 1) parts.push('<span style="color:#b00">&#9888;</span>');
      }
      out.innerHTML = parts.join(' ');
    });
}

function rowToObj(tr) {
  const o = {};
  COLS.forEach(c => o[c] = tr.querySelector(`[name="${c}"]`).value);
  return o;
}

async function saveRow(tr) {
  const i = tr.dataset.i;
  const body = rowToObj(tr);
  await fetch(`{{ url_for('people_save', idx=0) }}`.replace('/0', '/' + i), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  rowPreview(tr);
}

async function deleteRow(tr) {
  const i = tr.dataset.i;
  await fetch(`{{ url_for('people_delete', idx=0) }}`.replace('/0', '/' + i), {method: 'POST'});
  location.reload();
}

async function addRow() {
  await fetch('{{ url_for('people_add') }}', {method: 'POST'});
  location.reload();
}

document.querySelectorAll('#tbl tbody tr').forEach(tr => {
  tr.querySelectorAll('input').forEach(inp => inp.addEventListener('change', () => saveRow(tr)));
  tr.querySelector('.del').addEventListener('click', () => deleteRow(tr));
  rowPreview(tr);
});
document.getElementById('add').addEventListener('click', addRow);
</script>
"""


@app.route("/people")
def people_page():
    with PEOPLE_LOCK:
        rows = _read_rows()
    return render_template_string(PEOPLE_HTML, rows=rows, cols=CSV_COLUMNS)


@app.route("/people/add", methods=["POST"])
def people_add() -> Response:
    with PEOPLE_LOCK:
        rows = _read_rows()
        rows.append({c: "" for c in CSV_COLUMNS})
        _write_rows(rows)
    return Response(status=204)


@app.route("/people/<int:idx>/save", methods=["POST"])
def people_save(idx: int) -> Response:
    data = request.get_json(silent=True) or {}
    with PEOPLE_LOCK:
        rows = _read_rows()
        if idx < 0 or idx >= len(rows):
            return Response("bad index", status=400)
        rows[idx] = {c: str(data.get(c, "")).strip() for c in CSV_COLUMNS}
        _write_rows(rows)
    return Response(status=204)


@app.route("/people/<int:idx>/delete", methods=["POST"])
def people_delete(idx: int) -> Response:
    with PEOPLE_LOCK:
        rows = _read_rows()
        if idx < 0 or idx >= len(rows):
            return Response("bad index", status=400)
        rows.pop(idx)
        _write_rows(rows)
    return Response(status=204)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
