"""Shared helpers and data constants for the web app."""

from __future__ import annotations

import csv
import logging

from genealogy_core import (
    apply_confirmations,
    confidence_tier,
    load_confirmations,
    load_records_csv,
    record_key,
    save_confirmations,
)
from genealogy_search import (
    RESULTS_CSV,
    RESULTS_HTML,
    write_csv,
    write_html,
)

from .state import (
    CONFIRMS_FILE,
    CONFIRMS_LOCK,
    CSV_COLUMNS,
    HERE,
    RUN,
    RUN_LOCK,
    UPLOAD_CSV,
)

TIER_ORDER = ["Likely", "Possible", "Weak", "Manual"]
TIER_STARS = {"Likely": "★★★", "Possible": "★★", "Weak": "★", "Manual": "🔍"}
TIER_BLURB = {
    "Likely": "Looks like a strong match.",
    "Possible": "Could be them — worth a look.",
    "Weak": "Shaky match. Check carefully.",
    "Manual": "Needs a human eye.",
}
TIER_COLOR = {
    "Likely": "#0a7d2b",
    "Possible": "#b57b00",
    "Weak": "#9a6f6f",
    "Manual": "#555",
}

COUNTY_COORDS = {
    "antrim": (54.90, -6.30),
    "armagh": (54.35, -6.45),
    "carlow": (52.70, -6.80),
    "cavan": (54.00, -7.30),
    "clare": (52.90, -9.00),
    "cork": (51.95, -8.50),
    "derry": (54.90, -7.00),
    "londonderry": (54.90, -7.00),
    "donegal": (54.90, -8.00),
    "down": (54.33, -5.90),
    "dublin": (53.35, -6.25),
    "fermanagh": (54.35, -7.60),
    "galway": (53.30, -9.00),
    "kerry": (52.10, -9.60),
    "kildare": (53.20, -6.80),
    "kilkenny": (52.60, -7.25),
    "laois": (52.90, -7.35),
    "leitrim": (54.10, -8.00),
    "limerick": (52.50, -8.60),
    "longford": (53.70, -7.80),
    "louth": (53.95, -6.50),
    "mayo": (54.00, -9.40),
    "meath": (53.60, -6.65),
    "monaghan": (54.25, -6.95),
    "offaly": (53.20, -7.50),
    "roscommon": (53.70, -8.30),
    "sligo": (54.15, -8.60),
    "tipperary": (52.60, -8.00),
    "tyrone": (54.60, -7.30),
    "waterford": (52.20, -7.60),
    "westmeath": (53.50, -7.50),
    "wexford": (52.50, -6.55),
    "wicklow": (52.95, -6.30),
}

IRELAND_OUTLINE = [
    (-7.40, 55.38),
    (-6.95, 55.18),
    (-6.03, 55.21),
    (-5.65, 55.00),
    (-5.85, 54.65),
    (-5.49, 54.43),
    (-5.86, 54.21),
    (-6.00, 54.05),
    (-6.25, 54.08),
    (-6.07, 53.75),
    (-6.03, 53.33),
    (-6.03, 52.99),
    (-6.11, 52.72),
    (-6.38, 52.18),
    (-6.95, 52.14),
    (-7.56, 52.11),
    (-8.14, 51.84),
    (-8.56, 51.60),
    (-9.47, 51.44),
    (-9.95, 51.47),
    (-10.37, 51.99),
    (-10.16, 52.26),
    (-9.73, 52.62),
    (-9.93, 52.95),
    (-9.47, 53.15),
    (-9.90, 53.42),
    (-10.18, 53.62),
    (-9.83, 53.92),
    (-10.08, 54.28),
    (-9.06, 54.28),
    (-8.70, 54.49),
    (-8.70, 54.71),
    (-8.37, 55.17),
    (-7.95, 55.21),
    (-7.40, 55.38),
]

