"""Feedback loop: confirmed records teach the scorer.

When the user marks a record "confirmed" in the web UI, its place/year/kin
tokens become hints for *future* runs of the same person. The next run
rewards records that share those signals.
"""
import csv
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
                out.append(Record(**kw))
    except Exception:
        return []
    return out


def extract_hints(confirmed: list[Record]) -> dict[str, dict]:
    """Group confirmed records by person_searched and distil into hints.

    Returns {person_label: {"places": set[str], "kin": set[str], "years": set[int]}}.
    """
    out: dict[str, dict] = defaultdict(
        lambda: {"places": set(), "kin": set(), "years": set()}
    )
    for r in confirmed:
        bucket = out[r.person_searched]
        bucket["places"] |= _tokens(r.place)
        bucket["kin"] |= _tokens(r.other_details) | _tokens(r.name_found)
        for m in _YEAR_RE.finditer(r.age_or_year or ""):
            bucket["years"].add(int(m.group(0)))
    return dict(out)


def score_hint_bonus(rec: Record, hints: dict | None) -> tuple[int, str]:
    """Extra points for sharing signals with a confirmed record.

    +2 per shared place token, +1 per shared kin token (capped), +2 if
    year within ±2 of a confirmed year. Returns (points, reason_string).
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

    kin_toks = _tokens(rec.other_details) | _tokens(rec.name_found)
    shared_kin = kin_toks & hints.get("kin", set())
    if shared_kin:
        pts += min(len(shared_kin), 3)
        reasons.append(f"kin:{','.join(sorted(list(shared_kin))[:3])}")

    for m in _YEAR_RE.finditer(rec.age_or_year or ""):
        y = int(m.group(0))
        if any(abs(y - cy) <= 2 for cy in hints.get("years", set())):
            pts += 2
            reasons.append(f"year:{y}")
            break

    return pts, "; ".join(reasons)
