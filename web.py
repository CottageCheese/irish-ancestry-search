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
    "person_name": "",    # first person being searched (shown in loading heading)
    "cancel_requested": False,
}
RUN_LOCK = threading.Lock()


INDEX_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>Irish genealogy search</title>
<style>
  :root {
    --cream:#f4ecd8; --cream-dark:#e8dcc0; --moss:#4a7c3a; --moss-dark:#1f4722;
    --sepia:#8b6f47; --gold:#c9a04a; --ink:#2b2416;
  }
  * { box-sizing: border-box; }
  body {
    font-family: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
    max-width: 900px; margin: 0 auto; padding: 1rem;
    background: var(--cream); color: var(--ink); min-height: 100vh;
  }
  h1 { font-size: 2.6rem; margin:.4rem 0 .2rem; color: var(--moss-dark); text-align:center; letter-spacing:.02em; }
  .tagline { text-align:center; color: var(--sepia); font-style:italic; margin:0 0 1.2rem; font-size:1.1rem; }
  .hero {
    background: #fffcf2; border: 2px solid var(--gold); border-radius: 12px;
    padding: 1.4rem 1.6rem; margin: 1rem 0; box-shadow: 0 4px 12px rgba(0,0,0,.08);
    position: relative;
  }
  .hero h2 { margin-top:0; color: var(--moss-dark); font-size:1.5rem; }
  label { display: block; margin:.9rem 0 .3rem; font-weight: 600; color: var(--moss-dark); }
  input[type=text], input[type=number], select {
    width: 100%; padding:.7rem; font: inherit; font-size: 1.05rem;
    border: 2px solid var(--cream-dark); border-radius: 6px; background:#fff;
  }
  input:focus, select:focus { outline: none; border-color: var(--moss); }
  .two-col { display:grid; grid-template-columns: 1fr 1fr; gap:.8rem; }
  @media (max-width:520px){ .two-col { grid-template-columns: 1fr; } }
  button.go {
    display:block; width:100%; margin-top:1.2rem; padding:.9rem;
    font: inherit; font-size:1.3rem; font-weight:700;
    background: var(--moss); color:#fff; border:0; border-radius:8px; cursor:pointer;
    box-shadow: 0 3px 0 var(--moss-dark); transition: transform .1s;
  }
  button.go:hover { background: var(--moss-dark); }
  button.go:active { transform: translateY(2px); box-shadow: 0 1px 0 var(--moss-dark); }
  .scene { margin: 1.4rem 0 .4rem; }
  .scene svg { width:100%; height:auto; display:block; border-radius:8px; }
  .hint { font-size:.9rem; color: var(--sepia); margin-top:.3rem; font-style:italic; }
  .state { margin: 1rem 0; padding: 1rem; background:#fffcf2; border:2px solid var(--gold); border-radius: 8px; }
  .bar { height: 10px; background: var(--cream-dark); border-radius: 5px; overflow: hidden; margin-top: .4rem; }
  .bar > div { height: 100%; background: var(--moss); transition: width .3s; }
  a.btn { display: inline-block; margin:.4rem .3rem 0 0; padding:.5rem 1rem; background: var(--moss); color: white; text-decoration: none; border-radius: 6px; font-weight:600; }
  a.btn:hover { background: var(--moss-dark); }
</style>

<h1>&#9752; Family Finder &#9752;</h1>
<p class="tagline">Find your Irish ancestors in old books and papers</p>

{% if run.state == 'idle' %}
<div class="hero">
  <h2>&#128269; Who do you want to find?</h2>
  <p style="margin:.2rem 0 0;color:#666">Type a name of someone in your family tree. We will search old Irish records for them.</p>

  <form method="post" action="{{ url_for('quick_search') }}">
    <div class="two-col">
      <div>
        <label for="forename">First name</label>
        <input type="text" id="forename" name="forename" required placeholder="e.g. Mary" autofocus>
      </div>
      <div>
        <label for="surname">Last name</label>
        <input type="text" id="surname" name="surname" required placeholder="e.g. Kelly">
      </div>
    </div>

    <div class="two-col">
      <div>
        <label for="county">County in Ireland</label>
        <select id="county" name="county">
          <option value="">(any county)</option>
          {% for c in counties %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
        </select>
      </div>
      <div>
        <label for="birth_year">Born around (year)</label>
        <input type="number" id="birth_year" name="birth_year" min="1700" max="1950" placeholder="e.g. 1875">
        <div class="hint">A guess is fine. We search 5 years either side.</div>
      </div>
    </div>

    <button type="submit" class="go" id="qs-btn">&#127808; Find them!</button>
    <div id="qs-spinner" style="display:none;margin-top:.8rem;text-align:center">
      <div class="sheep-spin" aria-hidden="true">&#128017;</div>
      <div style="color:var(--sepia);font-style:italic;margin-top:.3rem">Asking the old books…</div>
    </div>
  </form>
  <style>
    .sheep-spin { font-size:2.4rem; display:inline-block; animation: sheep-bounce 1s infinite ease-in-out; }
    @keyframes sheep-bounce { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-10px) } }
  </style>
  <script>
    (function(){
      const form = document.querySelector('form[action="{{ url_for('quick_search') }}"]');
      if (!form) return;
      form.addEventListener('submit', function(){
        const btn = document.getElementById('qs-btn');
        const sp  = document.getElementById('qs-spinner');
        if (btn) { btn.disabled = true; btn.textContent = 'Searching…'; }
        if (sp)  sp.style.display = 'block';
      });
    })();
  </script>

  <div class="scene" aria-hidden="true">
    <svg viewBox="0 0 800 200" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="sky" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#cfe6f2"/>
          <stop offset="100%" stop-color="#f4ecd8"/>
        </linearGradient>
        <linearGradient id="hill1" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#6ca84a"/>
          <stop offset="100%" stop-color="#4a7c3a"/>
        </linearGradient>
        <linearGradient id="hill2" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#8bbd5d"/>
          <stop offset="100%" stop-color="#5d8f3d"/>
        </linearGradient>
      </defs>
      <rect width="800" height="200" fill="url(#sky)"/>
      <path d="M0 150 Q200 80 400 130 T800 110 L800 200 L0 200 Z" fill="url(#hill1)"/>
      <path d="M0 170 Q150 130 350 160 T800 150 L800 200 L0 200 Z" fill="url(#hill2)"/>
      <g transform="translate(540,120)">
        <rect x="0" y="20" width="70" height="45" fill="#d9c9a0" stroke="#8b6f47" stroke-width="2"/>
        <polygon points="-5,20 35,-5 75,20" fill="#6b4a2a" stroke="#4a3220" stroke-width="2"/>
        <rect x="26" y="40" width="14" height="25" fill="#4a3220"/>
        <rect x="8" y="28" width="12" height="12" fill="#f4ecd8" stroke="#8b6f47"/>
        <rect x="50" y="28" width="12" height="12" fill="#f4ecd8" stroke="#8b6f47"/>
        <rect x="46" y="-5" width="8" height="18" fill="#8b6f47"/>
        <path d="M46 -3 Q48 -8 50 -5" fill="none" stroke="#999" stroke-width="2"/>
      </g>
      <g transform="translate(120,130)">
        <ellipse cx="0" cy="30" rx="30" ry="18" fill="#2f5a22"/>
        <ellipse cx="-15" cy="22" rx="20" ry="18" fill="#3a6b2a"/>
        <ellipse cx="15" cy="22" rx="20" ry="18" fill="#3a6b2a"/>
        <rect x="-4" y="30" width="8" height="18" fill="#5a3a20"/>
      </g>
      <g transform="translate(220,160)">
        <ellipse cx="0" cy="0" rx="14" ry="9" fill="#f5f0e4"/>
        <circle cx="-12" cy="-2" r="6" fill="#4a3a28"/>
        <circle cx="-14" cy="-4" r="1.5" fill="#000"/>
        <rect x="-6" y="7" width="3" height="5" fill="#4a3a28"/>
        <rect x="4" y="7" width="3" height="5" fill="#4a3a28"/>
      </g>
      <g transform="translate(300,170)">
        <ellipse cx="0" cy="0" rx="14" ry="9" fill="#f5f0e4"/>
        <circle cx="-12" cy="-2" r="6" fill="#4a3a28"/>
        <circle cx="-14" cy="-4" r="1.5" fill="#000"/>
        <rect x="-6" y="7" width="3" height="5" fill="#4a3a28"/>
        <rect x="4" y="7" width="3" height="5" fill="#4a3a28"/>
      </g>
      <circle cx="680" cy="40" r="22" fill="#f6d35e" opacity=".85"/>
    </svg>
  </div>
