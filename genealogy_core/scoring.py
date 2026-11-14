"""Additive relevance scoring. Pure function of (Person, Record)."""
import re

from .models import Person, Record
from .soundex import soundex


def _extract_year(*fields: str) -> int | None:
    for f in fields:
        if not f:
            continue
        m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", f)
        if m:
            return int(m.group(0))
        if re.search(r"\d", f):
            return None
    return None


def _expected_event_window(record_type: str, birth_window: tuple[int, int] | None
                           ) -> tuple[int, int] | None:
    """Narrow expected year window per event type, given birth window."""
    if not birth_window:
        return None
    lo, hi = birth_window
    rt = (record_type or "").lower()
    if "marriage" in rt:
        return lo + 18, hi + 45
    if "death" in rt or "burial" in rt:
        return lo + 30, hi + 100
    if "birth" in rt or "baptism" in rt:
        return lo, hi
    if "census" in rt:
        return lo, hi + 100
    return lo - 5, hi + 100


def _year_match(year: int | None, window: tuple[int, int] | None) -> bool:
    if year is None or window is None:
        return False
    lo, hi = window
    return lo <= year <= hi


def score_record(person: Person, rec: Record) -> int:
    """Simple additive score. Manual-link rows score 0 so real hits float
    above. Points favour: exact forename, exact surname, county/townland
    match in place, year within person's window.
    """
    if "manual" in rec.record_type.lower():
        return 0
    pts = 1
    name_l = (rec.name_found or "").lower()
    place_l = (rec.place or "").lower()
    details_l = (rec.other_details or "").lower()
    haystack = name_l + " | " + place_l + " | " + details_l

    if person.forename and person.forename.lower() in name_l:
        pts += 3
    if person.surname and person.surname.lower() in name_l:
        pts += 3
    else:
        target_code = soundex(person.surname)
        if target_code:
            for tok in re.findall(r"[A-Za-z']{3,}", name_l):
                if soundex(tok) == target_code:
                    pts += 2
                    break
    for variant in person.surname_variants:
        if variant and variant.lower() in name_l:
            pts += 1
            break
    if person.county and person.county.lower() in haystack:
        pts += 2
    if person.townland and person.townland.lower() in haystack:
        pts += 4
    else:
        matched_variant = False
        for variant in getattr(person, "townland_variants", []) or []:
            if variant and variant.lower() in haystack:
                pts += 3
                matched_variant = True
                break
        if not matched_variant and person.townland:
            t_code = soundex(person.townland)
            if t_code:
                for tok in re.findall(r"[A-Za-z']{4,}", place_l):
                    if soundex(tok) == t_code:
                        pts += 2
                        break
    sparse = not rec.place and not rec.age_or_year
    if not sparse:
        year = _extract_year(rec.age_or_year, rec.name_found, rec.record_type)
        expected = _expected_event_window(rec.record_type, person.year_window())
        if _year_match(year, expected):
            narrow_lo, narrow_hi = expected  # type: ignore[misc]
            span = narrow_hi - narrow_lo
            pts += 3 if span <= 30 else 2
    if sparse:
        pts -= 1
    return pts
