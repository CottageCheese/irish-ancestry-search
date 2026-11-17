"""'Our Family' grid, per-person page, delete + wipe routes."""
from __future__ import annotations

import json as _json
import logging
import re as _re

from flask import Blueprint, abort, redirect, render_template, url_for

from genealogy_core import load_confirmations, load_records_csv, record_key
from genealogy_core.render import slugify
from genealogy_search import (
    RESULTS_CSV,
    RESULTS_HTML,
    RESULTS_JSON,
    RESULTS_SUMMARY,
)

from ..helpers import (
    COUNTY_COORDS,
    ERA_RANK,
    GEN_LABELS,
    IRELAND_OUTLINE,
    TIER_BLURB,
    TIER_COLOR,
    TIER_ORDER,
    TIER_STARS,
    _avatar,
    _era_for_year,
    _generation,
    _narrate,
    _purge_person_trail,
    _read_rows,
    _silhouette,
    _write_rows,
)
from ..state import (
    CONFIRMS_FILE,
    CONFIRMS_LOCK,
    HERE,
    PEOPLE_LOCK,
)

bp = Blueprint("ancestors", __name__)


@bp.route("/ancestors")
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
        found = record_counts.get(label, 0)
        confirmed = confirmed_counts.get(label, 0)
        pct = round(100 * confirmed / found) if found else 0
        people_data.append({
            "label": label,
            "forename": forename,
            "surname": surname,
            "slug": slugify(label),
            "county": row.get("county", "").strip(),
            "year": year,
            "found": found,
            "confirmed": confirmed,
            "pct_confirmed": pct,
            "avatar_bg": bg,
            "avatar_fg": fg,
            "tint": tint,
            "era": era,
            "silhouette": _silhouette(row.get("sex", "")),
        })

    years = [int(p["year"][:4]) for p in people_data if p["year"][:4].isdigit()]
    youngest = max(years) if years else None
    for p in people_data:
        p["generation"] = _generation(p["year"], youngest)

    people_data.sort(key=lambda p: (
        ERA_RANK.get(p["era"], 99),
        -p["confirmed"], -p["found"], p["label"],
    ))

    groups: list[tuple[str, list[dict]]] = []
    for p in people_data:
        if not groups or groups[-1][0] != p["era"]:
            groups.append((p["era"], []))
        groups[-1][1].append(p)

    gens_present = sorted({p["generation"] for p in people_data if p["generation"] >= 0})
    gen_chips = [(n, lbl) for n, lbl in GEN_LABELS if n in gens_present]

    return render_template(
        "ancestors.html",
        people=people_data,
        groups=groups,
        gen_chips=gen_chips,
    )


@bp.route("/ancestors/<slug>/delete", methods=["POST"])
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
    return redirect(url_for("ancestors.ancestors_page"))


@bp.route("/ancestors/wipe", methods=["POST"])
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
    return redirect(url_for("ancestors.ancestors_page"))


@bp.route("/person/<slug>")
def person_page(slug: str):
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

    return render_template(
        "person.html",
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