</div>

<div class="hero" style="background:#fff7e0">
  <h2 style="margin:0 0 .4rem;font-size:1.15rem">&#128214; What we search</h2>
  <div style="font-size:.95rem;line-height:1.5">
    <div>&#128203; <strong>The old census</strong> &mdash; who lived in which house in 1901, 1911 and 1926.</div>
    <div>&#128220; <strong>Births, marriages &amp; deaths</strong> &mdash; government and church records.</div>
    <div>&#127969; <strong>Griffiths Valuation</strong> &mdash; a list from the 1850s of who rented land.</div>
  </div>
</div>

{% set known = people | selectattr('forename') | list %}
{% if known %}
<style>
  .people-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:.6rem; margin-top:.6rem; }
  .person-btn {
    width:100%; text-align:left; background:#fffcf2; border:2px solid var(--cream-dark);
    border-radius:8px; padding:.65rem .8rem; cursor:pointer; font:inherit;
    transition:border-color .15s, background .15s;
  }
  .person-btn:hover { border-color:var(--gold); background:#fff8e6; }
  .person-btn:active { background:var(--cream-dark); }
  .person-name { display:block; font-weight:700; color:var(--moss-dark); font-size:1.05rem; }
  .person-sub { display:block; font-size:.85rem; color:var(--sepia); margin-top:.15rem; }
</style>
<div class="hero" style="margin-top:.6rem">
  <h2 style="margin:0 0 .2rem;font-size:1.2rem">&#128100; Search again for&hellip;</h2>
  <p style="margin:.1rem 0 .6rem;font-size:.9rem;color:#666">People already in your list &mdash; tap one to search.</p>
  <div class="people-grid" id="people-grid">
    {% for p in known %}
    <form method="post" action="{{ url_for('quick_search') }}" class="person-form" style="margin:0">
      <input type="hidden" name="forename" value="{{ p.forename }}">
      <input type="hidden" name="surname" value="{{ p.surname }}">
      <input type="hidden" name="county" value="{{ p.county }}">
      <input type="hidden" name="birth_year" value="{{ p.birth_year_approx }}">
      <button type="submit" class="person-btn">
        <span class="person-name">{{ p.forename }} {{ p.surname }}</span>
        <span class="person-sub">
          {%- if p.county %}{{ p.county }}{% endif %}
          {%- if p.birth_year_approx %} &middot; b.&nbsp;~{{ p.birth_year_approx }}{% endif -%}
        </span>
      </button>
    </form>
    {% endfor %}
  </div>
</div>
<script>
(function(){
  document.querySelectorAll('.person-form').forEach(function(form){
    form.addEventListener('submit', function(){
      const btn = form.querySelector('.person-btn');
      if (btn) { btn.disabled = true; btn.style.opacity = '.6'; }
    });
  });
})();
</script>
{% endif %}

{% else %}

<style>
  .loader-wrap { text-align:center; padding:1rem 0 2rem; }
  .loader-scene svg { width:100%; max-width:700px; height:auto; border-radius:12px; }
  .loader-heading { font-size:2rem; color:var(--moss-dark); margin:.8rem 0 .3rem; }
  .loader-msg { font-size:1.25rem; color:var(--sepia); font-style:italic; min-height:1.8em; margin:.2rem 0 .8rem; transition:opacity .4s; }
  .loader-progress-text { color:var(--sepia); font-size:1rem; margin-bottom:.4rem; }
  .loader-bar-wrap { max-width:500px; margin:0 auto .4rem; height:14px; background:var(--cream-dark); border-radius:7px; overflow:hidden; }
  .loader-bar-fill { height:100%; background:var(--moss); border-radius:7px; transition:width .4s; }
  .loader-pause { margin:.8rem auto; max-width:460px; padding:.5rem .8rem; background:#fff6d8; border-left:4px solid #e0a800; border-radius:6px; font-size:.95rem; display:none; }
  .sheep-big { font-size:3.5rem; display:inline-block; animation:sheep-bounce 1.1s infinite ease-in-out; }
  @keyframes sheep-bounce { 0%,100%{transform:translateY(0)}50%{transform:translateY(-14px)} }
</style>

<div class="loader-wrap" id="loader-screen" style="{{ 'display:none' if run.state != 'running' else '' }}">
  <div class="loader-scene" aria-hidden="true">
    <svg viewBox="0 0 800 200" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="lsky" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#cfe6f2"/><stop offset="100%" stop-color="#f4ecd8"/>
        </linearGradient>
        <linearGradient id="lh1" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#6ca84a"/><stop offset="100%" stop-color="#4a7c3a"/>
        </linearGradient>
        <linearGradient id="lh2" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#8bbd5d"/><stop offset="100%" stop-color="#5d8f3d"/>
        </linearGradient>
      </defs>
      <rect width="800" height="200" fill="url(#lsky)"/>
      <path d="M0 150 Q200 80 400 130 T800 110 L800 200 L0 200 Z" fill="url(#lh1)"/>
      <path d="M0 170 Q150 130 350 160 T800 150 L800 200 L0 200 Z" fill="url(#lh2)"/>
      <g transform="translate(540,120)">
        <rect x="0" y="20" width="70" height="45" fill="#d9c9a0" stroke="#8b6f47" stroke-width="2"/>
        <polygon points="-5,20 35,-5 75,20" fill="#6b4a2a" stroke="#4a3220" stroke-width="2"/>
        <rect x="26" y="40" width="14" height="25" fill="#4a3220"/>
        <rect x="8" y="28" width="12" height="12" fill="#f4ecd8" stroke="#8b6f47"/>
        <rect x="50" y="28" width="12" height="12" fill="#f4ecd8" stroke="#8b6f47"/>
        <rect x="46" y="-5" width="8" height="18" fill="#8b6f47"/>
        <path d="M46 -3 Q48 -8 50 -5" fill="none" stroke="#999" stroke-width="2"/>
      </g>
      <g transform="translate(120,130)">
        <ellipse cx="0" cy="30" rx="30" ry="18" fill="#2f5a22"/>
        <ellipse cx="-15" cy="22" rx="20" ry="18" fill="#3a6b2a"/>
        <ellipse cx="15" cy="22" rx="20" ry="18" fill="#3a6b2a"/>
        <rect x="-4" y="30" width="8" height="18" fill="#5a3a20"/>
      </g>
      <g transform="translate(230,163)">
        <ellipse cx="0" cy="0" rx="16" ry="10" fill="#f5f0e4"/>
        <circle cx="-14" cy="-2" r="7" fill="#4a3a28"/>
        <circle cx="-16" cy="-4" r="1.8" fill="#000"/>
        <rect x="-7" y="8" width="4" height="6" fill="#4a3a28"/>
        <rect x="5" y="8" width="4" height="6" fill="#4a3a28"/>
      </g>
      <g transform="translate(310,170)">
        <ellipse cx="0" cy="0" rx="13" ry="9" fill="#f5f0e4"/>
        <circle cx="-11" cy="-2" r="6" fill="#4a3a28"/>
        <circle cx="-13" cy="-4" r="1.5" fill="#000"/>
        <rect x="-5" y="7" width="3" height="5" fill="#4a3a28"/>
        <rect x="4" y="7" width="3" height="5" fill="#4a3a28"/>
      </g>
      <circle cx="680" cy="40" r="22" fill="#f6d35e" opacity=".85"/>
      <text x="400" y="70" text-anchor="middle" font-size="38" opacity=".7">📖</text>
    </svg>
  </div>

  <div class="sheep-big" aria-hidden="true">🐑</div>
  <h2 class="loader-heading">Looking for <span id="loader-name">{{ run.person_name or 'your ancestor' }}</span>…</h2>
  <div class="loader-msg" id="loader-msg">Asking the old books…</div>
  <div id="loader-step" style="font-size:.95rem;color:var(--moss-dark);margin:.1rem 0 .6rem;min-height:1.4em"></div>

  <div class="loader-progress-text">
    Checked <strong id="loader-current">{{ run.current }}</strong>
    of <strong id="loader-total">{{ run.total or '?' }}</strong> ancestors
  </div>
  <div class="loader-bar-wrap">
    <div class="loader-bar-fill" id="loader-bar" style="width:{{ (100 * run.current / run.total) if run.total else 0 }}%"></div>
  </div>

  <div class="loader-pause" id="loader-pause">
    ⏳ Being polite — waiting <strong id="loader-pause-remain">0</strong>s
  </div>

  <form id="cancel-form" style="margin-top:1.2rem">
    <button type="button" id="cancel-btn"
      style="background:none;border:1px solid var(--sepia);color:var(--sepia);padding:.4rem 1.1rem;border-radius:6px;cursor:pointer;font:inherit;font-size:.95rem"
      onclick="cancelSearch()">✕ Stop searching</button>
  </form>
</div>

<div class="state" id="status-panel" style="{{ 'display:none' if run.state == 'running' else '' }}">
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
    <button type="submit" class="go">&#128269; Try another name</button>
  </form>
</div>

<script>
const LOADER_MSGS = [
  'Asking the old books\u2026',
  'Checking the census from 1901\u2026',
  'Looking at church records\u2026',
  'Searching the 1911 census\u2026',
  'Flicking through old birth records\u2026',
  'Checking who lived nearby\u2026',
  'Looking at marriage records\u2026',
  'Searching the 1926 census\u2026',
  'Checking old land records\u2026',
  'Nearly there, keep going\u2026',
];
let _msgIdx = 0;
let _msgTimer = null;
function rotateMsg() {
  _msgIdx = (_msgIdx + 1) % LOADER_MSGS.length;
  const el = document.getElementById('loader-msg');
  if (!el) return;
  el.style.opacity = '0';
  setTimeout(() => { el.textContent = LOADER_MSGS[_msgIdx]; el.style.opacity = '1'; }, 400);
}

let PAUSE = null;
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
  const loaderPause = document.getElementById('loader-pause');
  if (!PAUSE) {
    if (panel) panel.style.display = 'none';
    if (loaderPause) loaderPause.style.display = 'none';
    return;
  }
  const remain = Math.max(0, (PAUSE.until - Date.now()) / 1000);
  if (remain <= 0) {
    if (panel) panel.style.display = 'none';
    if (loaderPause) loaderPause.style.display = 'none';
    PAUSE = null;
    return;
  }
  if (panel) {
    panel.style.display = 'block';
    document.getElementById('st-pause-remain').textContent = remain.toFixed(1);
    document.getElementById('st-pause-host').textContent = PAUSE.host;
    const pct = PAUSE.seconds > 0 ? (100 * (PAUSE.seconds - remain) / PAUSE.seconds) : 100;
    document.getElementById('st-pause-bar').style.width = Math.min(100, pct) + '%';
  }
  if (loaderPause) {
    loaderPause.style.display = 'block';
    const lpr = document.getElementById('loader-pause-remain');
    if (lpr) lpr.textContent = Math.ceil(remain);
  }
}
setInterval(renderPause, 100);

async function cancelSearch() {
  const btn = document.getElementById('cancel-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Stopping…'; }
  await fetch('{{ url_for("cancel") }}', {method: 'POST'});
}

async function tick() {
  try {
    const [s, l] = await Promise.all([
      fetch('{{ url_for("status_json") }}').then(r => r.json()),
      fetch('{{ url_for("log_json") }}').then(r => r.json()),
    ]);

    const loaderScreen = document.getElementById('loader-screen');
    const statusPanel = document.getElementById('status-panel');
    const loaderName = document.getElementById('loader-name');
    if (loaderName && s.person_name) loaderName.textContent = s.person_name;
    const lc = document.getElementById('loader-current'); if (lc) lc.textContent = s.current;
    const lt = document.getElementById('loader-total'); if (lt) lt.textContent = s.total || '?';
    const lb = document.getElementById('loader-bar'); if (lb) lb.style.width = (s.total ? (100*s.current/s.total) : 0) + '%';
    const ls = document.getElementById('loader-step');
    if (ls) {
      const SITE_LABELS = {'NAI 1901':'📜 Checking 1901 census…','NAI 1911':'📜 Checking 1911 census…','NAI 1926':'📜 Checking 1926 census…'};
      const step = s.step || '';
      ls.textContent = SITE_LABELS[step] || (step ? '🔍 Checking ' + step + '…' : '');
    }

    if (s.state === 'running') {
      if (loaderScreen) loaderScreen.style.display = '';
      if (statusPanel) statusPanel.style.display = 'none';
      if (!_msgTimer) _msgTimer = setInterval(rotateMsg, 3200);
    } else {
      if (loaderScreen) loaderScreen.style.display = 'none';
      if (statusPanel) statusPanel.style.display = '';
      if (_msgTimer) { clearInterval(_msgTimer); _msgTimer = null; }
    }

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
    except _CancelledError:
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


@app.route("/")
def index():
    try:
        with PEOPLE_LOCK:
            people_rows = _read_rows()
    except Exception:
        people_rows = []
    sites = [{"key": k, "name": v.get("name", k)} for k, v in SITES.items()]
    return render_template_string(INDEX_HTML, run=RUN, people=people_rows,
                                  sites=sites, counties=IRISH_COUNTIES)


IRISH_COUNTIES = [
    "Antrim", "Armagh", "Carlow", "Cavan", "Clare", "Cork", "Derry", "Donegal",
    "Down", "Dublin", "Fermanagh", "Galway", "Kerry", "Kildare", "Kilkenny",
    "Laois", "Leitrim", "Limerick", "Longford", "Louth", "Mayo", "Meath",
    "Monaghan", "Offaly", "Roscommon", "Sligo", "Tipperary", "Tyrone",
    "Waterford", "Westmeath", "Wexford", "Wicklow",
]


@app.route("/quick-search", methods=["POST"])
def quick_search():
    with RUN_LOCK:
        if RUN["state"] == "running":
            return redirect(url_for("index"))

    forename = (request.form.get("forename") or "").strip()
    surname = (request.form.get("surname") or "").strip()
    county = (request.form.get("county") or "").strip()
    birth_year = (request.form.get("birth_year") or "").strip()
    if not forename or not surname:
        return redirect(url_for("index"))

    with PEOPLE_LOCK:
        rows = _read_rows()
        key = (forename.lower(), surname.lower())
        if not any((r.get("forename", "").lower(), r.get("surname", "").lower()) == key for r in rows):
            new_row = {c: "" for c in CSV_COLUMNS}
            new_row["forename"] = forename
            new_row["surname"] = surname
            new_row["county"] = county
            new_row["birth_year_approx"] = birth_year
            new_row["year_range"] = "5" if birth_year else ""
            rows.append(new_row)
            _write_rows(rows)

    with RUN_LOCK:
        RUN.update({
            "state": "running",
            "current": 0, "total": 0, "label": "",
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished": None, "error": "",
            "new_count": 0, "record_count": 0,
            "person_name": f"{forename} {surname}".strip(),
            "cancel_requested": False,
        })

    RUNTIME_FLAGS["soundex"] = False
    RUNTIME_FLAGS["collapse_weak"] = True
    scrape_settings.CENSUS_YEAR_FILTER = ""
    scrape_settings.IG_EVENT_FILTER = ()

    opts = {
        "headless": True,
        "no_cache": False,
        "use_surnames": False,
        "use_nicks": True,
        "surname_picks": [],
        "forename_picks": [],
        "only": f"{forename} {surname}",
        "skip": "",
    }
    threading.Thread(target=_worker, args=(opts,), daemon=True).start()
    return redirect(url_for("index"))


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
            "person_name": "",
            "cancel_requested": False,
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


@app.route("/cancel", methods=["POST"])
def cancel():
    with RUN_LOCK:
        if RUN["state"] == "running":
            RUN["cancel_requested"] = True
    return Response(status=204)


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
            "person_name": "",
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
