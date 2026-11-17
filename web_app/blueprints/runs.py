"""Run-control + quick-search + status/log routes."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from genealogy_core import RUNTIME_FLAGS
from genealogy_core.nicknames import NICKNAMES, expand_forename
from genealogy_core.soundex import soundex
from genealogy_scrape import settings as scrape_settings
from genealogy_scrape.sites import SITES
from genealogy_search import LOG_FILE

from ..helpers import (
    IRISH_COUNTIES,
    _next_suggestions,
    _portal_stats,
    _read_rows,
    _reset_run_state,
    _write_rows,
)
from ..state import (
    CSV_COLUMNS,
    HERE,
    PEOPLE_LOCK,
    RUN,
    RUN_LOCK,
)
from ..worker import _worker

bp = Blueprint("runs", __name__)


@bp.route("/")
def index():
    try:
        with PEOPLE_LOCK:
            people_rows = _read_rows()
    except Exception:
        people_rows = []
    sites = [{"key": k, "name": v.get("name", k)} for k, v in SITES.items()]
    next_suggestions: list[dict] = []
    if RUN.get("state") == "done" and not RUN.get("stopped"):
        try:
            next_suggestions = _next_suggestions(RUN.get("label") or "", people_rows)
        except Exception:
            next_suggestions = []

    show_portal = (
        RUN.get("state") in ("idle", "done")
        and not request.args.get("find")
        and any((r.get("forename") or "").strip() and (r.get("surname") or "").strip() for r in people_rows)
    )
    if show_portal:
        try:
            stats = _portal_stats(people_rows)
        except Exception:
            stats = None
        if stats:
            return render_template("portal.html", run=RUN, people=people_rows, stats=stats)

    return render_template(
        "index.html",
        run=RUN,
        people=people_rows,
        sites=sites,
        counties=IRISH_COUNTIES,
        nicknames_json=json.dumps(NICKNAMES),
        next_suggestions=next_suggestions,
    )


@bp.route("/quick-search", methods=["POST"])
def quick_search():
    with RUN_LOCK:
        if RUN["state"] == "running":
            return redirect(url_for("runs.index"))

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
        return redirect(url_for("runs.index"))

    with PEOPLE_LOCK:
        rows = _read_rows()
        key = (forename.lower(), surname.lower())
        existing = next((r for r in rows if (r.get("forename", "").lower(), r.get("surname", "").lower()) == key), None)
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
            if townland and existing.get("townland", "").strip() != townland:
                existing["townland"] = townland; changed = True
            if county and existing.get("county", "").strip() != county:
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
    return redirect(url_for("runs.index"))


@bp.route("/start", methods=["POST"])
def start():
    with RUN_LOCK:
        if RUN["state"] == "running":
            return redirect(url_for("runs.index"))
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

    from ..state import UPLOAD_CSV
    upload = request.files.get("csv")
    if upload and upload.filename:
        upload.save(UPLOAD_CSV)

    if not UPLOAD_CSV.exists():
        with RUN_LOCK:
            RUN["state"] = "error"
            RUN["error"] = "No ancestors.csv found and none uploaded."
        return redirect(url_for("runs.index"))

    want_surnames = bool(request.form.get("surnames"))
    want_nicks = bool(request.form.get("nicks"))
    surname_picks = [s.strip() for s in request.form.getlist("surname_pick") if s.strip()]
    forename_picks = [s.strip() for s in request.form.getlist("forename_pick") if s.strip()]
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
    current_app.logger.warning("START opts=%r form_keys=%r", opts, list(request.form.keys()))

    threading.Thread(target=_worker, args=(opts,), daemon=True).start()
    return redirect(url_for("runs.index"))


@bp.route("/cancel", methods=["POST"])
def cancel():
    with RUN_LOCK:
        if RUN["state"] == "running":
            RUN["cancel_requested"] = True
    return Response(status=204)


@bp.route("/reset", methods=["POST"])
def reset():
    _reset_run_state()
    return redirect(url_for("runs.index"))


@bp.route("/prefill")
def prefill():
    _reset_run_state()
    qs = request.query_string.decode("ascii", "ignore")
    target = url_for("runs.index") + (("?" + qs) if qs else "") + "#forename"
    return redirect(target)


@bp.route("/status.json")
def status_json() -> Response:
    with RUN_LOCK:
        snap = dict(RUN)
    snap["server_now"] = time.time()
    return Response(json.dumps(snap, default=str), mimetype="application/json")


@bp.route("/log.json")
def log_json() -> Response:
    p = HERE / LOG_FILE
    lines: list[str] = []
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = raw[-15:]
        except Exception:
            lines = []
    return Response(json.dumps({"lines": lines}), mimetype="application/json")


@bp.route("/api/preview")
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
