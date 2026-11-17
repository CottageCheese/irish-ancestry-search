"""Shared helpers and data constants for the web app."""
from __future__ import annotations

import csv
import logging

from genealogy_core import (
    apply_confirmations,
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
    RUN_LOCK,
    UPLOAD_CSV,
    RUN,
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
    "Likely": "#0a7d2b", "Possible": "#b57b00",
    "Weak": "#9a6f6f", "Manual": "#555",
}

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

IRISH_COUNTIES = [
    "Antrim", "Armagh", "Carlow", "Cavan", "Clare", "Cork", "Derry", "Donegal",
    "Down", "Dublin", "Fermanagh", "Galway", "Kerry", "Kildare", "Kilkenny",
    "Laois", "Leitrim", "Limerick", "Longford", "Louth", "Mayo", "Meath",
    "Monaghan", "Offaly", "Roscommon", "Sligo", "Tipperary", "Tyrone",
    "Waterford", "Westmeath", "Wexford", "Wicklow",
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
        return "\U0001F468"
    if s == "F":
        return "\U0001F469"
    return "\U0001F464"


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
        (r for r in rows
         if (r.get("forename", "").strip().lower(),
             r.get("surname", "").strip().lower()) == (forename.lower(), surname.lower())),
        None,
    )
    if row is None:
        return []

    existing = {
        (r.get("forename", "").strip().lower(), r.get("surname", "").strip().lower())
        for r in rows
    }
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
        out.append({
            "label": f"{forename}'s dad",
            "emoji": "\U0001F468",
            "forename": f_fore, "surname": f_surn or surname,
            "birth_year": parent_year, "sex": "M", "county": county,
            "prefill_only": not f_have,
        })
    m_fore, m_surn = _parse_relative("mother", "")
    m_have = bool(m_fore) and (m_fore.lower(), m_surn.lower()) not in existing
    m_missing = not m_fore
    if m_have or m_missing:
        out.append({
            "label": f"{forename}'s mum",
            "emoji": "\U0001F469",
            "forename": m_fore, "surname": m_surn,
            "birth_year": parent_year, "sex": "F", "county": county,
            "prefill_only": not m_have,
        })
    s_fore, s_surn = _parse_relative("spouse", "")
    if s_fore and (s_fore.lower(), s_surn.lower()) not in existing:
        out.append({
            "label": f"{forename}'s partner",
            "emoji": "\U0001F49E",
            "forename": s_fore, "surname": s_surn,
            "birth_year": str(birth) if birth else "", "sex": "", "county": county,
            "prefill_only": False,
        })
    return out


def _reset_run_state() -> bool:
    """Flip RUN back to idle so the quick-search form renders. Returns False
    if a run is currently in progress (nothing is reset)."""
    with RUN_LOCK:
        if RUN["state"] == "running":
            return False
        RUN.update({
            "state": "idle",
            "current": 0, "total": 0, "label": "",
            "started": None, "finished": None, "error": "",
            "new_count": 0, "record_count": 0,
            "person_name": "",
        })
    return True
