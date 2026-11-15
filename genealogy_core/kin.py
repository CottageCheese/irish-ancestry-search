"""Father/mother/spouse name cross-check against a record's text."""
import re

from .feedback import score_hint_bonus
from .models import Person, Record
from .nicknames import MALE_NAMES, FEMALE_NAMES
from .scoring import score_record, _extract_year, _expected_event_window

KIN_SPLIT = re.compile(r"[,\s;]+")


def _kin_tokens(name: str) -> set[str]:
    if not name:
        return set()
    return {t for t in KIN_SPLIT.split(name.lower().strip()) if len(t) >= 3}


def kin_cross_check(person: Person, rec: Record) -> str:
    """Return 'father:John; spouse:Mary' when kin tokens appear in the
    record text. Tokens equal to the subject's own surname / forename are
    excluded to avoid trivial self-matches.
    """
    household_text = " ".join(rec.household) if rec.household else ""
    haystack = ((rec.name_found or "") + " " + (rec.other_details or "") + " " + household_text).lower()
    if not haystack.strip():
        return ""
    own = {(person.surname or "").lower(), (person.forename or "").lower()}
    for v in person.surname_variants:
        if v:
            own.add(v.lower())
    hits: list[str] = []
    for label, value in (("father", person.father),
                         ("mother", person.mother),
                         ("spouse", person.spouse)):
        toks = _kin_tokens(value) - own
        matched = [t for t in toks if t in haystack]
        if matched:
            hits.append(f"{label}:{value}")
    return "; ".join(hits)


def annotate_records(person: Person, records: list[Record],
                     hints: dict | None = None) -> None:
    """Attach score + kin_hits in-place. Kin match adds +5. Feedback hints
    (from previously-confirmed records for this person) add a variable
    bonus and their reason is appended to kin_hits.
    """
    for r in records:
        total, breakdown = score_record(person, r)
        r.score = total
        r.score_breakdown = dict(breakdown)
        kin = kin_cross_check(person, r)
        if kin:
            r.kin_hits = kin
            r.score += 5
            r.score_breakdown["kin"] = 5
        bonus, reason = score_hint_bonus(r, hints)
        if bonus:
            r.score += bonus
            r.score_breakdown["feedback"] = bonus
            r.kin_hits = f"{r.kin_hits}; feedback:{reason}".lstrip("; ") if r.kin_hits else f"feedback:{reason}"

    sex = person.sex
    if sex not in ("M", "F"):
        pf = (person.forename or "").split()[0].lower()
        if pf in MALE_NAMES:
            sex = "M"
        elif pf in FEMALE_NAMES:
            sex = "F"
    if sex in ("M", "F"):
        opp = FEMALE_NAMES if sex == "M" else MALE_NAMES
        for r in records:
            if r.status:
                continue
            first = (r.name_found or "").split()[0].lower() if r.name_found else ""
            if first and first in opp:
                r.status = "rejected"

    bw = person.year_window()
    for r in records:
        if r.status:
            continue
        if "manual" in (r.record_type or "").lower():
            continue
        bd = r.score_breakdown or {}
        if bd.get("forename_mismatch", 0) < 0:
            rescued = bool(r.kin_hits) or "townland" in bd or "townland_variant" in bd \
                      or "townland_soundex" in bd or "census_age" in bd
            if not rescued:
                r.status = "rejected"
                continue
        if bw and "census_age" not in bd and "year_window" not in bd:
            yr = _extract_year(r.age_or_year, r.name_found, r.record_type)
            if yr is not None:
                expected = _expected_event_window(r.record_type, bw)
                if expected:
                    lo, hi = expected
                    if yr < lo - 5 or yr > hi + 5:
                        r.status = "rejected"
