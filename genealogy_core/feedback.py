"""Feedback loop: confirmed records teach the scorer.

When the user marks a record "confirmed" in the web UI, its place/year/kin
tokens become hints for *future* runs of the same person. The next run
rewards records that share those signals.
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from .models import Record

_WORD_RE = re.compile(r"[A-Za-z']{3,}")
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")

PERSON_HINTS = dict[str, set[str] | set[int]]


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


def load_records_csv(path: Path) -> list[Record]:
    """Read a results.csv back into Record objects. Returns [] if missing/broken."""
    if not path.exists():
        return []
    out: list[Record] = []
    fields = set(Record.__dataclass_fields__.keys())
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                kw = {k: v for k, v in row.items() if k in fields}
                if "score" in kw:
                    try:
                        kw["score"] = int(kw["score"] or 0)
                    except ValueError:
                        kw["score"] = 0
                if "is_new" in kw:
                    kw["is_new"] = str(kw["is_new"]).lower() in ("1", "true", "yes")
                if "score_breakdown" in kw:
                    raw = kw["score_breakdown"]
                    if isinstance(raw, str) and raw.strip():
                        try:
                            kw["score_breakdown"] = json.loads(raw)
                        except Exception:
                            kw["score_breakdown"] = {}
                    elif not isinstance(raw, dict):
                        kw["score_breakdown"] = {}
                out.append(Record(**kw))
    except Exception:
        return []
    return out


def _empty_bucket() -> dict:
    return {
        "places": set(), "kin": set(), "years": set(),
        "anti_places": set(), "anti_kin": set(), "anti_years": set(),
    }


def extract_hints(confirmed: list[Record],
                  rejected: list[Record] | None = None) -> dict[str, dict]:
    """Group confirmed + rejected records by person_searched into hints.

    Confirmed tokens go to `places/kin/years` (positive signal). Rejected
    tokens go to `anti_places/anti_kin/anti_years` (negative signal), but
    only when they do NOT also appear in the confirmed set — confirmed
    always wins so a rejected sibling record doesn't punish its confirmed
    counterpart's shared place.
    """
    out: dict[str, dict] = defaultdict(_empty_bucket)
    for r in confirmed:
        bucket = out[r.person_searched]
        bucket["places"] |= _tokens(r.place)
        bucket["kin"] |= _tokens(r.other_details) | _tokens(r.name_found)
        for m in _YEAR_RE.finditer(r.age_or_year or ""):
            bucket["years"].add(int(m.group(0)))
    for r in rejected or []:
        bucket = out[r.person_searched]
        bucket["anti_places"] |= _tokens(r.place)
        bucket["anti_kin"] |= _tokens(r.other_details) | _tokens(r.name_found)
        for m in _YEAR_RE.finditer(r.age_or_year or ""):
            bucket["anti_years"].add(int(m.group(0)))
    for bucket in out.values():
        bucket["anti_places"] -= bucket["places"]
        bucket["anti_kin"] -= bucket["kin"]
        bucket["anti_years"] -= bucket["years"]
    return dict(out)


def score_hint_bonus(rec: Record, hints: dict | None) -> tuple[int, str]:
    """Score adjustment from confirmed + rejected feedback.

    Positive: +2 per shared place token (cap 3), +1 per shared kin token
    (cap 3), +2 if year within ±2 of a confirmed year.
    Negative: mirror weights against anti_* sets from rejected records.
    Returns (points, reason_string); points may be negative.
    """
    if not hints:
        return 0, ""
    pts = 0
    reasons: list[str] = []

    place_toks = _tokens(rec.place)
    shared_place = place_toks & hints.get("places", set())
    if shared_place:
        pts += 2 * min(len(shared_place), 3)
        reasons.append(f"place:{','.join(sorted(shared_place))}")
    anti_place = place_toks & hints.get("anti_places", set())
    if anti_place:
        pts -= 2 * min(len(anti_place), 3)
        reasons.append(f"anti_place:{','.join(sorted(anti_place))}")

    kin_toks = _tokens(rec.other_details) | _tokens(rec.name_found)
    shared_kin = kin_toks & hints.get("kin", set())
    if shared_kin:
        pts += min(len(shared_kin), 3)
        reasons.append(f"kin:{','.join(sorted(list(shared_kin))[:3])}")
    anti_kin = kin_toks & hints.get("anti_kin", set())
    if anti_kin:
        pts -= min(len(anti_kin), 3)
        reasons.append(f"anti_kin:{','.join(sorted(list(anti_kin))[:3])}")

    rec_years = [int(m.group(0)) for m in _YEAR_RE.finditer(rec.age_or_year or "")]
    for y in rec_years:
        if any(abs(y - cy) <= 2 for cy in hints.get("years", set())):
            pts += 2
            reasons.append(f"year:{y}")
            break
    for y in rec_years:
        if any(abs(y - cy) <= 2 for cy in hints.get("anti_years", set())):
            pts -= 2
            reasons.append(f"anti_year:{y}")
            break

    return pts, "; ".join(reasons)
