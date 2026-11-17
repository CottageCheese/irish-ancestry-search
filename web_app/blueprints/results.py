"""Results download, confirmation, bookmarks, narrative + next-suggestions."""
from __future__ import annotations

import json
import json as _json
import logging

from flask import (
    Blueprint,
    Response,
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from genealogy_core import (
    VALID_STATUSES,
    load_confirmations,
    load_records_csv,
    record_key,
    save_confirmations,
    set_status,
)
from genealogy_search import (
    LOG_FILE,
    RESULTS_CSV,
    RESULTS_HTML,
    RESULTS_JSON,
    RESULTS_SUMMARY,
)

from ..helpers import (
    TIER_BLURB,
    TIER_COLOR,
    TIER_ORDER,
    TIER_STARS,
    _narrate,
    _next_suggestions,
    _read_rows,
    _rerender_results_html,
)
from ..state import (
    CONFIRMS_FILE,
    CONFIRMS_LOCK,
    HERE,
    PEOPLE_LOCK,
    RUN,
    RUN_LOCK,
)

bp = Blueprint("results", __name__)


@bp.route("/results.html")
def results_html():
    p = HERE / RESULTS_HTML
    if not p.exists():
        abort(404)
    return send_file(p)


@bp.route("/download/<name>")
def download(name: str):
    allowed = {RESULTS_CSV, RESULTS_JSON, RESULTS_HTML, LOG_FILE, RESULTS_SUMMARY,
               "confirmations.json"}
    if name not in allowed:
        abort(404)
    p = HERE / name
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True)


@bp.route("/confirm", methods=["POST"])
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


@bp.route("/next-suggestions.json")
def next_suggestions_json() -> Response:
    try:
        with PEOPLE_LOCK:
            rows = _read_rows()
    except Exception:
        rows = []
    with RUN_LOCK:
        label = RUN.get("label") or ""
        stopped = bool(RUN.get("stopped"))
        state = RUN.get("state")
    suggestions: list[dict] = []
    if state == "done" and not stopped:
        try:
            suggestions = _next_suggestions(label, rows)
        except Exception:
            suggestions = []
    return Response(json.dumps({"suggestions": suggestions}),
                    mimetype="application/json")


@bp.route("/narrative")
def narrative():
    return redirect(url_for("results.results_html"))


def _narrative_legacy():
    """Kid-friendly 'What we found' panel: cards grouped by confidence tier.
    Preserved for compatibility; no longer wired to a route."""
    p = HERE / RESULTS_JSON
    if not p.exists():
        return render_template(
            "narrative.html", generated="—", total=0, people=[],
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
    return render_template(
        "narrative.html",
        generated=payload.get("generated", "—"),
        total=payload.get("total", 0),
        people=people_out,
        tier_order=TIER_ORDER, tier_stars=TIER_STARS,
        tier_blurb=TIER_BLURB, tier_color=TIER_COLOR,
    )


@bp.route("/bookmarks")
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
    return render_template("bookmarks.html", groups=ordered)
