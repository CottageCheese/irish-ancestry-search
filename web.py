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

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for
from playwright.sync_api import sync_playwright

from genealogy_core import (
    Record,
    RUNTIME_FLAGS,
    annotate_records,
    load_previous_keys,
    mark_new_records,
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

app = Flask(__name__)

RUN: dict = {
    "state": "idle",      # idle | running | done | error
    "current": 0,
    "total": 0,
    "label": "",
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
    <label><input type="checkbox" name="deep"> Deep (click into each hit)</label>
    <label><input type="checkbox" name="no_cache"> Ignore cache</label>
  </div>

  <br>
  <button type="submit">Start search</button>
</form>
{% else %}
<div class="state">
  <strong>State:</strong> {{ run.state }}<br>
  {% if run.total %}
    <strong>Progress:</strong> {{ run.current }} / {{ run.total }} &mdash; {{ run.label }}
    <div class="bar"><div style="width: {{ (100 * run.current / run.total) if run.total else 0 }}%"></div></div>
  {% endif %}
  {% if run.started %}<div><small>Started {{ run.started }}</small></div>{% endif %}
  {% if run.finished %}<div><small>Finished {{ run.finished }}</small></div>{% endif %}
  {% if run.error %}<div style="color:#b00"><strong>Error:</strong> {{ run.error }}</div>{% endif %}
  {% if run.state == 'done' %}
    <div>{{ run.record_count }} unique record(s). {{ run.new_count }} new since previous run.</div>
    <a class="btn" href="{{ url_for('results_html') }}" target="_blank">Open results.html</a>
    <a class="btn" href="{{ url_for('download', name='results.csv') }}">Download CSV</a>
    <a class="btn" href="{{ url_for('download', name='results.json') }}">Download JSON</a>
    <a class="btn" href="{{ url_for('download', name='run.log') }}">run.log</a>
  {% endif %}
  {% if run.state in ('done', 'error') %}
    <form method="post" action="{{ url_for('reset') }}" style="margin-top:1rem">
      <button type="submit">New run</button>
    </form>
  {% endif %}
</div>
{% if run.state == 'running' %}<script>setTimeout(() => location.reload(), 2000);</script>{% endif %}
{% endif %}
"""


def _progress(current: int, total: int, label: str) -> None:
    with RUN_LOCK:
        RUN["current"] = current
        RUN["total"] = total
        RUN["label"] = label


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

        with sync_playwright() as pw:
            records = _run_search(
                pw, people, opts["headless"], logger, skip_sites,
                cache, True, opts["deep"], True, storage_path,
                incremental_save=_save,
                progress=_progress,
            )

        new_count = mark_new_records(records, prev_keys)
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
        "deep": bool(request.form.get("deep")),
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
    allowed = {RESULTS_CSV, RESULTS_JSON, RESULTS_HTML, LOG_FILE, RESULTS_SUMMARY}
    if name not in allowed:
        abort(404)
    p = HERE / name
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True)


@app.route("/status.json")
def status_json() -> Response:
    with RUN_LOCK:
        return Response(
            __import__("json").dumps(RUN, default=str),
            mimetype="application/json",
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
