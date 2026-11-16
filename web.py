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

import json
from genealogy_core.nicknames import expand_forename, NICKNAMES
from genealogy_core.render import slugify
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
    "current_query": "",  # e.g. "Furey William" — surname + forename variant in flight
    "person_name": "",    # first person being searched (shown in loading heading)
    "cancel_requested": False,
    "stopped": False,
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
  html {
    background:
      linear-gradient(180deg, rgba(0,155,58,.55) 0, rgba(0,155,58,.55) 6px,
                              rgba(255,255,255,.75) 6px, rgba(255,255,255,.75) 12px,
                              rgba(255,136,0,.55) 12px, rgba(255,136,0,.55) 18px,
                              transparent 18px),
      radial-gradient(1200px 500px at 50% -10%, #fff7df 0%, transparent 60%),
      radial-gradient(900px 600px at 10% 110%, #efe3c2 0%, transparent 55%),
      var(--cream);
    background-attachment: fixed;
  }
  body {
    font-family: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
    max-width: 900px; margin: 0 auto; padding: 1.4rem 1rem 1rem;
    color: var(--ink); min-height: 100vh;
    background: transparent;
  }
  .site-foot {
    text-align:center; margin: 1.6rem 0 .3rem; color: var(--sepia);
    font-style: italic; font-size:.95rem; letter-spacing:.02em;
  }
  .site-foot .glyph { color: var(--moss-dark); font-style: normal; margin: 0 .35rem; }
  h1 { font-size: 2.2rem; margin:.2rem 0 .1rem; color: var(--moss-dark); text-align:center; letter-spacing:.02em; }
  .tagline { text-align:center; color: var(--sepia); font-style:italic; margin:0 0 .5rem; font-size:1.05rem; }
  .hero {
    background: #fffcf2; border: 2px solid var(--gold); border-radius: 12px;
    padding: 1rem 1.3rem; margin: .4rem 0;
    box-shadow: 0 0 0 4px #fffcf2, 0 0 0 5px var(--sepia), 0 6px 14px rgba(0,0,0,.1);
    position: relative;
  }
  .hero::before, .hero::after {
    content:"\2619"; position:absolute; color: var(--gold); font-size:1.2rem;
    opacity:.75;
  }
  .hero::before { top:.3rem; left:.5rem; }
  .hero::after  { top:.3rem; right:.5rem; }
  .hero h2 { margin-top:0; margin-bottom:.25rem; color: var(--moss-dark); font-size:1.3rem; }
  label { display: block; margin:.55rem 0 .2rem; font-weight: 600; color: var(--moss-dark); font-size:1rem; }
  input[type=text], input[type=number], select {
    width: 100%; padding:.55rem .7rem; font: inherit; font-size: 1.05rem;
    border: 2px solid var(--cream-dark); border-radius: 6px; background:#fff;
  }
  input:focus, select:focus { outline: none; border-color: var(--moss); }
  .two-col { display:grid; grid-template-columns: 1fr 1fr; gap:.6rem; }
  @media (max-width:520px){ .two-col { grid-template-columns: 1fr; } }
  button.go {
    display:block; width:100%; margin-top:.8rem; padding:.7rem;
    font: inherit; font-size:1.2rem; font-weight:700;
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
  .family-link {
    display:inline-block; padding:.5rem 1.3rem;
    background:#fffcf2; border:2px solid var(--gold);
    border-radius:24px; font-size:1rem; font-weight:700;
    color:var(--moss-dark); text-decoration:none;
    transition:background .15s, border-color .15s, box-shadow .15s;
  }
  .family-link:hover { background:#fff3c0; border-color:var(--sepia); box-shadow:0 3px 10px rgba(0,0,0,.1); }
</style>

<h1>&#9752; Family Finder &#9752;</h1>
<p class="tagline">&mdash; <em>Fáilte</em> &mdash; hunting your Irish ancestors in the old books and papers &mdash;</p>

{% set known = people | selectattr('forename') | list %}
{% if known %}
<div style="text-align:center;margin:.4rem 0 .8rem">
  <a href="{{ url_for('ancestors_page') }}" class="family-link">
    &#128106; Our family so far ({{ known|length }}) &#8594;
  </a>
</div>
{% endif %}

{% if run.state == 'idle' %}
<div class="hero">
  <h2>&#128269; Who do you want to find?</h2>
  <p style="margin:.2rem 0 0;color:#666">Someone from your family who lived in Ireland. We will search old records to find them.</p>

  <form method="post" action="{{ url_for('quick_search') }}">
    <div class="two-col">
      <div style="position:relative">
        <label for="forename">&#128100; First name</label>
        <input type="text" id="forename" name="forename" required placeholder="e.g. Mary" autofocus>
        <div id="fore-variants" style="display:none;position:absolute;top:calc(100% + 10px);left:0;right:0;z-index:20;background:#fff;border:2px solid var(--moss);border-radius:10px;padding:.6rem .8rem;box-shadow:0 4px 14px rgba(74,124,58,.18)">
          <div aria-hidden="true" style="position:absolute;top:-8px;left:24px;width:14px;height:14px;background:#fff;border-left:2px solid var(--moss);border-top:2px solid var(--moss);transform:rotate(45deg)"></div>
          <div style="font-size:.9rem;color:var(--moss-dark);margin-bottom:.35rem;font-weight:600">
            &#10024; Also called: <span id="fore-chips"></span>
          </div>
          <label style="font-size:.85rem;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;color:var(--ink)">
            <input type="checkbox" name="use_nicks" value="1" id="use-nicks-cb">
            Search for these names too
          </label>
          <div class="hint" style="margin-top:.2rem">Finds records even if they wrote a different version of the name.</div>
        </div>
      </div>
      <div>
        <label for="surname">&#128106; Last name</label>
        <input type="text" id="surname" name="surname" required placeholder="e.g. Kelly">
      </div>
    </div>

    <div style="margin-top:.55rem">
      <label style="display:block;margin-bottom:.3rem">Are they a&hellip;</label>
      <div style="display:flex;gap:.45rem;flex-wrap:wrap">
        <label class="sex-btn"><input type="radio" name="sex" value="" checked> &#129335; Don&rsquo;t know</label>
        <label class="sex-btn"><input type="radio" name="sex" value="M"> &#128102; Boy / Man</label>
        <label class="sex-btn"><input type="radio" name="sex" value="F"> &#128103; Girl / Woman</label>
      </div>
      <div class="hint">Picking boy or girl helps skip records that are clearly the wrong person.</div>
    </div>

    <div class="two-col" style="margin-top:.55rem">
      <div>
        <label for="county">&#128506; Where in Ireland?</label>
        <select id="county" name="county">
          <option value="">(any county)</option>
          {% for c in counties %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
        </select>
        <input type="text" id="townland" name="townland" placeholder="Townland or village (optional) — e.g. Ballymore" autocomplete="off" style="margin-top:.4rem">
        <div class="hint">Know a townland or village? Shortest unique part works best. Leave blank if unsure.</div>
      </div>
      <div>
        <label for="birth_year">&#127874; When were they born? <button type="button" id="year-helper-btn" style="font-size:.78rem;cursor:pointer;border:1px solid var(--gold);background:#fffcf2;color:var(--sepia);border-radius:10px;padding:1px 8px;font-family:inherit;margin-left:.3rem">&#127922; Help me guess</button></label>
        <div id="year-helper" style="display:none;margin-bottom:.4rem;padding:.5rem .7rem;background:#fffcf2;border:1px solid var(--gold);border-radius:8px;font-size:.88rem">
          <div style="font-weight:600;color:var(--moss-dark);margin-bottom:.35rem">Who is this person to you?</div>
          <div style="display:flex;gap:.4rem;flex-wrap:wrap">
            <button type="button" class="gen-btn" data-year="1950" data-range="15">&#128106; Parent (born ~1935&ndash;1965)</button>
            <button type="button" class="gen-btn" data-year="1910" data-range="15">&#128106;&#128106; Grandparent (born ~1895&ndash;1925)</button>
            <button type="button" class="gen-btn" data-year="1875" data-range="15">&#128106;&#128106;&#128106; Great-grandparent (born ~1860&ndash;1900)</button>
            <button type="button" class="gen-btn" data-year="1840" data-range="15">Further back (born ~1820&ndash;1860)</button>
          </div>
        </div>
        <div id="year-mode-toggle" class="seg" role="tablist" style="margin-bottom:.45rem">
          <button type="button" class="seg-btn selected" data-mode="decade" role="tab" aria-selected="true">Decade</button>
          <button type="button" class="seg-btn" data-mode="exact" role="tab" aria-selected="false">Exact year</button>
          <button type="button" class="seg-btn" data-mode="unknown" role="tab" aria-selected="false">Not sure</button>
        </div>
        <div id="mode-decade" class="mode-panel">
          <div id="decade-row" class="btn-group" style="margin-bottom:.25rem">
            <button type="button" class="decade-btn" data-year="1825">1820s</button>
            <button type="button" class="decade-btn" data-year="1835">1830s</button>
            <button type="button" class="decade-btn" data-year="1845">1840s</button>
            <button type="button" class="decade-btn" data-year="1855">1850s</button>
            <button type="button" class="decade-btn" data-year="1865">1860s</button>
            <button type="button" class="decade-btn" data-year="1875">1870s</button>
            <button type="button" class="decade-btn" data-year="1885">1880s</button>
            <button type="button" class="decade-btn" data-year="1895">1890s</button>
            <button type="button" class="decade-btn" data-year="1905">1900s</button>
            <button type="button" class="decade-btn" data-year="1915">1910s</button>
            <button type="button" class="decade-btn" data-year="1925">1920s</button>
            <button type="button" class="decade-btn" data-year="1935">1930s</button>
            <button type="button" class="decade-btn" data-year="1945">1940s</button>
          </div>
        </div>
        <div id="mode-exact" class="mode-panel" style="display:none">
          <div style="display:flex;gap:.4rem;align-items:center;font-size:.9rem;flex-wrap:wrap">
            <input type="number" id="exact_year" min="1700" max="1950" placeholder="e.g. 1875" style="width:8rem">
          </div>
        </div>
        <div id="mode-unknown" class="mode-panel" style="display:none;font-size:.85rem;color:var(--sepia);font-style:italic">
          No year &mdash; search will cast a wider net; results less precise.
        </div>
        <div style="margin-top:.35rem;font-size:.82rem">
          <span id="census-age-readout" style="display:none;color:var(--moss-dark);font-weight:600"><span id="census-age-list"></span></span>
        </div>
        <input type="hidden" id="birth_year" name="birth_year">
        <input type="hidden" id="year_mode" name="year_mode" value="approx">
        <div id="timeline-wrap" style="display:none;margin-top:.45rem">
          <button type="button" id="tl-expand-btn" style="font-size:.88rem;cursor:pointer;border:1px solid var(--gold);background:#fffcf2;color:var(--sepia);border-radius:16px;padding:.35rem .9rem;font-family:inherit;font-weight:600">
            &#128197; See what else was happening <span id="tl-gen" style="font-weight:400;opacity:.85"></span>
          </button>
        </div>

        <style>
          #tl-modal { display:none; position:fixed; inset:0; z-index:1000; align-items:center; justify-content:center; padding:1.5rem;
            background: radial-gradient(ellipse at center, rgba(40,25,5,.78) 0%, rgba(20,12,0,.92) 100%); }
          #tl-modal-panel {
            position:relative; max-width:1200px; width:100%; max-height:92vh; overflow:auto;
            padding:1.6rem 2.2rem 1.4rem;
            font-family:"Palatino Linotype","Book Antiqua",Georgia,serif;
            color:#3a2a10;
            background:
              radial-gradient(ellipse at 20% 10%, rgba(255,248,210,.9) 0%, transparent 60%),
              radial-gradient(ellipse at 80% 90%, rgba(230,200,140,.6) 0%, transparent 55%),
              repeating-linear-gradient(115deg, rgba(180,140,60,.045) 0 2px, transparent 2px 7px),
              linear-gradient(180deg, #f8ecc8 0%, #ecd9a6 100%);
            border-radius:6px;
            box-shadow:
              inset 0 0 0 2px #c9a24a,
              inset 0 0 0 4px #fffcf2,
              inset 0 0 0 5px #8b6d2e,
              inset 0 0 40px rgba(160,110,30,.25),
              0 18px 60px rgba(0,0,0,.6);
          }
          #tl-modal-panel::before, #tl-modal-panel::after {
            content:"\2618"; position:absolute; font-size:1.5rem; color:#8b6d2e; opacity:.6;
          }
          #tl-modal-panel::before { top:.7rem; left:1rem; }
          #tl-modal-panel::after { bottom:.7rem; right:1rem; }
          .tl-corner { position:absolute; font-size:1.5rem; color:#8b6d2e; opacity:.6; }
          .tl-corner.tr { top:.7rem; right:3rem; }
          .tl-corner.bl { bottom:.7rem; left:1rem; }
          .tl-title {
            text-align:center; font-size:2rem; margin:0 0 .1rem;
            color:#5a3f10; letter-spacing:.06em; font-variant:small-caps; font-weight:700;
            text-shadow:0 1px 0 rgba(255,255,255,.4);
          }
          .tl-title .orn { color:#c9a24a; font-size:1.1rem; vertical-align:middle; margin:0 .4rem; }
          .tl-subtitle {
            text-align:center; font-style:italic; color:#7a5a2a; font-size:1.05rem; margin:0 0 .3rem;
          }
          .tl-divider {
            text-align:center; color:#c9a24a; letter-spacing:.6em; margin:.1rem 0 .5rem; font-size:.9rem;
          }
          #tl-modal-caption {
            margin-top:.7rem; padding:.7rem 1rem;
            background:rgba(255,252,226,.55);
            border-left:4px solid #c9a24a; border-right:4px solid #c9a24a;
            border-radius:3px;
            font-size:1.1rem; color:#5a3f10; line-height:1.5; font-style:italic;
            text-align:center; min-height:1em;
          }
          #tl-close-btn {
            position:absolute; top:.7rem; right:.9rem;
            font-size:1.15rem; cursor:pointer;
            border:1px solid #8b6d2e; background:#fffcf2; color:#5a3f10;
            border-radius:50%; width:2.1rem; height:2.1rem;
            font-family:inherit; font-weight:700;
            box-shadow:inset 0 0 0 1px #c9a24a;
          }
          #tl-close-btn:hover { background:#f3e6c2; }
          #tl-expand-btn {
            font-family:"Palatino Linotype",Georgia,serif;
            background:linear-gradient(180deg,#fffcf2,#f3e6c2);
            border:1px solid #8b6d2e; color:#5a3f10;
          }
          #tl-expand-btn:hover { background:linear-gradient(180deg,#f8ecc8,#ecd9a6); }
        </style>
        <div id="tl-modal">
          <div id="tl-modal-panel">
            <span class="tl-corner tr">&#x2618;</span>
            <span class="tl-corner bl">&#x2618;</span>
            <button type="button" id="tl-close-btn">&times;</button>
            <h2 class="tl-title"><span class="orn">&#x2766;</span>What Else Was Happening in &Eacute;ire<span class="orn">&#x2766;</span></h2>
            <div class="tl-subtitle" id="tl-modal-gen"></div>
            <div class="tl-divider">&#9753; &#8226; &#9753; &#8226; &#9753;</div>
            <svg id="tl-modal-svg" viewBox="0 0 1200 420" style="width:100%;height:auto;font-family:'Palatino Linotype',Georgia,serif" aria-hidden="true">
              <line x1="40" y1="340" x2="1160" y2="340" stroke="#8b6d2e" stroke-width="3"/>
              <g font-size="18" fill="#5a3f10" font-weight="600" text-anchor="middle">
                <g><line x1="40" y1="334" x2="40" y2="346" stroke="#8b6d2e" stroke-width="2"/><text x="40" y="372">1800</text></g>
                <g><line x1="280" y1="334" x2="280" y2="346" stroke="#8b6d2e" stroke-width="2"/><text x="280" y="372">1830</text></g>
                <g><line x1="520" y1="334" x2="520" y2="346" stroke="#8b6d2e" stroke-width="2"/><text x="520" y="372">1860</text></g>
                <g><line x1="760" y1="334" x2="760" y2="346" stroke="#8b6d2e" stroke-width="2"/><text x="760" y="372">1890</text></g>
                <g><line x1="1000" y1="334" x2="1000" y2="346" stroke="#8b6d2e" stroke-width="2"/><text x="1000" y="372">1920</text></g>
                <g><line x1="1160" y1="334" x2="1160" y2="346" stroke="#8b6d2e" stroke-width="2"/><text x="1160" y="372">1950</text></g>
              </g>
              <g id="tl-modal-events" font-size="18" fill="#3a2a10" text-anchor="middle"></g>
              <g id="tl-modal-pin" style="display:none">
                <line id="tl-modal-pin-line" x1="0" y1="40" x2="0" y2="340" stroke="#8a1c1c" stroke-width="4"/>
                <circle id="tl-modal-pin-dot" cx="0" cy="40" r="14" fill="#8a1c1c" stroke="#fffcf2" stroke-width="2"/>
                <text id="tl-modal-pin-label" x="0" y="26" font-size="24" fill="#8a1c1c" font-weight="700" text-anchor="middle"></text>
              </g>
            </svg>
            <div id="tl-modal-caption"></div>
          </div>
        </div>
      </div>
    </div>

    <details style="margin-top:.7rem;border-top:1px solid var(--cream-dark);padding-top:.6rem">
      <summary style="cursor:pointer;margin-bottom:.5rem;font-size:.9rem;color:var(--sepia);font-weight:600;list-style:none;user-select:none">
        &#9881; What should we search? <span style="font-weight:400;opacity:.75">(click to customise &mdash; defaults search everything)</span>
      </summary>
      <div style="display:flex;gap:.5rem;margin-bottom:.6rem;flex-wrap:wrap">
        <button type="button" id="preset-census"
          style="padding:.4rem 1rem;border-radius:20px;border:2px solid;cursor:pointer;font:inherit;font-size:.9rem;font-weight:600;background:#fff;border-color:var(--cream-dark);color:var(--sepia)">
          📜 Just the census <span style="font-weight:400;opacity:.85">(fast)</span>
        </button>
        <button type="button" id="preset-full"
          style="padding:.4rem 1rem;border-radius:20px;border:2px solid;cursor:pointer;font:inherit;font-size:.9rem;font-weight:600;background:var(--moss);border-color:var(--moss);color:#fff">
          🔍 Everything <span style="font-weight:400;opacity:.85">(takes longer)</span>
        </button>
      </div>
      <div class="filter-row">
        <span class="filter-lbl">📜 Census</span>
        <div class="btn-group">
          <label class="tog-btn"><input type="checkbox" name="census_year" value="1901" checked>1901</label>
          <label class="tog-btn"><input type="checkbox" name="census_year" value="1911" checked>1911</label>
          <label class="tog-btn"><input type="checkbox" name="census_year" value="1926" checked>1926</label>
        </div>
      </div>
      <div class="filter-row" style="margin-top:.4rem">
        <span class="filter-lbl">&nbsp;</span>
        <label style="font-size:.88rem;cursor:pointer;display:inline-flex;align-items:center;gap:.4rem;color:var(--ink)" data-tip="&#127969; Also fetch who else lived in each census household. Slower but great for learning about families.">
          <input type="checkbox" name="fetch_household" value="1">
          &#127969; Also show household members <span style="font-weight:400;opacity:.7">(learning extra &mdash; slower)</span>
        </label>
      </div>
      <div class="filter-row" style="margin-top:.4rem">
        <span class="filter-lbl">🔍 Records</span>
        <div class="btn-group" id="rec-btn-group">
          <label class="tog-btn" data-tip="&#128220; Official birth records. Ireland started keeping these in 1864."><input type="checkbox" name="ig_event" value="event-birth" checked>Births</label>
          <label class="tog-btn" data-tip="&#9962; Church record of a baptism &mdash; often older than official records, going back centuries."><input type="checkbox" name="ig_event" value="event-baptism" checked>Baptisms</label>
          <label class="tog-btn" data-tip="&#128141; Official and church marriage records. Civil records from 1845."><input type="checkbox" name="ig_event" value="event-marriage" checked>Marriages</label>
          <label class="tog-btn" data-tip="&#128366; Official death records, kept from 1864."><input type="checkbox" name="ig_event" value="event-death" checked>Deaths</label>
          <label class="tog-btn" data-tip="&#9960; Church burial record &mdash; can be much older than official records."><input type="checkbox" name="ig_event" value="event-burial" checked>Burials</label>
          <label class="tog-btn" data-tip="&#127969; A list of who rented land in Ireland in the 1850s and 1860s. Great for finding whole families."><input type="checkbox" name="ig_event" value="griffiths" checked>Land records</label>
        </div>
      </div>
      <div id="rec-tip" style="visibility:hidden;margin-top:.4rem;margin-left:91px;padding:.4rem .7rem;background:#fffcf2;border-left:3px solid var(--gold);border-radius:0 6px 6px 0;font-size:.88rem;color:var(--ink)"></div>
      <div class="filter-hint">Click any to untick and skip that search.</div>
    </details>

    <div id="search-count" style="margin-top:.5rem;font-size:.85rem;color:var(--moss-dark);padding:.3rem .6rem;background:#f4fdf4;border-radius:6px;border-left:3px solid var(--moss)"></div>
    <div id="fast-offer" style="display:none;margin-top:.4rem;font-size:.88rem;padding:.5rem .7rem;background:#fff6d8;border-left:3px solid #e0a800;border-radius:6px">
      ⚡ That is a lot of searches. Each one takes 5–10s pause.
      <button type="button" id="fast-btn" style="margin-left:.5rem;padding:.3rem .7rem;border-radius:16px;border:2px solid var(--moss);background:#fff;color:var(--moss-dark);cursor:pointer;font:inherit;font-size:.85rem;font-weight:600">
        Fast search — exact name only
      </button>
    </div>

    <div id="search-quality" style="visibility:hidden;margin:.7rem 0 .2rem;padding:.55rem .8rem;border-radius:8px;background:#f4fdf4;border:1px solid #c0dfc0;font-size:.95rem"></div>
    <style>
      .filter-row { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
      .filter-lbl { font-size:.92rem; font-weight:700; color:var(--moss-dark); min-width:85px; flex-shrink:0; }
      .filter-hint { font-size:.82rem; color:var(--sepia); margin-top:.45rem; margin-left:91px; font-style:italic; }
      .btn-group { display:flex; gap:.35rem; flex-wrap:wrap; }
      .tog-btn {
        display:inline-block; cursor:pointer; padding:.32rem .8rem;
        border-radius:20px; font-size:.92rem; border:2px solid var(--cream-dark);
        background:#fff; color:var(--sepia); transition:all .12s; user-select:none;
        font-family:inherit;
      }
      .tog-btn input { display:none; }
      .tog-btn:has(input:checked) { background:var(--moss); color:#fff; border-color:var(--moss-dark); }
      .tog-btn:hover { border-color:var(--gold); }
      .sex-btn {
        display:inline-block; cursor:pointer; padding:.38rem 1rem;
        border-radius:20px; font-size:1rem; border:2px solid var(--cream-dark);
        background:#fff; color:var(--sepia); transition:all .12s; user-select:none;
        font-family:inherit;
      }
      .sex-btn input { display:none; }
      .sex-btn:has(input:checked) { background:var(--moss); color:#fff; border-color:var(--moss-dark); }
      .sex-btn:hover { border-color:var(--gold); }
      .gen-btn {
        cursor:pointer; padding:.3rem .75rem; border-radius:16px; font-size:.85rem;
        border:1px solid var(--gold); background:#fff; color:var(--sepia);
        font-family:inherit; transition:all .12s;
      }
      .gen-btn:hover { background:var(--gold); color:#fff; }
      .decade-btn {
        cursor:pointer; padding:.22rem .55rem; border-radius:14px; font-size:.82rem;
        border:2px solid var(--cream-dark); background:#fff; color:var(--sepia);
        font-family:inherit; font-weight:600; transition:all .12s;
      }
      .decade-btn:hover { border-color:var(--gold); }
      .decade-btn.selected { background:var(--moss); color:#fff; border-color:var(--moss-dark); }
      .seg { display:inline-flex; border:1px solid var(--cream-dark); border-radius:16px; overflow:hidden; background:#fff; }
      .seg-btn {
        cursor:pointer; padding:.3rem .8rem; font-size:.85rem; font-family:inherit; font-weight:600;
        border:0; background:transparent; color:var(--sepia); transition:all .12s;
        border-right:1px solid var(--cream-dark);
      }
      .seg-btn:last-child { border-right:0; }
      .seg-btn:hover { background:#fffcf2; }
      .seg-btn.selected { background:var(--moss); color:#fff; }
    </style>

    <button type="submit" class="go" id="qs-btn">&#9752; Begin the hunt &#127808;</button>
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


      var NK = {{ nicknames_json | safe }};
      function expandFore(name) {
        var key = name.toLowerCase().trim();
        if (!key) return [];
        var out = [name], seen = {}; seen[key] = 1;
        (NK[key] || []).forEach(function(v){ if (!seen[v]){ out.push(v); seen[v]=1; } });
        Object.keys(NK).forEach(function(canon){
          if (NK[canon].indexOf(key) >= 0 && !seen[canon]){
            out.push(canon); seen[canon] = 1;
            NK[canon].forEach(function(v){ if (!seen[v]){ out.push(v); seen[v]=1; } });
          }
        });
        return out;
      }
      var foreInput = document.getElementById('forename');
      var foreVarDiv = document.getElementById('fore-variants');
      var foreChips = document.getElementById('fore-chips');
      var _foreHasVariants = false;
      function _showVariants() { if (_foreHasVariants) foreVarDiv.style.display = 'block'; }
      function _hideVariants() { foreVarDiv.style.display = 'none'; }
      if (foreInput && foreVarDiv && foreChips) {
        foreInput.addEventListener('input', function(){
          var val = this.value.trim();
          var variants = val ? expandFore(val).slice(1) : [];
          if (variants.length) {
            foreChips.innerHTML = variants.map(function(v){
              return '<span style="display:inline-block;padding:2px 9px;border-radius:12px;'
                   + 'background:#e8f4e8;color:var(--moss-dark);border:1px solid var(--moss);'
                   + 'margin:1px;font-size:.88rem">' + v + '</span>';
            }).join('');
            _foreHasVariants = true;
            _showVariants();
          } else {
            _foreHasVariants = false;
            _hideVariants();
          }
          updateQuality();
        });
        foreInput.addEventListener('focus', _showVariants);
        document.addEventListener('click', function(e){
          if (!foreVarDiv.contains(e.target) && e.target !== foreInput) _hideVariants();
        });
      }

      // --- Record type tooltips ---
      var recTip = document.getElementById('rec-tip');
      if (recTip) {
        document.querySelectorAll('#rec-btn-group [data-tip]').forEach(function(el){
          el.addEventListener('mouseenter', function(){
            recTip.innerHTML = el.dataset.tip;
            recTip.style.visibility = 'visible';
          });
          el.addEventListener('mouseleave', function(){ recTip.style.visibility = 'hidden'; });
          el.addEventListener('focus', function(){
            recTip.innerHTML = el.dataset.tip;
            recTip.style.visibility = 'visible';
          });
          el.addEventListener('blur', function(){ recTip.style.visibility = 'hidden'; });
        });
      }

      // --- Search quality stars ---
      var qualDiv = document.getElementById('search-quality');
      function updateQuality() {
        if (!qualDiv) return;
        var fore = (document.getElementById('forename').value || '').trim();
        var sur  = (document.getElementById('surname').value || '').trim();
        var yr   = (document.getElementById('birth_year').value || '').trim();
        var cty  = (document.getElementById('county').value || '').trim();
        if (!fore && !sur) { qualDiv.style.visibility = 'hidden'; return; }
        var score = 1 + (yr ? 1 : 0) + (cty ? 1 : 0);
        var stars = ['&#9733;','&#9733;','&#9733;'].map(function(s,i){
          return '<span style="color:'+(i<score?'#c9a04a':'#ccc')+';font-size:1.3em">'+s+'</span>';
        }).join('');
        var msg = score === 3
          ? '<strong>Brilliant!</strong> Ready to search &#127808;'
          : score === 2
          ? '<strong>Getting there!</strong> Add a ' + (!yr ? 'birth year' : 'county') + ' to narrow it down.'
          : '<strong>Good start!</strong> Add a birth year and county for better results.';
        qualDiv.innerHTML = stars + '&nbsp; ' + msg;
        qualDiv.style.visibility = 'visible';
      }
      ['surname','county'].forEach(function(id){
        var el = document.getElementById(id);
        if (el) el.addEventListener('input', updateQuality);
        if (el && el.tagName === 'SELECT') el.addEventListener('change', updateQuality);
      });

      // --- Search presets ---
      function applyPreset(mode) {
        form.querySelectorAll('input[name="census_year"]').forEach(function(cb){ cb.checked = true; });
        form.querySelectorAll('input[name="ig_event"]').forEach(function(cb){ cb.checked = (mode === 'full'); });
        var pc = document.getElementById('preset-census');
        var pf = document.getElementById('preset-full');
        if (pc && pf) {
          var on = 'background:var(--moss);border-color:var(--moss);color:#fff';
          var off = 'background:#fff;border-color:var(--cream-dark);color:var(--sepia)';
          var base = 'padding:.4rem 1rem;border-radius:20px;border:2px solid;cursor:pointer;font:inherit;font-size:.9rem;font-weight:600;';
          pc.setAttribute('style', base + (mode === 'census' ? on : off));
          pf.setAttribute('style', base + (mode === 'full' ? on : off));
        }
        updateSearchCount();
        if (typeof updateQuality === 'function') updateQuality();
      }
      var pcBtn = document.getElementById('preset-census');
      var pfBtn = document.getElementById('preset-full');
      if (pcBtn) pcBtn.addEventListener('click', function(){ applyPreset('census'); });
      if (pfBtn) pfBtn.addEventListener('click', function(){ applyPreset('full'); });

      // --- Live search count ---
      function updateSearchCount() {
        var cEl = document.getElementById('search-count');
        if (!cEl) return;
        var censusN = form.querySelectorAll('input[name="census_year"]:checked').length;
        var igChecked = Array.from(form.querySelectorAll('input[name="ig_event"]:checked'))
          .map(function(cb){ return cb.value; });
        var igN = igChecked.filter(function(v){ return v !== 'griffiths'; }).length;
        var griff = igChecked.indexOf('griffiths') >= 0 ? 1 : 0;
        var sites = censusN + igN + griff;
        if (sites === 0) {
          cEl.innerHTML = '<strong style="color:#b00">Nothing ticked</strong> — pick at least one to search.';
          return;
        }
        var nicksCb = document.getElementById('use-nicks-cb');
        var foreVal = (foreInput && foreInput.value || '').trim();
        var nameCount = 1;
        if (nicksCb && nicksCb.checked && foreVal) {
          nameCount = Math.max(1, expandFore(foreVal).length);
        }
        var total = sites * nameCount;
        var parts = [];
        if (censusN) parts.push(censusN + ' census');
        if (igN) parts.push(igN + ' record type' + (igN === 1 ? '' : 's'));
        if (griff) parts.push('land records');
        var namesSuffix = nameCount > 1
          ? ' &times; ' + nameCount + ' name variants'
          : '';
        cEl.innerHTML = '<strong>' + total + '</strong> search' + (total === 1 ? '' : 'es') + ' will run: ' + parts.join(' + ') + namesSuffix;
        var offer = document.getElementById('fast-offer');
        if (offer) offer.style.display = (total >= 15) ? 'block' : 'none';
      }
      var fastBtn = document.getElementById('fast-btn');
      if (fastBtn) fastBtn.addEventListener('click', function(){
        var cb = document.getElementById('use-nicks-cb');
        if (cb) cb.checked = false;
        updateSearchCount();
        form.requestSubmit();
      });
      form.querySelectorAll('input[name="census_year"], input[name="ig_event"]').forEach(function(cb){
        cb.addEventListener('change', function(){
          // user manually toggled → both presets deselected
          var pc = document.getElementById('preset-census');
          var pf = document.getElementById('preset-full');
          var base = 'padding:.4rem 1rem;border-radius:20px;border:2px solid;cursor:pointer;font:inherit;font-size:.9rem;font-weight:600;background:#fff;border-color:var(--cream-dark);color:var(--sepia)';
          if (pc) pc.setAttribute('style', base);
          if (pf) pf.setAttribute('style', base);
          updateSearchCount();
        });
      });
      var nicksCb = document.getElementById('use-nicks-cb');
      if (nicksCb) nicksCb.addEventListener('change', updateSearchCount);
      if (foreInput) foreInput.addEventListener('input', updateSearchCount);
      updateSearchCount();

      // --- Birth year: decade chips + exact ---
      var hiddenYear = document.getElementById('birth_year');
      var hiddenMode = document.getElementById('year_mode');
      var exactInput = document.getElementById('exact_year');
      var decadeBtns = document.querySelectorAll('.decade-btn');

      // --- Census age readout + checkbox gating ---
      function updateCensusAges(){
        var readout = document.getElementById('census-age-readout');
        var list = document.getElementById('census-age-list');
        if (!readout || !list) return;
        var yr = parseInt(hiddenYear.value, 10);
        var isExact = hiddenMode && hiddenMode.value === 'exact';
        var years = [1901, 1911, 1926];
        if (!yr || isNaN(yr) || !isExact) {
          readout.style.display = 'none';
          years.forEach(function(cy){
            var cb = form.querySelector('input[name="census_year"][value="'+cy+'"]');
            if (cb) { cb.disabled = false; cb.parentElement.title = ''; cb.parentElement.style.opacity = ''; }
          });
          return;
        }
        var parts = [];
        years.forEach(function(cy){
          var age = cy - yr;
          var cb = form.querySelector('input[name="census_year"][value="'+cy+'"]');
          var label = cb ? cb.parentElement : null;
          var chip, implausible = false, reason = '';
          if (age < 0) {
            chip = '<span style="color:#aaa;text-decoration:line-through">'+cy+'</span>';
            implausible = true; reason = 'Not born yet in ' + cy;
          } else if (age > 95) {
            chip = '<span style="color:#aaa;text-decoration:line-through">'+cy+':'+age+'</span>';
            implausible = true; reason = 'Would be ' + age + ' in ' + cy + ' — unlikely alive';
          } else if (age === 0) {
            chip = cy+': &#128700;baby';
          } else {
            chip = cy+': '+age;
          }
          parts.push(chip);
          if (cb) {
            if (implausible) {
              if (cb.checked) cb.checked = false;
              cb.disabled = true;
              if (label) { label.title = reason; label.style.opacity = '.45'; }
            } else {
              cb.disabled = false;
              if (label) { label.title = ''; label.style.opacity = ''; }
            }
          }
        });
        list.innerHTML = '&nbsp;&middot;&nbsp;Age: ' + parts.join(' &middot; ');
        readout.style.display = 'inline';
        if (typeof updateSearchCount === 'function') updateSearchCount();
      }

      // --- Timeline of historical events ---
      var TL_EVENTS = [
        {year:1845, label:'Great Famine',        emoji:'\u2620'},
        {year:1864, label:'Civil records begin', emoji:'\u270D'},
        {year:1901, label:'1901 Census',         emoji:'\u2460'},
        {year:1911, label:'1911 Census',         emoji:'\u2461'},
        {year:1916, label:'Easter Rising',       emoji:'\u2721'},
        {year:1922, label:'Irish Free State',    emoji:'\u2618'},
        {year:1926, label:'1926 Census',         emoji:'\u2462'}
      ];
      var TL_MIN = 1800, TL_MAX = 1950;
      var TL_CFG = {
        modal:  { x0:40, x1:1160, lineY:340, rowTop:60, rowStep:38 }
      };
      function tlX(cfg, y){ return cfg.x0 + (y - TL_MIN) * (cfg.x1 - cfg.x0) / (TL_MAX - TL_MIN); }
      function renderEvents(gId, cfg){
        var g = document.getElementById(gId);
        if (!g) return;
        g.innerHTML = '';
        var evs = TL_EVENTS.slice().sort(function(a,b){ return a.year - b.year; });
        // one row per event, oldest at bottom (closest to axis) → newest at top
        var total = evs.length;
        evs.forEach(function(ev, i){
          var x = tlX(cfg, ev.year);
          var yLabel = cfg.rowTop + (total - 1 - i) * cfg.rowStep;
          var yTickTop = yLabel + 4;
          g.innerHTML += '<line x1="'+x+'" y1="'+yTickTop+'" x2="'+x+'" y2="'+(cfg.lineY-2)+'" stroke="#a8884a" stroke-width="1" stroke-dasharray="3,3"/>'
            + '<text x="'+x+'" y="'+yLabel+'" font-weight="700">'+ev.year+' &#183; '+ev.emoji+' '+ev.label+'</text>';
        });
      }
      renderEvents('tl-modal-events', TL_CFG.modal);
      function generationLabel(y){
        var now = new Date().getFullYear();
        var gens = Math.round((now - y) / 30);
        if (gens < 1) return '';
        if (gens === 1) return '~1 generation ago';
        return '~' + gens + ' generations ago';
      }
      function timelineCaption(y){
        if (y < 1845) return 'Before the Great Famine — most families farmed small plots of land.';
        if (y < 1852) return 'Famine years — a million died, a million more sailed on coffin ships.';
        if (y < 1864) return 'Just after the Famine — mass emigration still reshaping every parish.';
        if (y < 1880) return 'Civil records had started — births and deaths being written down.';
        if (y < 1901) return 'Land War era — peak emigration, ~80,000 leaving each year for America.';
        if (y < 1912) return 'Edwardian Ireland — counted in the 1901 and 1911 censuses.';
        if (y < 1918) return 'Would have lived through WW1 and the Easter Rising.';
        if (y < 1923) return 'War of Independence and Civil War — a violent birth for the new state.';
        if (y < 1939) return 'Grew up in the young Irish Free State.';
        return 'Teenager or young adult when WW2 broke out (Ireland stayed neutral).';
      }
      function setPin(cfg, lineId, dotId, labelId, groupId, yr){
        var clamped = Math.min(TL_MAX, Math.max(TL_MIN, yr));
        var x = tlX(cfg, clamped);
        var ln = document.getElementById(lineId);
        ln.setAttribute('x1', x); ln.setAttribute('x2', x);
        document.getElementById(dotId).setAttribute('cx', x);
        var lbl = document.getElementById(labelId);
        lbl.setAttribute('x', x); lbl.textContent = yr;
        document.getElementById(groupId).style.display = '';
      }
      function updateTimeline(){
        var wrap = document.getElementById('timeline-wrap');
        var yr = parseInt(hiddenYear.value, 10);
        if (!wrap) return;
        if (!yr || isNaN(yr)) {
          wrap.style.display = 'none';
          var m = document.getElementById('tl-modal'); if (m) m.style.display = 'none';
          return;
        }
        wrap.style.display = 'block';
        setPin(TL_CFG.modal, 'tl-modal-pin-line', 'tl-modal-pin-dot', 'tl-modal-pin-label', 'tl-modal-pin', yr);
        document.getElementById('tl-gen').textContent = '— born ~' + yr + ' · ' + generationLabel(yr);
        var mg = document.getElementById('tl-modal-gen'); if (mg) mg.textContent = 'Born ~' + yr + ' · ' + generationLabel(yr);
        var mc = document.getElementById('tl-modal-caption'); if (mc) mc.textContent = timelineCaption(yr);
      }
      (function wireTimelineModal(){
        var modal = document.getElementById('tl-modal');
        var open = function(){ if (modal) modal.style.display = 'flex'; };
        var close = function(){ if (modal) modal.style.display = 'none'; };
        var btn = document.getElementById('tl-expand-btn');
        var cls = document.getElementById('tl-close-btn');
        if (btn) btn.addEventListener('click', open);
        if (cls) cls.addEventListener('click', close);
        if (modal) modal.addEventListener('click', function(e){ if (e.target === modal) close(); });
        document.addEventListener('keydown', function(e){ if (e.key === 'Escape') close(); });
      })();

      function pickDecade(btn){
        decadeBtns.forEach(function(x){ x.classList.remove('selected'); });
        btn.classList.add('selected');
        hiddenYear.value = btn.dataset.year;
        hiddenMode.value = 'approx';
        if (exactInput) exactInput.value = '';
        updateQuality();
        updateCensusAges();
        updateTimeline();
      }
      decadeBtns.forEach(function(b){
        b.addEventListener('click', function(){ pickDecade(b); });
      });
      if (exactInput) {
        exactInput.addEventListener('input', function(){
          var v = exactInput.value.trim();
          if (v) {
            decadeBtns.forEach(function(x){ x.classList.remove('selected'); });
            hiddenYear.value = v;
            hiddenMode.value = 'exact';
          } else {
            hiddenYear.value = '';
            hiddenMode.value = 'approx';
          }
          updateQuality();
          updateCensusAges();
          updateTimeline();
        });
      }

      // --- Year mode segmented toggle ---
      var segBtns = document.querySelectorAll('#year-mode-toggle .seg-btn');
      var panels = {
        decade:  document.getElementById('mode-decade'),
        exact:   document.getElementById('mode-exact'),
        unknown: document.getElementById('mode-unknown')
      };
      function setYearMode(mode){
        segBtns.forEach(function(b){
          var on = b.dataset.mode === mode;
          b.classList.toggle('selected', on);
          b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        Object.keys(panels).forEach(function(k){
          if (panels[k]) panels[k].style.display = (k === mode) ? '' : 'none';
        });
        if (mode === 'decade') {
          if (exactInput) exactInput.value = '';
          hiddenMode.value = 'approx';
          var sel = document.querySelector('.decade-btn.selected');
          hiddenYear.value = sel ? sel.dataset.year : '';
        } else if (mode === 'exact') {
          decadeBtns.forEach(function(x){ x.classList.remove('selected'); });
          var v = exactInput ? exactInput.value.trim() : '';
          hiddenYear.value = v;
          hiddenMode.value = v ? 'exact' : 'approx';
        } else {
          decadeBtns.forEach(function(x){ x.classList.remove('selected'); });
          if (exactInput) exactInput.value = '';
          hiddenYear.value = '';
          hiddenMode.value = 'approx';
        }
        updateQuality();
        updateCensusAges();
        updateTimeline();
      }
      segBtns.forEach(function(b){
        b.addEventListener('click', function(){ setYearMode(b.dataset.mode); });
      });

      // --- "Help me guess" generation shortcut ---
      var yhBtn = document.getElementById('year-helper-btn');
      var yhDiv = document.getElementById('year-helper');
      if (yhBtn && yhDiv) {
        yhBtn.addEventListener('click', function(){
          yhDiv.style.display = yhDiv.style.display === 'none' ? 'block' : 'none';
        });
        yhDiv.querySelectorAll('.gen-btn').forEach(function(b){
          b.addEventListener('click', function(){
            var yr = parseInt(b.dataset.year);
            var decade = Math.floor(yr / 10) * 10 + 5;
            var match = document.querySelector('.decade-btn[data-year="' + decade + '"]');
            if (match) { setYearMode('decade'); pickDecade(match); }
            yhDiv.style.display = 'none';
          });
        });
      }
    })();
  </script>

</div>

<div class="hero" style="background:#fff7e0;margin-top:.6rem">
  <h2 style="margin:0 0 .4rem;font-size:1.15rem">&#128214; What we search</h2>
  <div style="font-size:.95rem;line-height:1.5">
    <div>&#128203; <strong>The old census</strong> &mdash; who lived in which house in 1901, 1911 and 1926.</div>
    <div>&#128220; <strong>Births, marriages &amp; deaths</strong> &mdash; government and church records.</div>
    <div>&#127969; <strong>Griffiths Valuation</strong> &mdash; a list from the 1850s of who rented land.</div>
  </div>
</div>

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
          <stop offset="0%" stop-color="#b8d2dc"/><stop offset="60%" stop-color="#dcd6c0"/><stop offset="100%" stop-color="#f0e8d0"/>
        </linearGradient>
        <linearGradient id="lh1" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#6b8a4a"/><stop offset="100%" stop-color="#4a6a3a"/>
        </linearGradient>
        <linearGradient id="lh2" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#85a556"/><stop offset="100%" stop-color="#5d8f3d"/>
        </linearGradient>
        <linearGradient id="lthatch" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#d4b06b"/><stop offset="100%" stop-color="#9c7a3e"/>
        </linearGradient>
        <linearGradient id="lwall" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="#fbf7ec"/><stop offset="100%" stop-color="#e4d8bc"/>
        </linearGradient>
      </defs>
      <rect width="800" height="200" fill="url(#lsky)"/>
      <!-- cloud wisps -->
      <ellipse cx="180" cy="40" rx="45" ry="7" fill="#fff" opacity=".55"/>
      <ellipse cx="520" cy="55" rx="55" ry="6" fill="#fff" opacity=".45"/>
      <!-- distant hills -->
      <path d="M0 130 Q200 75 400 115 T800 100 L800 200 L0 200 Z" fill="url(#lh1)" opacity=".9"/>
      <path d="M0 150 Q180 120 400 140 T800 135 L800 200 L0 200 Z" fill="url(#lh2)"/>
      <!-- near field -->
      <path d="M0 162 L800 155 L800 200 L0 200 Z" fill="#7aa54d"/>
      <!-- drystone wall -->
      <rect x="0" y="158" width="800" height="5" fill="#9c9684"/>
      <g fill="#b5b09e" stroke="#6b6b5a" stroke-width=".3">
        <rect x="20" y="158" width="18" height="5"/>
        <rect x="55" y="158" width="14" height="5"/>
        <rect x="85" y="158" width="20" height="5"/>
        <rect x="125" y="158" width="16" height="5"/>
        <rect x="160" y="158" width="22" height="5"/>
        <rect x="200" y="158" width="14" height="5"/>
        <rect x="345" y="158" width="20" height="5"/>
        <rect x="390" y="158" width="14" height="5"/>
        <rect x="425" y="158" width="18" height="5"/>
        <rect x="465" y="158" width="22" height="5"/>
        <rect x="705" y="158" width="18" height="5"/>
        <rect x="745" y="158" width="20" height="5"/>
      </g>
      <!-- haycock -->
      <g transform="translate(70,140)">
        <ellipse cx="0" cy="20" rx="24" ry="4" fill="#8a6d3a" opacity=".6"/>
        <path d="M-24 20 Q-18 -4 0 -12 Q18 -4 24 20 Z" fill="#c9a961" stroke="#8a6d3a" stroke-width=".6"/>
        <path d="M-14 12 L2 -4" stroke="#8a6d3a" stroke-width=".6"/>
        <path d="M-6 16 L10 2" stroke="#8a6d3a" stroke-width=".6"/>
        <path d="M12 12 L4 -6" stroke="#8a6d3a" stroke-width=".6"/>
        <path d="M-10 18 Q0 -10 14 10" stroke="#a88a4a" stroke-width=".5" fill="none"/>
      </g>
      <!-- hawthorn tree -->
      <g transform="translate(445,118)">
        <rect x="-3" y="18" width="6" height="28" fill="#4a3220"/>
        <ellipse cx="0" cy="10" rx="20" ry="18" fill="#5a7a3a"/>
        <ellipse cx="-11" cy="2" rx="12" ry="12" fill="#6a8a4a"/>
        <ellipse cx="11" cy="4" rx="13" ry="12" fill="#6a8a4a"/>
        <ellipse cx="0" cy="-4" rx="10" ry="10" fill="#7a9a5a"/>
      </g>
      <!-- cottage -->
      <g transform="translate(515,110)">
        <!-- stone base -->
        <rect x="-2" y="58" width="114" height="10" fill="#9c9684" stroke="#5a5a4a" stroke-width=".5"/>
        <!-- whitewashed walls -->
        <rect x="0" y="22" width="110" height="38" fill="url(#lwall)" stroke="#a89a78" stroke-width="1"/>
        <!-- thatched roof -->
        <path d="M-12 24 L55 -8 L122 24 L110 30 L0 30 Z" fill="url(#lthatch)" stroke="#6b4a2a" stroke-width="1"/>
        <!-- thatch stroke texture (fan from ridge) -->
        <g stroke="#8a6d3a" stroke-width=".35" opacity=".8">
          <line x1="-6" y1="24" x2="55" y2="-6"/>
          <line x1="8" y1="24" x2="55" y2="-6"/>
          <line x1="22" y1="24" x2="55" y2="-6"/>
          <line x1="36" y1="24" x2="55" y2="-6"/>
          <line x1="50" y1="24" x2="55" y2="-6"/>
          <line x1="60" y1="24" x2="55" y2="-6"/>
          <line x1="74" y1="24" x2="55" y2="-6"/>
          <line x1="88" y1="24" x2="55" y2="-6"/>
          <line x1="102" y1="24" x2="55" y2="-6"/>
          <line x1="116" y1="24" x2="55" y2="-6"/>
        </g>
        <!-- ridge -->
        <line x1="-12" y1="24" x2="122" y2="24" stroke="#6b4a2a" stroke-width="1.5"/>
        <line x1="-12" y1="27" x2="122" y2="27" stroke="#8a6d3a" stroke-width=".5"/>
        <!-- stone chimney at gable -->
        <rect x="95" y="-12" width="12" height="22" fill="#9c9684" stroke="#5a5a4a" stroke-width=".6"/>
        <line x1="95" y1="-4" x2="107" y2="-4" stroke="#5a5a4a" stroke-width=".3"/>
        <line x1="95" y1="2" x2="107" y2="2" stroke="#5a5a4a" stroke-width=".3"/>
        <rect x="93" y="-14" width="16" height="4" fill="#6b4a2a"/>
        <!-- smoke -->
        <path d="M101 -16 Q96 -24 101 -32 Q106 -40 100 -50" fill="none" stroke="#c8c8bb" stroke-width="2.6" opacity=".7" stroke-linecap="round"/>
        <path d="M101 -16 Q98 -22 102 -28" fill="none" stroke="#dedbd0" stroke-width="2" opacity=".55" stroke-linecap="round"/>
        <!-- half-door (split dutch) -->
        <rect x="47" y="34" width="18" height="26" fill="#6b3a1a" stroke="#3a1810" stroke-width=".6"/>
        <line x1="47" y1="47" x2="65" y2="47" stroke="#2a1008" stroke-width=".8"/>
        <rect x="48" y="35" width="16" height="11" fill="#7a4a24"/>
        <rect x="48" y="48" width="16" height="11" fill="#5a2a14"/>
        <circle cx="61" cy="52" r=".9" fill="#f4ecd8"/>
        <!-- small-paned windows -->
        <g transform="translate(14,32)">
          <rect x="-1" y="-1" width="18" height="16" fill="#5a3a20"/>
          <rect width="16" height="14" fill="#2a3a46"/>
          <line x1="8" y1="0" x2="8" y2="14" stroke="#e8dcc0" stroke-width=".7"/>
          <line x1="0" y1="7" x2="16" y2="7" stroke="#e8dcc0" stroke-width=".7"/>
        </g>
        <g transform="translate(80,32)">
          <rect x="-1" y="-1" width="18" height="16" fill="#5a3a20"/>
          <rect width="16" height="14" fill="#2a3a46"/>
          <line x1="8" y1="0" x2="8" y2="14" stroke="#e8dcc0" stroke-width=".7"/>
          <line x1="0" y1="7" x2="16" y2="7" stroke="#e8dcc0" stroke-width=".7"/>
        </g>
      </g>
      <!-- turf stack -->
      <g transform="translate(650,158)">
        <rect x="0" y="0" width="46" height="18" fill="#5a3a20" stroke="#3a2210" stroke-width=".5"/>
        <rect x="0" y="0" width="46" height="4" fill="#6a4a30"/>
        <g stroke="#3a2210" stroke-width=".35">
          <line x1="10" y1="0" x2="10" y2="18"/>
          <line x1="20" y1="0" x2="20" y2="18"/>
          <line x1="30" y1="0" x2="30" y2="18"/>
          <line x1="38" y1="0" x2="38" y2="18"/>
          <line x1="0" y1="6" x2="46" y2="6"/>
          <line x1="0" y1="12" x2="46" y2="12"/>
        </g>
      </g>
      <!-- sheep 1 (fluffy wool curls) -->
      <g transform="translate(225,167)">
        <ellipse cx="0" cy="0" rx="18" ry="10" fill="#f0e8d8"/>
        <circle cx="-13" cy="-3" r="5.5" fill="#f5efe0"/>
        <circle cx="-6" cy="-8" r="6" fill="#f5efe0"/>
        <circle cx="2" cy="-9" r="6" fill="#f5efe0"/>
        <circle cx="10" cy="-7" r="5.5" fill="#f5efe0"/>
        <circle cx="15" cy="-2" r="4.5" fill="#f5efe0"/>
        <ellipse cx="-18" cy="-1" rx="6" ry="5" fill="#3a2a1a"/>
        <circle cx="-20" cy="-3" r="1.1" fill="#fff"/>
        <ellipse cx="-22" cy="-5" rx="2" ry="3" fill="#2a1a0a" transform="rotate(-20 -22 -5)"/>
        <rect x="-8" y="8" width="3" height="7" fill="#3a2a1a"/>
        <rect x="5" y="8" width="3" height="7" fill="#3a2a1a"/>
      </g>
      <!-- sheep 2 -->
      <g transform="translate(305,173)">
        <ellipse cx="0" cy="0" rx="14" ry="8" fill="#f0e8d8"/>
        <circle cx="-10" cy="-2" r="4.5" fill="#f5efe0"/>
        <circle cx="-4" cy="-6" r="5" fill="#f5efe0"/>
        <circle cx="4" cy="-6" r="5" fill="#f5efe0"/>
        <circle cx="10" cy="-2" r="4" fill="#f5efe0"/>
        <ellipse cx="-14" cy="-1" rx="5" ry="4" fill="#3a2a1a"/>
        <circle cx="-16" cy="-2.5" r=".9" fill="#fff"/>
        <rect x="-6" y="6" width="2.5" height="5" fill="#3a2a1a"/>
        <rect x="4" y="6" width="2.5" height="5" fill="#3a2a1a"/>
      </g>
      <!-- hen -->
      <g transform="translate(385,180)">
        <ellipse cx="0" cy="0" rx="8" ry="5.5" fill="#8a4a2a"/>
        <path d="M-8 -1 Q-5 3 -8 4" fill="#6a3a1a"/>
        <circle cx="7" cy="-4" r="3" fill="#8a4a2a"/>
        <path d="M5 -6 L7 -9 L9 -7 Z" fill="#c43828"/>
        <path d="M6 -7 L8 -10 L10 -7" fill="#c43828"/>
        <path d="M9 -4 L12 -3 L9 -3 Z" fill="#f6b53e"/>
        <circle cx="8" cy="-4" r=".6" fill="#000"/>
        <rect x="-2" y="4" width="1.2" height="4" fill="#f6b53e"/>
        <rect x="1" y="4" width="1.2" height="4" fill="#f6b53e"/>
      </g>
      <!-- sun -->
      <circle cx="710" cy="42" r="20" fill="#f6d35e" opacity=".75"/>
      <circle cx="710" cy="42" r="28" fill="none" stroke="#f6d35e" stroke-width="1" opacity=".3"/>
    </svg>
  </div>

  <div class="sheep-big" aria-hidden="true">🐑</div>
  <h2 class="loader-heading">Looking for <span id="loader-name">{{ run.person_name or 'your ancestor' }}</span>…</h2>
  <div class="loader-msg" id="loader-msg" style="transition:opacity .4s">Getting ready…</div>
  <div id="loader-step-desc" style="font-size:.88rem;color:var(--sepia);margin:.2rem 0 .7rem;min-height:1.2em;font-style:italic"></div>
  <div id="loader-query" style="font-size:.95rem;color:var(--moss-dark);margin:.1rem 0 .4rem;min-height:1.2em;font-weight:600"></div>

  <div id="loader-checklist" style="margin:.6rem auto;max-width:460px;text-align:left;font-size:.92rem;line-height:1.9"></div>

  <div id="loader-live" style="font-size:1rem;color:var(--moss-dark);margin:.5rem 0;min-height:1.4em;font-weight:600"></div>

  <div class="loader-pause" id="loader-pause">
    ⏳ Being polite — waiting <strong id="loader-pause-remain">0</strong>s before next <span id="loader-pause-what">request</span>
  </div>

  <div class="loader-progress-text" style="margin-top:.5rem">
    Checked <strong id="loader-current">{{ run.current }}</strong>
    of <strong id="loader-total">{{ run.total or '?' }}</strong> ancestors
  </div>
  <div class="loader-bar-wrap">
    <div class="loader-bar-fill" id="loader-bar" style="width:{{ (100 * run.current / run.total) if run.total else 0 }}%"></div>
  </div>

  <form id="cancel-form" style="margin-top:1.2rem">
    <button type="button" id="cancel-btn"
      style="background:none;border:1px solid var(--sepia);color:var(--sepia);padding:.4rem 1.1rem;border-radius:6px;cursor:pointer;font:inherit;font-size:.95rem"
      onclick="cancelSearch()">✕ Stop searching</button>
  </form>
</div>

<div class="state" id="status-panel" style="{{ 'display:none' if run.state == 'running' else '' }}">
  <div id="st-progress-block" style="{{ 'display:none' if run.state == 'done' else '' }}">
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
  </div>
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

  <div id="st-stopped" style="display:{{ 'block' if run.stopped else 'none' }};margin-top:.5rem;padding:.4rem .7rem;background:#fff6d8;border-left:3px solid #e0a800;border-radius:4px;font-size:.9rem">&#9208; Search stopped early &mdash; showing results found so far.</div>

  <div id="st-done" style="display:{{ 'block' if run.state == 'done' else 'none' }}">
    <div id="st-celebrate" style="text-align:center;padding:1.2rem 1rem 1rem;margin:.2rem 0 1rem;
         background:linear-gradient(180deg,#fff9df 0%,#f6e9b8 100%);
         border:2px solid var(--gold);border-radius:12px;box-shadow:inset 0 0 0 2px #fffcf2">
      <div style="font-size:2.4rem;line-height:1">&#127881;</div>
      <div style="font-size:1.35rem;font-weight:700;color:var(--moss-dark);margin-top:.3rem">
        You found <span id="st-rc">{{ run.record_count }}</span> clue<span id="st-rc-plural">{{ '' if run.record_count == 1 else 's' }}</span>
        <span id="st-celebrate-who">{% if run.label %}about {{ run.label.split(' (')[0] }}!{% else %}!{% endif %}</span>
      </div>
      <div id="st-new-line" style="font-size:.95rem;color:var(--sepia);margin-top:.35rem;font-style:italic">
        <span id="st-new">{{ run.new_count }}</span> new since last time.
      </div>
    </div>
    <div style="text-align:center;display:flex;gap:.5rem;flex-wrap:wrap;justify-content:center">
      <a class="btn" href="{{ url_for('ancestors_page') }}">&#128106; Our family</a>
      <a class="btn" href="{{ url_for('results_html') }}" target="_blank">&#128269; What we found</a>
      <a class="btn" href="{{ url_for('download', name='results.csv') }}">&#128190; Save (CSV)</a>
      <a class="btn" href="{{ url_for('download', name='results.json') }}">&#128190; Save (JSON)</a>
    </div>
    <details style="margin-top:.8rem">
      <summary style="cursor:pointer;font-size:.85rem;color:var(--sepia);opacity:.75">Grown-up files</summary>
      <div style="margin-top:.4rem;display:flex;gap:.4rem;flex-wrap:wrap">
        <a class="btn" href="{{ url_for('download', name='run.log') }}">run.log</a>
        <a class="btn" href="{{ url_for('download', name='confirmations.json') }}">confirmations.json</a>
      </div>
    </details>
  </div>

  <form id="st-reset" method="post" action="{{ url_for('reset') }}" style="margin-top:1rem;display:{{ 'block' if run.state in ('done','error') else 'none' }}">
    <button type="submit" class="go">&#128269; Try another name</button>
  </form>

</div>

<script>
const STEP_INFO = {
  'NAI 1901': { icon: '📜', label: '1901 census', msg: 'Flipping through the 1901 census…', desc: 'A list of everyone living in Ireland — over 4 million people!' },
  'NAI 1911': { icon: '📜', label: '1911 census', msg: 'Checking the 1911 census…', desc: 'Ten years later — families change a lot in a decade.' },
  'NAI 1926': { icon: '📜', label: '1926 census', msg: 'Looking at the 1926 census…', desc: 'This one even records how many years people were married!' },
  'IG Civil Birth': { icon: '🍼', label: 'birth records', msg: 'Searching birth records…', desc: 'Official government records kept since 1864.' },
  'IG Civil Marriage': { icon: '💍', label: 'marriage records', msg: 'Checking marriage records…', desc: 'Official government records since 1845.' },
  'IG Civil Death': { icon: '📋', label: 'death records', msg: 'Looking through death records…', desc: 'Official government records kept since 1864.' },
  'IG Church Baptism': { icon: '⛪', label: 'church baptisms', msg: 'Checking church baptism records…', desc: 'Churches kept these for hundreds of years — much older than government records!' },
  'IG Church Marriage': { icon: '⛪', label: 'church weddings', msg: 'Looking at church wedding records…', desc: 'A separate set of records from the official ones.' },
  'IG Church Burial': { icon: '⛪', label: 'church burials', msg: 'Searching burial records…', desc: "Sometimes the only place a person's name was ever written down." },
  'Griffith': { icon: '🗺️', label: "Griffith's Valuation", msg: "Looking at Griffith's Valuation…", desc: 'A huge land survey from the 1850s — who owned or rented what.' },
};
let _stepLog = [];
let _lastSeenStep = '';
let _msgTimer = null;

function updateLoaderStep(step) {
  if (!step || step === _lastSeenStep) return;
  // mark previous as done
  const prev = _stepLog.find(s => s.key === _lastSeenStep);
  if (prev) prev.done = true;
  _lastSeenStep = step;
  const info = STEP_INFO[step];
  if (!info) return;
  if (!_stepLog.find(s => s.key === step)) _stepLog.push({ key: step, icon: info.icon, label: info.label, done: false });
  const msgEl = document.getElementById('loader-msg');
  const descEl = document.getElementById('loader-step-desc');
  if (msgEl) { msgEl.style.opacity = '0'; setTimeout(() => { msgEl.textContent = info.msg; msgEl.style.opacity = '1'; }, 200); }
  if (descEl) { descEl.style.opacity = '0'; setTimeout(() => { descEl.textContent = info.desc; descEl.style.opacity = '1'; }, 200); }
  renderChecklist();
}

function renderChecklist() {
  const el = document.getElementById('loader-checklist');
  if (!el || !_stepLog.length) return;
  el.innerHTML = _stepLog.map(s => {
    const badge = s.done
      ? '<span style="color:#4a7c3a;font-weight:700">✓</span>'
      : '<span style="display:inline-block;animation:sheep-bounce .8s infinite ease-in-out">🔍</span>';
    const color = s.done ? 'color:#4a7c3a' : 'color:var(--ink);font-weight:600';
    return `<div style="${color}">${badge} ${s.icon} ${s.label}</div>`;
  }).join('');
}

let PAUSE = null;
function updatePause(s) {
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
    const lpw = document.getElementById('loader-pause-what');
    if (lpw) {
      const info = STEP_INFO[_lastSeenStep];
      lpw.textContent = info ? (info.label + ' search') : (PAUSE.host ? (PAUSE.host + ' request') : 'request');
    }
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
    const lq = document.getElementById('loader-query');
    if (lq) lq.textContent = s.current_query ? 'Trying name: ' + s.current_query : '';
    updateLoaderStep(s.step || '');

    if (s.state === 'running') {
      if (loaderScreen) loaderScreen.style.display = '';
      if (statusPanel) statusPanel.style.display = 'none';
    } else {
      if (loaderScreen) loaderScreen.style.display = 'none';
      if (statusPanel) statusPanel.style.display = '';
      _stepLog = []; _lastSeenStep = '';
    }

    document.getElementById('st-state').textContent = s.state;
    document.getElementById('st-current').textContent = s.current;
    document.getElementById('st-total').textContent = s.total;
    document.getElementById('st-label').textContent = s.label || '—';
    document.getElementById('st-live').textContent = s.live_records;
    var loaderLive = document.getElementById('loader-live');
    if (loaderLive) loaderLive.innerHTML = s.live_records > 0
      ? '🎉 Found <strong>' + s.live_records + '</strong> record' + (s.live_records === 1 ? '' : 's') + ' so far!'
      : 'Nothing found yet — still looking…';
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
    const stoppedNote = document.getElementById('st-stopped');
    const progressBlock = document.getElementById('st-progress-block');
    if (s.state === 'done') {
      doneBlock.style.display = 'block';
      resetBlock.style.display = 'block';
      if (progressBlock) progressBlock.style.display = 'none';
      document.getElementById('st-rc').textContent = s.record_count;
      document.getElementById('st-new').textContent = s.new_count;
      const plural = document.getElementById('st-rc-plural');
      if (plural) plural.textContent = (s.record_count === 1) ? '' : 's';
      const who = document.getElementById('st-celebrate-who');
      if (who) {
        const name = (s.label || '').split(' (')[0].trim();
        who.textContent = name ? ('about ' + name + '!') : '!';
      }
      const newLine = document.getElementById('st-new-line');
      if (newLine) newLine.style.display = (s.new_count > 0) ? '' : 'none';
      if (stoppedNote) stoppedNote.style.display = s.stopped ? 'block' : 'none';
    } else if (s.state === 'error') {
      doneBlock.style.display = 'none';
      resetBlock.style.display = 'block';
      if (progressBlock) progressBlock.style.display = '';
      if (stoppedNote) stoppedNote.style.display = 'none';
    } else {
      doneBlock.style.display = 'none';
      resetBlock.style.display = 'none';
      if (progressBlock) progressBlock.style.display = '';
      if (stoppedNote) stoppedNote.style.display = 'none';
    }
    if (s.state === 'running') setTimeout(tick, 2000);
  } catch (e) { setTimeout(tick, 3000); }
}
tick();
</script>
{% endif %}
<p class="site-foot"><span class="glyph">&#9752;</span> Sláinte <span class="glyph">&#9752;</span> built with love for the diaspora <span class="glyph">&#9752;</span></p>
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


@app.route("/")
def index():
    try:
        with PEOPLE_LOCK:
            people_rows = _read_rows()
    except Exception:
        people_rows = []
    sites = [{"key": k, "name": v.get("name", k)} for k, v in SITES.items()]
    return render_template_string(INDEX_HTML, run=RUN, people=people_rows,
                                  sites=sites, counties=IRISH_COUNTIES,
                                  nicknames_json=json.dumps(NICKNAMES))


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
    townland = (request.form.get("townland") or "").strip()
    birth_year = (request.form.get("birth_year") or "").strip()
    year_mode = (request.form.get("year_mode") or "approx").strip().lower()
    sex = (request.form.get("sex") or "").strip().upper()
    if sex not in ("M", "F"):
        sex = ""
    if not forename or not surname:
        return redirect(url_for("index"))

    with PEOPLE_LOCK:
        rows = _read_rows()
        key = (forename.lower(), surname.lower())
        existing = next((r for r in rows if (r.get("forename","").lower(), r.get("surname","").lower()) == key), None)
        if existing is None:
            new_row = {c: "" for c in CSV_COLUMNS}
            new_row["forename"] = forename
            new_row["surname"] = surname
            new_row["county"] = county
            new_row["townland"] = townland
            new_row["birth_year_approx"] = birth_year
            if birth_year:
                new_row["year_range"] = "0" if year_mode == "exact" else "5"
            else:
                new_row["year_range"] = ""
            new_row["sex"] = sex
            rows.append(new_row)
            _write_rows(rows)
        else:
            changed = False
            if townland and existing.get("townland","").strip() != townland:
                existing["townland"] = townland; changed = True
            if county and existing.get("county","").strip() != county:
                existing["county"] = county; changed = True
            if changed:
                _write_rows(rows)

    with RUN_LOCK:
        RUN.update({
            "state": "running",
            "current": 0, "total": 1, "label": f"{forename} {surname}".strip(),
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished": None, "error": "",
            "new_count": 0, "record_count": 0,
            "person_name": f"{forename} {surname}".strip(),
            "cancel_requested": False,
            "stopped": False,
        })

    RUNTIME_FLAGS["soundex"] = False
    RUNTIME_FLAGS["collapse_weak"] = True
    RUNTIME_FLAGS["fetch_household"] = bool(request.form.get("fetch_household"))
    census_years = set(request.form.getlist("census_year")) & {"1901", "1911", "1926"}
    ig_events = set(request.form.getlist("ig_event"))
    valid_ev = {"event-birth", "event-marriage", "event-death", "event-baptism", "event-burial"}
    want_griffiths = "griffiths" in ig_events
    want_ig = ig_events & valid_ev
    any_filter = bool(census_years or want_ig or want_griffiths)

    scrape_settings.CENSUS_YEAR_FILTER = census_years if census_years else ""
    scrape_settings.IG_EVENT_FILTER = tuple(want_ig) if want_ig else ()

    skip_list: list[str] = []
    if any_filter:
        if not census_years:
            skip_list.append("nai_census")
        if not want_ig:
            skip_list.append("irishgenealogy_civil")
        if not want_griffiths:
            skip_list.append("griffiths")
    skip_sites = ",".join(skip_list)

    opts = {
        "headless": True,
        "no_cache": False,
        "use_surnames": False,
        "use_nicks": bool(request.form.get("use_nicks")),
        "surname_picks": [],
        "forename_picks": [],
        "only": f"{forename} {surname}",
        "skip": skip_sites,
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
            "stopped": False,
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

# Approximate county centroids (lat, lon). All 32 counties of Ireland.
COUNTY_COORDS = {
    "antrim": (54.90, -6.30), "armagh": (54.35, -6.45),
    "carlow": (52.70, -6.80), "cavan": (54.00, -7.30),
    "clare": (52.90, -9.00), "cork": (51.95, -8.50),
    "derry": (54.90, -7.00), "londonderry": (54.90, -7.00),
    "donegal": (54.90, -8.00), "down": (54.33, -5.90),
    "dublin": (53.35, -6.25), "fermanagh": (54.35, -7.60),
    "galway": (53.30, -9.00), "kerry": (52.10, -9.60),
    "kildare": (53.20, -6.80), "kilkenny": (52.60, -7.25),
    "laois": (52.90, -7.35), "leitrim": (54.10, -8.00),
    "limerick": (52.50, -8.60), "longford": (53.70, -7.80),
    "louth": (53.95, -6.50), "mayo": (54.00, -9.40),
    "meath": (53.60, -6.65), "monaghan": (54.25, -6.95),
    "offaly": (53.20, -7.50), "roscommon": (53.70, -8.30),
    "sligo": (54.15, -8.60), "tipperary": (52.60, -8.00),
    "tyrone": (54.60, -7.30), "waterford": (52.20, -7.60),
    "westmeath": (53.50, -7.50), "wexford": (52.50, -6.55),
    "wicklow": (52.95, -6.30),
}

# Simplified Ireland coastline as (lon, lat) points, roughly clockwise from Malin Head.
IRELAND_OUTLINE = [
    (-7.40, 55.38), (-6.95, 55.18), (-6.03, 55.21), (-5.65, 55.00),
    (-5.85, 54.65), (-5.49, 54.43), (-5.86, 54.21), (-6.00, 54.05),
    (-6.25, 54.08), (-6.07, 53.75), (-6.03, 53.33), (-6.03, 52.99),
    (-6.11, 52.72), (-6.38, 52.18), (-6.95, 52.14), (-7.56, 52.11),
    (-8.14, 51.84), (-8.56, 51.60), (-9.47, 51.44), (-9.95, 51.47),
    (-10.37, 51.99), (-10.16, 52.26), (-9.73, 52.62), (-9.93, 52.95),
    (-9.47, 53.15), (-9.90, 53.42), (-10.18, 53.62), (-9.83, 53.92),
    (-10.08, 54.28), (-9.06, 54.28), (-8.70, 54.49), (-8.70, 54.71),
    (-8.37, 55.17), (-7.95, 55.21), (-7.40, 55.38),
]


AVATAR_PALETTES = [
    ("#c8e6c9", "#1b5e20"),
    ("#bbdefb", "#0d47a1"),
    ("#e1bee7", "#4a148c"),
    ("#ffe0b2", "#bf360c"),
    ("#f8bbd0", "#880e4f"),
    ("#b2dfdb", "#004d40"),
]


def _avatar(name: str) -> tuple[str, str]:
    idx = sum(ord(c) for c in name) % len(AVATAR_PALETTES)
    return AVATAR_PALETTES[idx]


ANCESTORS_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>Our Family &mdash; Family Finder</title>
<style>
  :root {
    --cream:#f4ecd8; --cream-dark:#e8dcc0; --moss:#4a7c3a; --moss-dark:#1f4722;
    --sepia:#8b6f47; --gold:#c9a04a; --ink:#2b2416;
  }
  * { box-sizing:border-box; }
  body {
    font-family:"Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
    max-width:960px; margin:0 auto; padding:0;
    background:var(--cream); color:var(--ink); min-height:100vh;
  }
  .scene { display:block; width:100%; height:auto; border-radius:0; margin-bottom:0; }
  .page-body { padding:1rem; }
  .nav { margin-bottom:.8rem; }
  .nav a { color:var(--moss); text-decoration:none; font-size:1rem; font-weight:600; }
  .nav a:hover { color:var(--moss-dark); text-decoration:underline; }
  h1 { font-size:2.2rem; color:var(--moss-dark); margin:.2rem 0 .1rem; text-align:center; letter-spacing:.02em; }
  .subtitle { text-align:center; color:var(--sepia); font-style:italic; margin:0 0 .6rem; font-size:1.05rem; }
  .summary-bar {
    text-align:center; margin:0 0 1.3rem;
    font-size:.95rem; color:var(--sepia);
    display:flex; justify-content:center; gap:1.2rem; flex-wrap:wrap;
  }
  .summary-bar span { display:inline-flex; align-items:center; gap:.3rem; }
  .filter-tabs { display:flex; gap:.4rem; justify-content:center; margin:0 0 1rem; flex-wrap:wrap; }
  .filter-tabs button {
    cursor:pointer; padding:.35rem 1rem; border-radius:20px; font-size:.9rem;
    border:2px solid var(--cream-dark); background:#fff; color:var(--sepia);
    font-family:inherit; font-weight:600; transition:all .12s;
  }
  .filter-tabs button:hover { border-color:var(--gold); }
  .filter-tabs button.active { background:var(--moss); color:#fff; border-color:var(--moss-dark); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:1.1rem; }
  .card {
    background:#fffcf2; border:2px solid var(--cream-dark); border-radius:14px;
    padding:0; text-align:center; position:relative;
    transition:border-color .15s, box-shadow .15s, transform .14s;
    display:block;
    transform:rotate(var(--tilt, 0deg));
    box-shadow:0 2px 6px rgba(0,0,0,.08);
  }
  .card:nth-child(3n) { --tilt:-.5deg; }
  .card:nth-child(3n+1) { --tilt:.4deg; }
  .card:hover { border-color:var(--gold); box-shadow:0 8px 20px rgba(0,0,0,.18); transform:rotate(0deg) translateY(-3px); }
  .card-confirmed {
    border-color:var(--gold);
    box-shadow:0 2px 10px rgba(201,160,74,.3);
  }
  .card-inner {
    display:block; text-decoration:none; color:var(--ink);
    padding:1.1rem 1rem 1rem;
  }
  .ribbon {
    display:inline-block; font-size:.7rem; letter-spacing:.08em;
    text-transform:uppercase; font-weight:700; color:var(--sepia);
    background:#fff6dc; padding:.1rem .5rem; border-radius:10px;
    margin-bottom:.35rem;
  }
  .card-del {
    position:absolute; top:.35rem; right:.4rem;
    background:none; border:none; cursor:pointer;
    font-size:1.1rem; color:#bbb; line-height:1; padding:.15rem .3rem;
    border-radius:4px; transition:color .12s, background .12s;
  }
  .card-del:hover { color:#b00; background:#ffeaea; }
  .avatar {
    width:66px; height:66px; border-radius:50%; margin:0 auto .75rem;
    display:flex; align-items:center; justify-content:center;
    font-size:1.9rem; font-weight:700; border:3px solid rgba(0,0,0,.07);
    flex-shrink:0;
  }
  .card-name { font-size:1.15rem; font-weight:700; color:var(--moss-dark); margin-bottom:.25rem; line-height:1.2; }
  .card-sub { font-size:.88rem; color:var(--sepia); margin-bottom:.6rem; min-height:1.2em; }
  .badge {
    display:inline-block; padding:.22rem .75rem; border-radius:12px;
    font-size:.82rem; font-weight:700;
  }
  .badge-confirmed { background:#d4edda; color:#155724; }
  .badge-found { background:#d1ecf1; color:#0c5460; }
  .badge-none { background:#f1f1f1; color:#888; font-weight:400; }
  .empty { text-align:center; padding:3rem 1rem; color:var(--sepia); font-style:italic; font-size:1.1rem; }
  .empty a { color:var(--moss); font-weight:700; text-decoration:none; }
  .empty a:hover { text-decoration:underline; }
</style>

<svg class="scene" viewBox="0 0 800 130" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="asky" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#b8d2dc"/><stop offset="60%" stop-color="#dcd6c0"/><stop offset="100%" stop-color="#f0e8d0"/>
    </linearGradient>
    <linearGradient id="ah1" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#6b8a4a"/><stop offset="100%" stop-color="#4a6a3a"/>
    </linearGradient>
    <linearGradient id="ah2" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#85a556"/><stop offset="100%" stop-color="#5d8f3d"/>
    </linearGradient>
    <linearGradient id="athatch" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#d4b06b"/><stop offset="100%" stop-color="#9c7a3e"/>
    </linearGradient>
    <linearGradient id="awall" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="#fbf7ec"/><stop offset="100%" stop-color="#e4d8bc"/>
    </linearGradient>
  </defs>
  <rect width="800" height="130" fill="url(#asky)"/>
  <ellipse cx="200" cy="25" rx="40" ry="5" fill="#fff" opacity=".5"/>
  <ellipse cx="500" cy="35" rx="48" ry="5" fill="#fff" opacity=".4"/>
  <path d="M0 85 Q200 45 400 75 T800 68 L800 130 L0 130 Z" fill="url(#ah1)" opacity=".9"/>
  <path d="M0 100 Q180 78 400 95 T800 90 L800 130 L0 130 Z" fill="url(#ah2)"/>
  <path d="M0 108 L800 103 L800 130 L0 130 Z" fill="#7aa54d"/>
  <!-- drystone wall -->
  <rect x="0" y="106" width="800" height="3" fill="#9c9684"/>
  <g fill="#b5b09e">
    <rect x="30" y="106" width="14" height="3"/>
    <rect x="60" y="106" width="12" height="3"/>
    <rect x="90" y="106" width="16" height="3"/>
    <rect x="310" y="106" width="14" height="3"/>
    <rect x="345" y="106" width="18" height="3"/>
    <rect x="690" y="106" width="16" height="3"/>
    <rect x="735" y="106" width="14" height="3"/>
  </g>
  <!-- haycock -->
  <g transform="translate(50,94)">
    <path d="M-16 14 Q-12 -4 0 -9 Q12 -4 16 14 Z" fill="#c9a961" stroke="#8a6d3a" stroke-width=".5"/>
    <path d="M-8 9 L0 -2" stroke="#8a6d3a" stroke-width=".4"/>
    <path d="M6 10 L2 -3" stroke="#8a6d3a" stroke-width=".4"/>
  </g>
  <!-- hawthorn -->
  <g transform="translate(470,78)">
    <rect x="-2" y="12" width="4" height="18" fill="#4a3220"/>
    <ellipse cx="0" cy="6" rx="14" ry="13" fill="#5a7a3a"/>
    <ellipse cx="-8" cy="0" rx="9" ry="9" fill="#6a8a4a"/>
    <ellipse cx="8" cy="2" rx="10" ry="9" fill="#6a8a4a"/>
  </g>
  <!-- cottage -->
  <g transform="translate(550,72)">
    <rect x="-1" y="40" width="74" height="6" fill="#9c9684"/>
    <rect x="0" y="16" width="72" height="26" fill="url(#awall)" stroke="#a89a78" stroke-width=".8"/>
    <path d="M-8 18 L36 -4 L80 18 L72 22 L0 22 Z" fill="url(#athatch)" stroke="#6b4a2a" stroke-width=".8"/>
    <g stroke="#8a6d3a" stroke-width=".3" opacity=".8">
      <line x1="-4" y1="18" x2="36" y2="-2"/>
      <line x1="6" y1="18" x2="36" y2="-2"/>
      <line x1="16" y1="18" x2="36" y2="-2"/>
      <line x1="26" y1="18" x2="36" y2="-2"/>
      <line x1="40" y1="18" x2="36" y2="-2"/>
      <line x1="50" y1="18" x2="36" y2="-2"/>
      <line x1="60" y1="18" x2="36" y2="-2"/>
      <line x1="70" y1="18" x2="36" y2="-2"/>
    </g>
    <line x1="-8" y1="18" x2="80" y2="18" stroke="#6b4a2a" stroke-width="1"/>
    <rect x="60" y="-9" width="8" height="15" fill="#9c9684" stroke="#5a5a4a" stroke-width=".4"/>
    <rect x="59" y="-10" width="10" height="3" fill="#6b4a2a"/>
    <path d="M64 -12 Q61 -18 64 -24 Q67 -30 63 -36" fill="none" stroke="#c8c8bb" stroke-width="2" opacity=".7" stroke-linecap="round"/>
    <rect x="30" y="24" width="12" height="18" fill="#6b3a1a" stroke="#3a1810" stroke-width=".4"/>
    <line x1="30" y1="33" x2="42" y2="33" stroke="#2a1008" stroke-width=".6"/>
    <rect x="9" y="22" width="12" height="10" fill="#2a3a46" stroke="#5a3a20"/>
    <line x1="15" y1="22" x2="15" y2="32" stroke="#e8dcc0" stroke-width=".5"/>
    <line x1="9" y1="27" x2="21" y2="27" stroke="#e8dcc0" stroke-width=".5"/>
    <rect x="51" y="22" width="12" height="10" fill="#2a3a46" stroke="#5a3a20"/>
    <line x1="57" y1="22" x2="57" y2="32" stroke="#e8dcc0" stroke-width=".5"/>
    <line x1="51" y1="27" x2="63" y2="27" stroke="#e8dcc0" stroke-width=".5"/>
  </g>
  <!-- turf stack -->
  <g transform="translate(660,108)">
    <rect x="0" y="0" width="34" height="12" fill="#5a3a20" stroke="#3a2210" stroke-width=".3"/>
    <g stroke="#3a2210" stroke-width=".3">
      <line x1="8" y1="0" x2="8" y2="12"/>
      <line x1="17" y1="0" x2="17" y2="12"/>
      <line x1="26" y1="0" x2="26" y2="12"/>
      <line x1="0" y1="6" x2="34" y2="6"/>
    </g>
  </g>
  <!-- sheep 1 -->
  <g transform="translate(185,112)">
    <ellipse cx="0" cy="0" rx="13" ry="7" fill="#f0e8d8"/>
    <circle cx="-9" cy="-2" r="4" fill="#f5efe0"/>
    <circle cx="-3" cy="-5" r="4.5" fill="#f5efe0"/>
    <circle cx="4" cy="-5" r="4.5" fill="#f5efe0"/>
    <circle cx="9" cy="-2" r="4" fill="#f5efe0"/>
    <ellipse cx="-13" cy="-1" rx="4.5" ry="4" fill="#3a2a1a"/>
    <circle cx="-14" cy="-2" r=".9" fill="#fff"/>
    <rect x="-5" y="5" width="2" height="4" fill="#3a2a1a"/>
    <rect x="3" y="5" width="2" height="4" fill="#3a2a1a"/>
  </g>
  <!-- sheep 2 -->
  <g transform="translate(250,118)">
    <ellipse cx="0" cy="0" rx="10" ry="6" fill="#f0e8d8"/>
    <circle cx="-6" cy="-3" r="3.5" fill="#f5efe0"/>
    <circle cx="0" cy="-5" r="4" fill="#f5efe0"/>
    <circle cx="6" cy="-3" r="3.5" fill="#f5efe0"/>
    <ellipse cx="-10" cy="-1" rx="4" ry="3.5" fill="#3a2a1a"/>
    <circle cx="-11" cy="-2" r=".8" fill="#fff"/>
    <rect x="-4" y="4" width="2" height="4" fill="#3a2a1a"/>
    <rect x="3" y="4" width="2" height="4" fill="#3a2a1a"/>
  </g>
  <!-- hen -->
  <g transform="translate(330,122)">
    <ellipse cx="0" cy="0" rx="6" ry="4" fill="#8a4a2a"/>
    <circle cx="5" cy="-3" r="2.3" fill="#8a4a2a"/>
    <path d="M4 -5 L6 -7 L8 -5 Z" fill="#c43828"/>
    <path d="M7 -3 L9 -2 L7 -2 Z" fill="#f6b53e"/>
    <circle cx="6" cy="-3" r=".5" fill="#000"/>
    <rect x="-1" y="3" width="1" height="3" fill="#f6b53e"/>
    <rect x="1" y="3" width="1" height="3" fill="#f6b53e"/>
  </g>
  <circle cx="710" cy="26" r="16" fill="#f6d35e" opacity=".75"/>
  <circle cx="710" cy="26" r="22" fill="none" stroke="#f6d35e" stroke-width=".8" opacity=".3"/>
</svg>

<div class="page-body">
<div class="nav">
  <a href="{{ url_for('index') }}">&#8592; Find someone</a>
  <span style="margin:0 .4rem;color:var(--sepia)">&middot;</span>
  <a href="{{ url_for('bookmarks_page') }}">&#128278; Bookmarks</a>
</div>

<h1>&#128106; Our Family</h1>
<p class="subtitle">Everyone we have been searching for</p>

{% if people %}
{% set n_confirmed = people | selectattr('confirmed', 'gt', 0) | list | length %}
{% set n_found = people | selectattr('found', 'gt', 0) | selectattr('confirmed', 'eq', 0) | list | length %}
{% set n_none = people | selectattr('found', 'eq', 0) | list | length %}
<div class="summary-bar">
  {% if n_confirmed %}<span>&#11088; {{ n_confirmed }} confirmed</span>{% endif %}
  {% if n_found %}<span>&#128270; {{ n_found }} with records</span>{% endif %}
  {% if n_none %}<span>&#8203;&#128100; {{ n_none }} not yet searched</span>{% endif %}
</div>
<div class="filter-tabs" id="filter-tabs">
  <button data-filter="all" class="active">All ({{ people|length }})</button>
  <button data-filter="found">&#128214; Found ({{ n_confirmed + n_found }})</button>
  <form method="post" action="{{ url_for('ancestors_wipe') }}" style="display:inline;margin-left:auto">
    <button type="submit" style="background:#c0392b;color:#fff;border:0;border-radius:8px;padding:.35rem .7rem;cursor:pointer;font-family:inherit"
      onclick="return confirm('Wipe EVERYTHING? This deletes every person, every record, and every confirmation. Cannot be undone.')">
      &#9888; Wipe all
    </button>
  </form>
</div>
<div class="grid" id="ancestor-grid">
  {% for p in people %}
  <div class="card{% if p.confirmed > 0 %} card-confirmed{% endif %}"
       data-found="{{ 1 if p.found > 0 else 0 }}"
       style="background:{{ p.tint }}">
    <a class="card-inner" href="{{ url_for('person_page', slug=p.slug) }}">
      <div class="avatar" style="background:{{ p.avatar_bg }};color:{{ p.avatar_fg }}">
        {{ p.silhouette }}
      </div>
      <div class="ribbon">{{ p.era }}</div>
      <div class="card-name">{{ p.forename }} {{ p.surname }}</div>
      <div class="card-sub">
        {%- if p.county %}{{ p.county }}{% endif %}
        {%- if p.year %} &middot; b.&nbsp;~{{ p.year }}{% endif -%}
      </div>
      {% if p.confirmed > 0 %}
        <span class="badge badge-confirmed">&#11088; {{ p.confirmed }} confirmed</span>
      {% elif p.found > 0 %}
        <span class="badge badge-found">&#128270; {{ p.found }} found</span>
      {% else %}
        <span class="badge badge-none">Not searched yet</span>
      {% endif %}
    </a>
    <form method="post" action="{{ url_for('ancestors_delete', slug=p.slug) }}" style="display:inline">
      <button class="card-del" title="Remove {{ p.forename }} {{ p.surname }}"
        onclick="return confirm('Remove {{ p.forename }} {{ p.surname }} from the list?')">&#10005;</button>
    </form>
  </div>
  {% endfor %}
</div>
<script>
  (function(){
    var tabs = document.querySelectorAll('#filter-tabs button');
    var cards = document.querySelectorAll('#ancestor-grid .card');
    tabs.forEach(function(t){
      t.addEventListener('click', function(){
        var f = t.dataset.filter;
        tabs.forEach(function(x){ x.classList.remove('active'); });
        t.classList.add('active');
        cards.forEach(function(c){
          var show = (f === 'all') || c.dataset.found === '1';
          c.style.display = show ? '' : 'none';
        });
      });
    });
  })();
</script>
{% else %}
<div class="empty">
  <p>No one here yet!</p>
  <a href="{{ url_for('index') }}">Go find someone &#8594;</a>
</div>
{% endif %}
</div>
"""



PERSON_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>{{ person.forename }} {{ person.surname }} &mdash; Family Finder</title>
<style>
  :root {
    --cream:#f4ecd8; --cream-dark:#e8dcc0; --moss:#4a7c3a; --moss-dark:#1f4722;
    --sepia:#8b6f47; --gold:#c9a04a; --ink:#2b2416;
  }
  * { box-sizing:border-box; }
  body {
    font-family:"Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
    max-width:860px; margin:0 auto; padding:1rem;
    background:var(--cream); color:var(--ink); min-height:100vh;
  }
  .nav { margin-bottom:1rem; display:flex; gap:1rem; align-items:center; flex-wrap:wrap; }
  .nav a { color:var(--moss); text-decoration:none; font-weight:600; }
  .nav a:hover { text-decoration:underline; }
  .hero-card {
    background:#fffcf2; border:2px solid var(--gold); border-radius:12px;
    padding:1.2rem 1.4rem; margin-bottom:1.2rem; display:flex; align-items:center; gap:1rem;
  }
  .avatar {
    width:72px; height:72px; border-radius:50%; flex-shrink:0;
    display:flex; align-items:center; justify-content:center;
    font-size:2.1rem; font-weight:700; border:3px solid rgba(0,0,0,.07);
  }
  .hero-name { font-size:1.75rem; font-weight:700; color:var(--moss-dark); margin:0 0 .2rem; }
  .hero-sub { color:var(--sepia); font-size:1rem; }
  .tier { margin:1rem 0; }
  .tier-head {
    font-weight:700; font-size:1.05rem; margin-bottom:.35rem;
    padding:.35rem .7rem; border-radius:6px; display:inline-block;
  }
  .tier-blurb { font-size:.88rem; color:#666; margin-bottom:.4rem; }
  .rec-card {
    background:#fff; border-left:4px solid #ccc; padding:.7rem 1rem;
    margin:.4rem 0; border-radius:4px; font-size:.98rem; line-height:1.5;
  }
  a.src { color:var(--moss); font-size:.9rem; margin-left:.6rem; }
  .badge-confirmed { font-size:.8rem; color:#155724; font-weight:700; margin-left:.5rem; }
  .empty { text-align:center; padding:2rem; color:var(--sepia); font-style:italic; font-size:1.05rem; }
  .search-btn {
    display:inline-block; margin-top:1rem; padding:.65rem 1.5rem;
    background:var(--moss); color:#fff; border-radius:8px; text-decoration:none;
    font-weight:700; font-size:1rem;
  }
  .search-btn:hover { background:var(--moss-dark); }
  .summary { color:var(--sepia); font-style:italic; margin:.2rem 0 1rem; }
  .fun-fact {
    background:linear-gradient(135deg,#fff6d8 0%,#ffe7a8 100%);
    border:2px dashed var(--gold); border-radius:12px;
    padding:.8rem 1rem; margin:0 0 1rem; font-size:1.02rem;
    display:flex; align-items:center; gap:.7rem;
  }
  .fun-fact .ff-icon { font-size:2rem; line-height:1; }
  .fun-fact .ff-text { flex:1; }
  .fun-fact .ff-text strong { color:var(--moss-dark); }
  .badges {
    display:flex; flex-wrap:wrap; gap:.5rem; margin:.2rem 0 1rem;
  }
  .badge {
    background:#fff; border:2px solid var(--cream-dark); border-radius:20px;
    padding:.35rem .8rem; font-size:.95rem; font-weight:600;
    display:inline-flex; align-items:center; gap:.4rem; color:var(--ink);
  }
  .badge.zero { opacity:.35; }
  .badge .b-ico { font-size:1.1rem; }
  .badge .b-num { background:var(--moss); color:#fff; border-radius:12px;
    padding:0 .55rem; font-size:.85rem; min-width:1.5rem; text-align:center; }
  .badge.zero .b-num { background:#bbb; }
  .timeline-wrap {
    background:#fffcf2; border:2px solid var(--cream-dark); border-radius:12px;
    padding:1rem 1.2rem .8rem; margin:0 0 1.2rem;
  }
  .timeline-title {
    font-weight:700; color:var(--moss-dark); margin-bottom:.6rem; font-size:1rem;
  }
  .timeline-title .tl-hint { font-weight:400; color:var(--sepia); font-size:.85rem; margin-left:.4rem; }
  .timeline {
    position:relative; height:70px; margin:0 .5rem;
  }
  .tl-track {
    position:absolute; left:0; right:0; top:34px; height:4px;
    background:linear-gradient(90deg,var(--sepia),var(--gold),var(--moss));
    border-radius:2px;
  }
  .tl-dot {
    position:absolute; top:22px; transform:translateX(-50%);
    width:28px; height:28px; border-radius:50%; background:#fff;
    border:3px solid var(--moss); cursor:pointer; text-decoration:none;
    display:flex; align-items:center; justify-content:center;
    font-size:1rem; transition:transform .15s, box-shadow .15s;
    box-shadow:0 2px 4px rgba(0,0,0,.15);
  }
  .tl-dot:hover { transform:translateX(-50%) scale(1.25); box-shadow:0 3px 8px rgba(0,0,0,.25); z-index:2; }
  .tl-dot .tl-year {
    position:absolute; bottom:-22px; left:50%; transform:translateX(-50%);
    font-size:.78rem; color:var(--sepia); white-space:nowrap; font-weight:600;
  }
  .tl-ends { display:flex; justify-content:space-between; font-size:.82rem; color:var(--sepia); margin-top:.3rem; }
  .map-wrap {
    background:#eaf3ea; border:2px solid var(--moss); border-radius:12px;
    padding:1rem; margin:0 0 1.2rem; display:flex; gap:1rem; align-items:center; flex-wrap:wrap;
  }
  .map-svg { flex:0 0 auto; }
  .map-svg svg { display:block; max-width:180px; height:auto; }
  .map-coast { fill:#c8e0c3; stroke:var(--moss-dark); stroke-width:1.5; stroke-linejoin:round; }
  .map-dot { fill:#7ea974; opacity:.55; }
  .map-dot-target {
    fill:#d9472a; stroke:#fff; stroke-width:2;
    filter:drop-shadow(0 1px 2px rgba(0,0,0,.4));
    animation:pulseMap 1.8s ease-in-out infinite;
    transform-origin:center;
            transform-box:fill-box;
  }
  @keyframes pulseMap {
    0%,100% { transform:scale(1); }
    50%     { transform:scale(1.4); }
  }
  .map-label {
    font-family:"Palatino Linotype",Georgia,serif;
    font-weight:700; font-size:11px; fill:#d9472a;
    paint-order:stroke; stroke:#fff; stroke-width:3;
  }
  .map-text { flex:1; min-width:180px; }
  .map-text h3 { margin:0 0 .4rem; color:var(--moss-dark); font-size:1.1rem; }
  .map-text p { margin:.2rem 0; font-size:.95rem; color:var(--ink); }
  .map-text .map-pin { font-size:2.2rem; line-height:1; display:inline-block; vertical-align:middle; }
  .trophy-wrap {
    background:linear-gradient(135deg,#fff4cc 0%,#ffe08a 55%,#ffd04a 100%);
    border:3px solid var(--gold); border-radius:14px;
    padding:1rem 1.2rem 1.1rem; margin:0 0 1.3rem;
    box-shadow:0 4px 14px rgba(201,160,74,.35);
    position:relative; overflow:hidden;
  }
  .trophy-wrap::before {
    content:""; position:absolute; top:-30px; right:-30px;
    width:120px; height:120px; background:radial-gradient(circle,#fff6d8,transparent 70%);
    pointer-events:none;
  }
  .trophy-title {
    font-size:1.3rem; font-weight:700; color:var(--moss-dark);
    display:flex; align-items:center; gap:.5rem; margin-bottom:.2rem;
  }
  .trophy-title .t-ico { font-size:1.7rem; animation:bounce 1.6s ease-in-out infinite; display:inline-block; }
  @keyframes bounce {
    0%,100% { transform:translateY(0); }
    50%     { transform:translateY(-5px); }
  }
  .trophy-sub { color:#6b5a1f; font-size:.92rem; margin-bottom:.7rem; font-style:italic; }
  .trophy-card {
    background:#fffef7; border:2px solid var(--gold); border-radius:10px;
    padding:.8rem 1rem; margin:.5rem 0; font-size:1rem; line-height:1.5;
    position:relative; padding-left:3rem;
  }
  .trophy-card::before {
    content:"⭐"; position:absolute; left:.8rem; top:.7rem; font-size:1.5rem;
  }
  .trophy-card a.src { color:var(--moss-dark); font-weight:600; }
  .trophy-card .ribbon {
    display:inline-block; background:var(--moss); color:#fff;
    font-size:.72rem; font-weight:700; padding:.15rem .5rem; border-radius:10px;
    margin-left:.4rem; vertical-align:middle; letter-spacing:.3px;
  }
  .rec-card.confirmed-card {
    background:#fffef7; border:2px solid var(--gold); border-left-width:6px;
    box-shadow:0 2px 6px rgba(201,160,74,.25);
  }
  .rec-card.confirmed-card::after {
    content:"⭐ confirmed"; float:right; background:var(--gold); color:#fff;
    font-size:.75rem; font-weight:700; padding:.15rem .55rem; border-radius:10px;
    margin-left:.5rem;
  }
  .rec-card:target {
    background:#fff6d8; border-left-width:6px;
    animation:flash 1.2s ease-out;
  }
  @keyframes flash {
    0% { background:#ffe7a8; }
    100% { background:#fff6d8; }
  }
</style>

<div class="nav">
  <a href="{{ url_for('ancestors_page') }}">&#8592; Our family</a>
</div>

<div class="hero-card">
  <div class="avatar" style="background:{{ person.avatar_bg }};color:{{ person.avatar_fg }}">
    {{ person.forename[0]|upper }}
  </div>
  <div>
    <div class="hero-name">{{ person.forename }} {{ person.surname }}</div>
    <div class="hero-sub">
      {%- if person.county %}{{ person.county }}{% endif %}
      {%- if person.year %} &middot; born around {{ person.year }}{% endif -%}
    </div>
  </div>
</div>

{% if map_data and map_data.target %}
<div class="map-wrap">
  <div class="map-svg">
    <svg width="180" height="240" viewBox="0 0 {{ map_data.w }} {{ map_data.h }}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Map of Ireland showing {{ map_data.county_label }}">
      <polygon class="map-coast" points="{{ map_data.outline }}"/>
      {% for d in map_data.dots %}
        {% if not d.target %}<circle class="map-dot" cx="{{ d.x }}" cy="{{ d.y }}" r="2.5"/>{% endif %}
      {% endfor %}
      <circle class="map-dot-target" cx="{{ map_data.target.x }}" cy="{{ map_data.target.y }}" r="6"/>
      <text class="map-label" x="{{ map_data.target.x + 9 }}" y="{{ map_data.target.y + 4 }}">{{ map_data.target.name }}</text>
    </svg>
  </div>
  <div class="map-text">
    <h3><span class="map-pin">📍</span> From {{ map_data.county_label }}</h3>
    <p>{{ person.forename }}'s family came from <strong>County {{ map_data.county_label }}</strong> in Ireland.</p>
    <p style="color:var(--sepia);font-size:.88rem">The red dot shows where on the island. Those green dots are all the other counties.</p>
  </div>
</div>
{% endif %}

{% if fun_fact %}
<div class="fun-fact">
  <div class="ff-icon">🕰️</div>
  <div class="ff-text"><strong>Did you know?</strong> {{ fun_fact }}</div>
</div>
{% endif %}

{% if total == 0 %}
<div class="empty">
  <p>We have not searched for {{ person.forename }} yet.</p>
  <a href="{{ url_for('index') }}" class="search-btn">&#128269; Search for {{ person.forename }}</a>
</div>
{% else %}
{% if confirmed_cards %}
<div class="trophy-wrap">
  <div class="trophy-title"><span class="t-ico">🏆</span> {{ confirmed_cards|length }} confirmed match{{ 'es' if confirmed_cards|length != 1 else '' }}!</div>
  <div class="trophy-sub">You already said these are definitely {{ person.forename }}. Nice work!</div>
  {% for r in confirmed_cards %}
  <div class="trophy-card">
    <a href="#{{ r.anchor }}" style="color:inherit;text-decoration:none">{{ r.sentence|safe }}</a>
    <span class="ribbon">{{ tier_stars[r.tier] }} {{ r.tier }}</span>
    {% if r.link %}<a class="src" href="{{ r.link }}" target="_blank">open source &#8599;</a>{% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}
<div class="badges">
  {% for key, icon, label in bucket_meta %}
    {% set n = type_counts.get(key, 0) %}
    <span class="badge {% if n == 0 %}zero{% endif %}" title="{{ n }} {{ label }}">
      <span class="b-ico">{{ icon }}</span>
      <span>{{ label }}</span>
      <span class="b-num">{{ n }}</span>
    </span>
  {% endfor %}
</div>

{% if timeline %}
<div class="timeline-wrap">
  <div class="timeline-title">📅 Life timeline <span class="tl-hint">tap a dot to jump to that record</span></div>
  <div class="timeline">
    <div class="tl-track"></div>
    {% for d in timeline %}
      {% set emoji = {'census':'🏘️','birth':'🎂','marriage':'💍','death':'⚰️','land':'🗺️','other':'📜'}[d.bucket] %}
      <a class="tl-dot" href="#{{ d.anchor }}" style="left:{{ d.pct }}%;border-color:{{ tier_color[d.tier] }}" title="{{ d.year }} — {{ d.bucket }}">
        <span>{{ emoji }}</span>
        <span class="tl-year">{{ d.year }}</span>
      </a>
    {% endfor %}
  </div>
  <div class="tl-ends"><span>{{ tl_min }}</span><span>{{ tl_max }}</span></div>
</div>
{% endif %}

<p class="summary">Found {{ total }} record(s) in the old books.</p>
{% for tier in tier_order %}
  {% set recs = tiers.get(tier, []) %}
  {% if recs %}
  <div class="tier">
    <div class="tier-head" style="color:{{ tier_color[tier] }}">
      {{ tier_stars[tier] }} {{ tier }} &mdash; {{ recs|length }} record(s)
    </div>
    <p class="tier-blurb">{{ tier_blurb[tier] }}</p>
    {% for r in recs %}
    <div class="rec-card {% if r.status == 'confirmed' %}confirmed-card{% endif %}" id="{{ r.anchor }}" style="border-left-color:{{ tier_color[tier] }}">
      {{ r.sentence|safe }}
      {% if r.link %}<a class="src" href="{{ r.link }}" target="_blank">open source &#8599;</a>{% endif %}
    </div>
    {% endfor %}
  </div>
  {% endif %}
{% endfor %}
{% endif %}
"""


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
    return redirect(url_for("results_html"))


def _narrative_legacy():
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
    "father", "mother", "spouse", "notes", "sex",
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


_THEME_EXTRA = """<style>
  html {
    background:
      linear-gradient(180deg, rgba(0,155,58,.55) 0, rgba(0,155,58,.55) 6px,
                              rgba(255,255,255,.75) 6px, rgba(255,255,255,.75) 12px,
                              rgba(255,136,0,.55) 12px, rgba(255,136,0,.55) 18px,
                              transparent 18px),
      radial-gradient(1200px 500px at 50% -10%, #fff7df 0%, transparent 60%),
      radial-gradient(900px 600px at 10% 110%, #efe3c2 0%, transparent 55%),
      #f4ecd8;
    background-attachment: fixed;
  }
  body { background: transparent !important; padding-top: 1.4rem !important; }
  .site-foot {
    text-align:center; margin: 1.6rem 0 .3rem; color:#8b6f47;
    font-style: italic; font-size:.95rem; letter-spacing:.02em;
  }
  .site-foot .glyph { color:#1f4722; font-style:normal; margin: 0 .35rem; }
</style>"""
_THEME_FOOT = ('<p class="site-foot"><span class="glyph">&#9752;</span> '
               'Sl&aacute;inte <span class="glyph">&#9752;</span> '
               'built with love for the diaspora '
               '<span class="glyph">&#9752;</span></p>')
for _name in ("ANCESTORS_HTML", "PERSON_HTML", "NARRATIVE_HTML", "PEOPLE_HTML"):
    globals()[_name] = _THEME_EXTRA + globals()[_name] + _THEME_FOOT


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
        gone = rows.pop(idx)
        _write_rows(rows)
    label = f"{gone.get('forename','').strip()} {gone.get('surname','').strip()}".strip()
    if label:
        _purge_person_trail(label)
    return Response(status=204)


def _purge_person_trail(label: str) -> None:
    """Strip rows from results.csv and entries from confirmations.json that
    belong to a deleted person. Keeps 'Our Family' counts honest."""
    results_csv_path = HERE / RESULTS_CSV
    kept_records = []
    dropped_keys: set[str] = set()
    if results_csv_path.exists():
        try:
            records = load_records_csv(results_csv_path)
            for r in records:
                if r.person_searched == label:
                    dropped_keys.add(record_key(r))
                else:
                    kept_records.append(r)
            write_csv(kept_records, results_csv_path)
            write_html(kept_records, HERE / RESULTS_HTML)
        except Exception:
            logging.getLogger("genealogy").exception("Purge results.csv failed")
    with CONFIRMS_LOCK:
        confs = load_confirmations(CONFIRMS_FILE)
        changed = False
        for k in list(confs.keys()):
            entry = confs.get(k) or {}
            if k in dropped_keys or entry.get("person") == label:
                confs.pop(k, None)
                changed = True
        if changed:
            save_confirmations(CONFIRMS_FILE, confs)


@app.route("/ancestors")
def ancestors_page():
    with PEOPLE_LOCK:
        people_rows = _read_rows()
    with CONFIRMS_LOCK:
        confs = load_confirmations(CONFIRMS_FILE)

    record_counts: dict[str, int] = {}
    confirmed_counts: dict[str, int] = {}
    results_csv_path = HERE / RESULTS_CSV
    if results_csv_path.exists():
        try:
            recs = load_records_csv(results_csv_path)
            for r in recs:
                lbl = r.person_searched
                record_counts[lbl] = record_counts.get(lbl, 0) + 1
                if confs.get(record_key(r), {}).get("status") == "confirmed":
                    confirmed_counts[lbl] = confirmed_counts.get(lbl, 0) + 1
        except Exception:
            pass

    people_data = []
    for row in people_rows:
        forename = row.get("forename", "").strip()
        surname = row.get("surname", "").strip()
        if not forename or not surname:
            continue
        label = f"{forename} {surname}"
        bg, fg = _avatar(label)
        year = row.get("birth_year_approx", "").strip()
        tint, era = _era_for_year(year)
        people_data.append({
            "label": label,
            "forename": forename,
            "surname": surname,
            "slug": slugify(label),
            "county": row.get("county", "").strip(),
            "year": year,
            "found": record_counts.get(label, 0),
            "confirmed": confirmed_counts.get(label, 0),
            "avatar_bg": bg,
            "avatar_fg": fg,
            "tint": tint,
            "era": era,
            "silhouette": _silhouette(row.get("sex", "")),
        })
    people_data.sort(key=lambda p: (-p["confirmed"], -p["found"], p["label"]))
    return render_template_string(ANCESTORS_HTML, people=people_data)


def _era_for_year(year_str: str) -> tuple[str, str]:
    try:
        y = int(str(year_str).strip()[:4])
    except (ValueError, TypeError):
        return ("#fffcf2", "Long ago")
    if y < 1845:
        return ("#f0e4cc", "Before the Famine")
    if y <= 1852:
        return ("#e8dcc0", "The Great Hunger")
    if y < 1880:
        return ("#ece0bf", "After the Famine")
    if y < 1900:
        return ("#e3dcc1", "Land War Ireland")
    if y < 1917:
        return ("#dce5c8", "Home Rule era")
    if y < 1923:
        return ("#cfe0c5", "Rising & Revolution")
    if y < 1949:
        return ("#d4e4cc", "Irish Free State")
    return ("#cfe0d1", "Republic of Ireland")


def _silhouette(sex: str) -> str:
    s = (sex or "").strip().upper()
    if s == "M":
        return "\U0001F468"  # man
    if s == "F":
        return "\U0001F469"  # woman
    return "\U0001F464"  # bust silhouette


BOOKMARKS_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>Bookmarks &mdash; Family Finder</title>
<style>
  :root { --cream:#f4ecd8; --cream-dark:#e8dcc0; --moss:#4a7c3a; --moss-dark:#1f4722;
          --sepia:#8b6f47; --gold:#c9a04a; --ink:#2b2416; }
  body { font-family:"Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
         max-width:960px; margin:0 auto; padding:1rem; background:var(--cream); color:var(--ink); }
  .nav a { color:var(--moss); text-decoration:none; font-weight:600; }
  .nav a:hover { text-decoration:underline; }
  h1 { color:var(--moss-dark); margin:.4rem 0 .2rem; }
  .subtitle { color:var(--sepia); font-style:italic; margin:0 0 1rem; }
  .group { background:#fff; border:1px solid var(--cream-dark); border-radius:10px;
           padding:.8rem 1rem; margin:.8rem 0; }
  .group h2 { margin:.1rem 0 .5rem; color:var(--moss-dark); font-size:1.2rem; }
  .rec { border-left:4px solid var(--gold); padding:.4rem .7rem; margin:.4rem 0;
         background:#fff8e8; border-radius:4px; }
  .rec a.src { color:var(--moss); font-size:.85rem; margin-left:.4rem; }
  .meta { color:var(--sepia); font-size:.85rem; }
  .empty { color:var(--sepia); font-style:italic; text-align:center; padding:2rem; }
  button.unkeep { background:#fff; border:1px solid #bbb; border-radius:6px;
                  padding:.15rem .5rem; font-size:.8rem; cursor:pointer; margin-left:.5rem; }
  button.unkeep:hover { background:#f4f4f4; }
</style>
<div class="nav">
  <a href="{{ url_for('index') }}">&#8592; Find someone</a>
  <span style="margin:0 .4rem;color:var(--sepia)">&middot;</span>
  <a href="{{ url_for('ancestors_page') }}">&#128106; Our Family</a>
</div>
<h1>&#128278; Bookmarks</h1>
<p class="subtitle">Records you kept &mdash; interesting, but not confirmed family.</p>

{% if not groups %}
  <div class="empty">
    No bookmarks yet. In the results, tap <strong>&#128278; Keep</strong> on any
    record that is worth remembering but isn't your ancestor.
  </div>
{% endif %}

{% for surname, items in groups %}
<div class="group">
  <h2>{{ surname }} <span class="meta">({{ items|length }})</span></h2>
  {% for r in items %}
  <div class="rec" data-key="{{ r.key }}">
    <strong>{{ r.name_found or 'Unknown' }}</strong>
    {% if r.record_type %} &middot; {{ r.record_type }}{% endif %}
    {% if r.age_or_year %} &middot; {{ r.age_or_year }}{% endif %}
    {% if r.place %} &middot; {{ r.place }}{% endif %}
    {% if r.link %}<a class="src" href="{{ r.link }}" target="_blank">open &#8599;</a>{% endif %}
    <button class="unkeep" data-key="{{ r.key }}" data-person="{{ r.person_searched }}">remove</button>
    <div class="meta">searched as {{ r.person_searched }} &middot; {{ r.site }}</div>
  </div>
  {% endfor %}
</div>
{% endfor %}

<script>
document.querySelectorAll('button.unkeep').forEach(function(b){
  b.addEventListener('click',function(){
    if(!confirm('Remove this bookmark?'))return;
    fetch('/confirm',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:b.dataset.key,status:'',person:b.dataset.person})})
    .then(function(r){if(r.ok){b.closest('.rec').remove();}
      else r.text().then(function(t){alert('Failed: '+t);});});
  });
});
</script>
"""


@app.route("/bookmarks")
def bookmarks_page():
    with CONFIRMS_LOCK:
        confs = load_confirmations(CONFIRMS_FILE)
    saved_keys = {k for k, v in confs.items() if v.get("status") == "saved"}
    groups: dict[str, list[dict]] = {}
    results_csv_path = HERE / RESULTS_CSV
    if saved_keys and results_csv_path.exists():
        try:
            recs = load_records_csv(results_csv_path)
            for r in recs:
                k = record_key(r)
                if k not in saved_keys:
                    continue
                surname = (r.person_searched.split()[-1]
                           if r.person_searched else "Other")
                groups.setdefault(surname, []).append({
                    "key": k,
                    "name_found": r.name_found,
                    "record_type": r.record_type,
                    "age_or_year": r.age_or_year,
                    "place": r.place,
                    "link": r.link,
                    "site": r.site,
                    "person_searched": r.person_searched,
                })
        except Exception:
            logging.getLogger("genealogy").exception("bookmarks load failed")
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return render_template_string(BOOKMARKS_HTML, groups=ordered)


@app.route("/ancestors/<slug>/delete", methods=["POST"])
def ancestors_delete(slug: str):
    purged: list[str] = []
    with PEOPLE_LOCK:
        rows = _read_rows()
        kept = []
        for row in rows:
            forename = row.get("forename", "").strip()
            surname = row.get("surname", "").strip()
            label = f"{forename} {surname}".strip()
            if slugify(label) != slug:
                kept.append(row)
            else:
                purged.append(label)
        _write_rows(kept)
    for lbl in purged:
        if lbl:
            _purge_person_trail(lbl)
    return redirect(url_for("ancestors_page"))


@app.route("/ancestors/wipe", methods=["POST"])
def ancestors_wipe():
    """Nuke everything: ancestors.csv, results.csv/html/json, confirmations.json."""
    with PEOPLE_LOCK:
        _write_rows([])
    for name in (RESULTS_CSV, RESULTS_HTML, RESULTS_JSON, RESULTS_SUMMARY):
        p = HERE / name
        try:
            if p.exists():
                p.unlink()
        except Exception:
            logging.getLogger("genealogy").exception("wipe: could not remove %s", name)
    with CONFIRMS_LOCK:
        try:
            if CONFIRMS_FILE.exists():
                CONFIRMS_FILE.unlink()
        except Exception:
            logging.getLogger("genealogy").exception("wipe: could not remove confirmations.json")
    return redirect(url_for("ancestors_page"))


@app.route("/person/<slug>")
def person_page(slug: str):
    import json as _json
    with PEOPLE_LOCK:
        rows = _read_rows()

    target = None
    for row in rows:
        forename = row.get("forename", "").strip()
        surname = row.get("surname", "").strip()
        if not forename or not surname:
            continue
        label = f"{forename} {surname}"
        if slugify(label) == slug:
            bg, fg = _avatar(label)
            target = {
                "label": label,
                "forename": forename,
                "surname": surname,
                "county": row.get("county", "").strip(),
                "year": row.get("birth_year_approx", "").strip(),
                "avatar_bg": bg,
                "avatar_fg": fg,
            }
            break
    if target is None:
        abort(404)

    import re as _re
    tiers: dict[str, list[dict]] = {t: [] for t in TIER_ORDER}
    years: list[int] = []
    type_counts = {"census": 0, "birth": 0, "marriage": 0, "death": 0, "land": 0, "other": 0}
    rec_id = 0
    p = HERE / RESULTS_JSON
    if p.exists():
        try:
            payload = _json.loads(p.read_text(encoding="utf-8"))
            for entry in payload.get("people", []):
                if entry.get("person") == target["label"]:
                    for rec in entry.get("records", []):
                        if rec.get("status") == "rejected":
                            continue
                        tier = rec.get("tier") or "Manual"
                        if tier not in tiers:
                            tiers[tier] = []
                        rec_id += 1
                        anchor = f"r{rec_id}"
                        rtype_raw = (rec.get("record_type") or "").lower()
                        site_raw = (rec.get("site") or "").lower()
                        if "census" in rtype_raw or "census" in site_raw:
                            bucket = "census"
                        elif "birth" in rtype_raw or "baptism" in rtype_raw:
                            bucket = "birth"
                        elif "marriage" in rtype_raw or "wedding" in rtype_raw:
                            bucket = "marriage"
                        elif "death" in rtype_raw or "burial" in rtype_raw:
                            bucket = "death"
                        elif "griff" in site_raw or "valuation" in rtype_raw or "land" in rtype_raw:
                            bucket = "land"
                        else:
                            bucket = "other"
                        type_counts[bucket] += 1
                        ym = _re.search(r"\b(1[6-9]\d\d|20\d\d)\b", (rec.get("age_or_year") or ""))
                        year_int = int(ym.group(1)) if ym else None
                        if year_int:
                            years.append(year_int)
                        card = {
                            "sentence": _narrate(rec),
                            "link": rec.get("link", ""),
                            "status": rec.get("status", ""),
                            "anchor": anchor,
                            "year": year_int,
                            "bucket": bucket,
                            "tier": tier,
                        }
                        tiers[tier].append(card)
        except Exception:
            pass

    total = sum(len(v) for v in tiers.values())
    confirmed_cards = [c for tier in TIER_ORDER for c in tiers.get(tier, []) if c.get("status") == "confirmed"]

    timeline = []
    if years:
        y_min, y_max = min(years), max(years)
        span = max(1, y_max - y_min)
        for tier in TIER_ORDER:
            for r in tiers.get(tier, []):
                if r["year"] is None:
                    continue
                timeline.append({
                    "year": r["year"],
                    "pct": round((r["year"] - y_min) / span * 100, 2),
                    "anchor": r["anchor"],
                    "bucket": r["bucket"],
                    "tier": tier,
                })
        timeline.sort(key=lambda x: x["year"])
    tl_min = min(years) if years else None
    tl_max = max(years) if years else None

    fun_fact = ""
    try:
        by = int((target.get("year") or "").strip()[:4])
    except (ValueError, TypeError):
        by = None
    if by:
        age = 2026 - by
        if by < 1800:
            era = "before the steam train came to Ireland"
        elif by < 1845:
            era = "before the Great Famine of 1845"
        elif by < 1900:
            era = "when Ireland had no cars, no planes, and no TV"
        elif by < 1922:
            era = "before Ireland became an independent country"
        else:
            era = "in modern times"
        fun_fact = f"Born around {by} — that was {age} years ago, {era}."

    bucket_meta = [
        ("census", "🏘️", "census"),
        ("birth", "🎂", "births"),
        ("marriage", "💍", "marriages"),
        ("death", "⚰️", "deaths"),
        ("land", "🗺️", "land records"),
        ("other", "📜", "other"),
    ]

    map_data = None
    county_key = (target.get("county") or "").strip().lower()
    county_key = county_key.replace("county ", "").replace("co ", "").replace(".", "").strip()
    county_key = county_key.split()[0] if county_key else ""
    MAP_W, MAP_H = 240, 320
    LON_MIN, LON_MAX = -10.6, -5.3
    LAT_MIN, LAT_MAX = 51.35, 55.55

    def _project(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - LON_MIN) / (LON_MAX - LON_MIN) * MAP_W
        y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * MAP_H
        return (round(x, 1), round(y, 1))

    outline_pts = " ".join(f"{x},{y}" for x, y in (_project(lon, lat) for lon, lat in IRELAND_OUTLINE))
    dots = []
    target_dot = None
    for name, (lat, lon) in COUNTY_COORDS.items():
        if name == "londonderry":
            continue
        x, y = _project(lon, lat)
        is_target = (name == county_key) or (county_key == "londonderry" and name == "derry")
        dots.append({"name": name.capitalize(), "x": x, "y": y, "target": is_target})
        if is_target:
            target_dot = {"name": name.capitalize(), "x": x, "y": y}
    if dots:
        map_data = {
            "w": MAP_W, "h": MAP_H,
            "outline": outline_pts,
            "dots": dots,
            "target": target_dot,
            "county_label": (target.get("county") or "").strip(),
        }

    return render_template_string(
        PERSON_HTML,
        person=target,
        tiers=tiers,
        total=total,
        tier_order=TIER_ORDER,
        tier_stars=TIER_STARS,
        tier_blurb=TIER_BLURB,
        tier_color=TIER_COLOR,
        timeline=timeline,
        tl_min=tl_min,
        tl_max=tl_max,
        fun_fact=fun_fact,
        type_counts=type_counts,
        bucket_meta=bucket_meta,
        map_data=map_data,
        confirmed_cards=confirmed_cards,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
