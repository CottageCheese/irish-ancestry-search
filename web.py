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

from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from playwright.sync_api import sync_playwright

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
}
RUN_LOCK = threading.Lock()


INDEX_HTML = """
<!doctype html>
<meta charset="utf-8">
<title>Irish genealogy search</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
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
<form method="post" action="{{ url_for('start') }}" enctype="multipart/form-data">
  <label for="csv">Ancestors CSV (leave empty to reuse existing ancestors.csv)</label>
  <input type="file" name="csv" id="csv" accept=".csv">

  <label for="only">Only (comma-separated person labels, optional)</label>
  <input type="text" name="only" id="only" placeholder="Furey,John">

  <label for="skip">Skip sites (comma-separated)</label>
  <input type="text" name="skip" id="skip" placeholder="griffiths">

  <div class="row" style="margin-top:.8rem">
    <label><input type="checkbox" name="headless" checked> Headless (no visible browser)</label>
    <label><input type="checkbox" name="no_cache"> Ignore cache</label>
  </div>

  <br>
  <button type="submit">Start search</button>
</form>
{% else %}
<div class="state">
  <strong>State:</strong> <span id="st-state">{{ run.state }}</span><br>
  <strong>Progress:</strong> <span id="st-current">{{ run.current }}</span> / <span id="st-total">{{ run.total }}</span>
  &mdash; <span id="st-label">{{ run.label }}</span>
  <div class="bar"><div id="st-bar" style="width: {{ (100 * run.current / run.total) if run.total else 0 }}%"></div></div>
  <div style="margin-top:.4rem">
    <strong>Records so far:</strong> <span id="st-live">{{ run.live_records }}</span>
    &nbsp;|&nbsp; <strong>Last site:</strong> <span id="st-site">{{ run.last_site or '—' }}</span>
  </div>
  {% if run.started %}<div><small>Started {{ run.started }}</small></div>{% endif %}
  <div><small id="st-finished">{% if run.finished %}Finished {{ run.finished }}{% endif %}</small></div>
  <div id="st-error" style="color:#b00">{% if run.error %}<strong>Error:</strong> {{ run.error }}{% endif %}</div>

  <details open style="margin-top:.8rem">
    <summary>Live log</summary>
    <pre id="st-log" style="background:#111;color:#cfc;padding:.6rem;border-radius:4px;max-height:220px;overflow:auto;font-size:.8rem;margin:.4rem 0 0">{{ '(waiting…)' }}</pre>
  </details>

  <div id="st-done" style="display:{{ 'block' if run.state == 'done' else 'none' }}">
    <div><strong><span id="st-rc">{{ run.record_count }}</span></strong> unique record(s).
         <span id="st-new">{{ run.new_count }}</span> new since previous run.</div>
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
    document.getElementById('st-bar').style.width = (s.total ? (100 * s.current / s.total) : 0) + '%';
    document.getElementById('st-finished').textContent = s.finished ? ('Finished ' + s.finished) : '';
    document.getElementById('st-error').innerHTML = s.error ? ('<strong>Error:</strong> ' + s.error) : '';
    document.getElementById('st-log').textContent = (l.lines || []).join('\n') || '(waiting…)';
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
    try:
        people = read_people(UPLOAD_CSV, logger=logger)
        if not people:
            raise RuntimeError(f"No people in {UPLOAD_CSV.name}")

        only_list = [s.strip() for s in opts["only"].split(",") if s.strip()]
        if only_list:
            people = [p for p in people if p.label() in only_list]
            if not people:
                raise RuntimeError("Nobody left after --only filter")

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
                cache, True, True, storage_path,
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
    return render_template_string(INDEX_HTML, run=RUN)


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

    RUNTIME_FLAGS["soundex"] = True
    RUNTIME_FLAGS["collapse_weak"] = True

    opts = {
        "headless": bool(request.form.get("headless")),
        "no_cache": bool(request.form.get("no_cache")),
        "only": request.form.get("only", ""),
        "skip": request.form.get("skip", ""),
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
        return Response(
            __import__("json").dumps(RUN, default=str),
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
