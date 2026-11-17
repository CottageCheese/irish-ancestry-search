"""CRUD routes for ancestors.csv rows."""
from __future__ import annotations

from flask import Blueprint, Response, render_template, request

from ..helpers import _purge_person_trail, _read_rows, _write_rows
from ..state import CSV_COLUMNS, PEOPLE_LOCK

bp = Blueprint("people", __name__)


@bp.route("/people")
def people_page():
    with PEOPLE_LOCK:
        rows = _read_rows()
    return render_template("people.html", rows=rows, cols=CSV_COLUMNS)


@bp.route("/people/add", methods=["POST"])
def people_add() -> Response:
    with PEOPLE_LOCK:
        rows = _read_rows()
        rows.append({c: "" for c in CSV_COLUMNS})
        _write_rows(rows)
    return Response(status=204)


@bp.route("/people/<int:idx>/save", methods=["POST"])
def people_save(idx: int) -> Response:
    data = request.get_json(silent=True) or {}
    with PEOPLE_LOCK:
        rows = _read_rows()
        if idx < 0 or idx >= len(rows):
            return Response("bad index", status=400)
        rows[idx] = {c: str(data.get(c, "")).strip() for c in CSV_COLUMNS}
        _write_rows(rows)
    return Response(status=204)


@bp.route("/people/<int:idx>/delete", methods=["POST"])
def people_delete(idx: int) -> Response:
    with PEOPLE_LOCK:
        rows = _read_rows()
        if idx < 0 or idx >= len(rows):
            return Response("bad index", status=400)
        gone = rows.pop(idx)
        _write_rows(rows)
    label = f"{gone.get('forename', '').strip()} {gone.get('surname', '').strip()}".strip()
    if label:
        _purge_person_trail(label)
    return Response(status=204)
