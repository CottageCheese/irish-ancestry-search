"""Father/mother/spouse name cross-check against a record's text."""
import re

from .feedback import score_hint_bonus
from .models import Person, Record
from .scoring import score_record

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
    haystack = ((rec.name_found or "") + " " + (rec.other_details or "")).lower()
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
        r.score = score_record(person, r)
        kin = kin_cross_check(person, r)
        if kin:
            r.kin_hits = kin
            r.score += 5
        bonus, reason = score_hint_bonus(r, hints)
        if bonus:
            r.score += bonus
            r.kin_hits = f"{r.kin_hits}; feedback:{reason}".lstrip("; ") if r.kin_hits else f"feedback:{reason}"