IRISH_COUNTIES = [
    "Antrim",
    "Armagh",
    "Carlow",
    "Cavan",
    "Clare",
    "Cork",
    "Derry",
    "Donegal",
    "Down",
    "Dublin",
    "Fermanagh",
    "Galway",
    "Kerry",
    "Kildare",
    "Kilkenny",
    "Laois",
    "Leitrim",
    "Limerick",
    "Longford",
    "Louth",
    "Mayo",
    "Meath",
    "Monaghan",
    "Offaly",
    "Roscommon",
    "Sligo",
    "Tipperary",
    "Tyrone",
    "Waterford",
    "Westmeath",
    "Wexford",
    "Wicklow",
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


def _silhouette(sex: str) -> str:
    s = (sex or "").strip().upper()
    if s == "M":
        return "\U0001f468"
    if s == "F":
        return "\U0001f469"
    return "\U0001f464"


ERA_ORDER = [
    "Before the Famine",
    "The Great Hunger",
    "After the Famine",
    "Land War Ireland",
    "Home Rule era",
    "Rising & Revolution",
    "Irish Free State",
    "Republic of Ireland",
    "Long ago",
]
ERA_RANK = {e: i for i, e in enumerate(ERA_ORDER)}

GEN_LABELS = [
    (0, "\U0001f476 Youngest"),
    (1, "\U0001f46a Parents"),
    (2, "\U0001f474 Grandparents"),
    (3, "\U0001f570\ufe0f Great-grans"),
    (4, "\U0001f4dc Further back"),
]


def _generation(year_str: str, youngest: int | None) -> int:
    """Generation steps from youngest known ancestor. -1 if unknown."""
    if youngest is None:
        return -1
    try:
        y = int(str(year_str).strip()[:4])
    except (ValueError, TypeError):
        return -1
    return min(4, max(0, round((youngest - y) / 25)))


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


def _next_suggestions(person_label: str, rows: list[dict]) -> list[dict]:
    """Build 'Who next?' ancestor suggestions for the finished-run panel."""
    name = (person_label or "").split(" (")[0].strip()
    if not name:
        return []
    parts = name.split(None, 1)
    forename = parts[0] if parts else ""
    surname = parts[1] if len(parts) > 1 else ""
    row = next(
        (
            r
            for r in rows
            if (r.get("forename", "").strip().lower(), r.get("surname", "").strip().lower())
            == (forename.lower(), surname.lower())
        ),
        None,
    )
    if row is None:
        return []

    existing = {(r.get("forename", "").strip().lower(), r.get("surname", "").strip().lower()) for r in rows}
    try:
        birth = int(row.get("birth_year_approx") or 0) or None
    except ValueError:
        birth = None
    parent_year = str(birth - 30) if birth else ""
    county = row.get("county", "")

    def _parse_relative(field: str, fallback_surname: str) -> tuple[str, str]:
        raw = (row.get(field) or "").strip()
        if not raw:
            return "", ""
        bits = raw.split(None, 1)
        return bits[0], (bits[1] if len(bits) > 1 else fallback_surname)

    out: list[dict] = []
    f_fore, f_surn = _parse_relative("father", surname)
    f_have = bool(f_fore) and (f_fore.lower(), f_surn.lower()) not in existing
    f_missing = not f_fore
    if f_have or f_missing:
        out.append(
            {
                "label": f"{forename}'s dad",
                "emoji": "\U0001f468",
                "forename": f_fore,
                "surname": f_surn or surname,
                "birth_year": parent_year,
                "sex": "M",
                "county": county,
                "prefill_only": not f_have,
            }
        )
    m_fore, m_surn = _parse_relative("mother", "")
    m_have = bool(m_fore) and (m_fore.lower(), m_surn.lower()) not in existing
    m_missing = not m_fore
    if m_have or m_missing:
        out.append(
            {
                "label": f"{forename}'s mum",
                "emoji": "\U0001f469",
                "forename": m_fore,
                "surname": m_surn,
                "birth_year": parent_year,
                "sex": "F",
                "county": county,
                "prefill_only": not m_have,
            }
        )
    s_fore, s_surn = _parse_relative("spouse", "")
    if s_fore and (s_fore.lower(), s_surn.lower()) not in existing:
        out.append(
            {
                "label": f"{forename}'s partner",
                "emoji": "\U0001f49e",
                "forename": s_fore,
                "surname": s_surn,
                "birth_year": str(birth) if birth else "",
                "sex": "",
                "county": county,
                "prefill_only": False,
            }
        )
    return out


def _portal_stats(people_rows: list[dict]) -> dict:
    """Aggregate counts for the portal landing dashboard.

    Returns ancestor count, confirmed/saved record counts, pending review count
    (confirm-worthy records with no decision), progress percent, latest finds,
    and a smart-CTA recommendation."""
    ancestors = sum(1 for r in people_rows if (r.get("forename") or "").strip() and (r.get("surname") or "").strip())

    with CONFIRMS_LOCK:
        confs = load_confirmations(CONFIRMS_FILE)
    confirmed = sum(1 for v in confs.values() if v.get("status") == "confirmed")
    saved = sum(1 for v in confs.values() if v.get("status") == "saved")

    records_total = 0
    pending = 0
    latest: list[dict] = []
    era_record_counts: dict[str, int] = {e: 0 for e in ERA_ORDER}
    results_csv_path = HERE / RESULTS_CSV
    if results_csv_path.exists():
        try:
            recs = load_records_csv(results_csv_path)
            records_total = len(recs)
            for r in recs:
                status = confs.get(record_key(r), {}).get("status") or ""
                if status == "confirmed":
                    _, era = _era_for_year(r.age_or_year)
                    era_record_counts[era] = era_record_counts.get(era, 0) + 1
                if status:
                    continue
                tier = confidence_tier(r.score)
                if tier in ("Likely", "Possible"):
                    pending += 1
            new_recs = [r for r in recs if getattr(r, "is_new", False)]
            pool = new_recs or recs
            pool_sorted = sorted(pool, key=lambda r: r.score, reverse=True)
            for r in pool_sorted[:4]:
                latest.append(
                    {
                        "person": r.person_searched,
                        "name": r.name_found,
                        "site": r.site,
                        "type": r.record_type,
                        "year": r.age_or_year,
                        "place": r.place,
                        "tier": confidence_tier(r.score),
                    }
                )
        except Exception:
            pass

    era_ancestor_counts: dict[str, int] = {e: 0 for e in ERA_ORDER}
    county_ancestors: dict[str, list[str]] = {}
    for r in people_rows:
        fname = (r.get("forename") or "").strip()
        sname = (r.get("surname") or "").strip()
        if not (fname and sname):
            continue
        _, era = _era_for_year(r.get("birth_year_approx", ""))
        era_ancestor_counts[era] = era_ancestor_counts.get(era, 0) + 1
        county = (r.get("county") or "").strip().lower()
        if county and county in COUNTY_COORDS:
            county_ancestors.setdefault(county, []).append(f"{fname} {sname}")

    era_sample = {
        "Before the Famine": "1830",
        "The Great Hunger": "1848",
        "After the Famine": "1870",
        "Land War Ireland": "1890",
        "Home Rule era": "1910",
        "Rising & Revolution": "1920",
        "Irish Free State": "1935",
        "Republic of Ireland": "1960",
    }
    era_rc_max = max(era_record_counts.values(), default=0)
    era_ac_max = max(era_ancestor_counts.values(), default=0)
    era_ribbon = []
    for e in ERA_ORDER:
        if e == "Long ago":
            continue
        tint, _ = _era_for_year(era_sample.get(e, ""))
        rc = era_record_counts.get(e, 0)
        ac = era_ancestor_counts.get(e, 0)
        if era_rc_max:
            density = round(100 * rc / era_rc_max)
        elif era_ac_max:
            density = round(100 * ac / era_ac_max)
        else:
            density = 0
        era_ribbon.append(
            {
                "era": e,
                "tint": tint,
                "records": rc,
                "ancestors": ac,
                "density": density,
                "has_data": rc > 0 or ac > 0,
            }
        )

    reviewed = confirmed + saved + sum(1 for v in confs.values() if v.get("status") == "rejected")
    denom = reviewed + pending
    progress_pct = round(100 * reviewed / denom) if denom else 0

    if ancestors == 0:
        cta = {"kind": "add", "label": "Add first ancestor", "href": "/?find=1#quick-search"}
    elif pending > 0:
        cta = {"kind": "review", "label": f"Review {pending} clue{'s' if pending != 1 else ''}", "href": "/ancestors"}
    elif records_total == 0:
        cta = {"kind": "run", "label": "Run a search", "href": "/start", "method": "post"}
    else:
        cta = {"kind": "rerun", "label": "Re-run search for new records", "href": "/start", "method": "post"}

    return {
        "ancestors": ancestors,
        "confirmed": confirmed,
        "saved": saved,
        "pending": pending,
        "records_total": records_total,
        "progress_pct": progress_pct,
        "latest": latest,
        "cta": cta,
        "era_ribbon": era_ribbon,
        "county_pins": _county_pins(county_ancestors),
        "ireland_path": _ireland_svg_path(),
    }


def _county_pins(county_ancestors: dict[str, list[str]]) -> list[dict]:
    """Project COUNTY_COORDS into SVG-space pins sized by ancestor count."""
    if not county_ancestors:
        return []
    lats = [lat for lat, _ in COUNTY_COORDS.values()]
    lons = [lon for _, lon in COUNTY_COORDS.values()]
    lat_min, lat_max = min(lats) - 0.2, max(lats) + 0.2
    lon_min, lon_max = min(lons) - 0.2, max(lons) + 0.2
    w, h = 300, 420
    max_count = max(len(v) for v in county_ancestors.values())
    pins = []
    for county, names in county_ancestors.items():
        lat, lon = COUNTY_COORDS[county]
        x = (lon - lon_min) / (lon_max - lon_min) * w
        y = (lat_max - lat) / (lat_max - lat_min) * h
        n = len(names)
        r = 5 + 10 * (n / max_count) if max_count else 5
        pins.append(
            {
                "county": county.title(),
                "x": round(x, 1),
                "y": round(y, 1),
                "r": round(r, 1),
                "count": n,
                "names": names,
            }
        )
    return pins


def _ireland_svg_path() -> str:
    """Convert IRELAND_OUTLINE coords to SVG-space path matching _county_pins()."""
    lats = [lat for lat, _ in COUNTY_COORDS.values()]
    lons = [lon for _, lon in COUNTY_COORDS.values()]
    lat_min, lat_max = min(lats) - 0.2, max(lats) + 0.2
    lon_min, lon_max = min(lons) - 0.2, max(lons) + 0.2
    w, h = 300, 420
    parts = []
    for i, (lon, lat) in enumerate(IRELAND_OUTLINE):
        x = (lon - lon_min) / (lon_max - lon_min) * w
        y = (lat_max - lat) / (lat_max - lat_min) * h
        parts.append(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}")
    return " ".join(parts) + " Z"


def _reset_run_state() -> bool:
    """Flip RUN back to idle so the quick-search form renders. Returns False
    if a run is currently in progress (nothing is reset)."""
    with RUN_LOCK:
        if RUN["state"] == "running":
            return False
        RUN.update(
            {
                "state": "idle",
                "current": 0,
                "total": 0,
                "label": "",
                "started": None,
                "finished": None,
                "error": "",
                "new_count": 0,
                "record_count": 0,
                "person_name": "",
            }
        )
    return True
