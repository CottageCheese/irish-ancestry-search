"""Additive relevance scoring. Pure function of (Person, Record)."""
import re

from .models import Person, Record
from .soundex import soundex


def _year_in_window(year_str: str, window: tuple[int, int] | None) -> bool:
    if not window:
        return False
    m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", year_str or "")
    if not m:
        return False
    y = int(m.group(0))
    lo, hi = window
    return (lo - 10) <= y <= (hi + 40)


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
    if _year_in_window(rec.age_or_year, person.year_window()):
        pts += 2
    if not rec.place and not rec.age_or_year:
        pts -= 1
    return pts
