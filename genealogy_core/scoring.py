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


_AGE_RE = re.compile(r"\s*(\d{1,3})\b")
_CENSUS_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")


def _parse_age(s: str) -> int | None:
    """Parse age-at-census from a cell like '26', '26 years', '0'. Rejects
    anything looking like a year (>=200). Returns None if no leading int."""
    if not s:
        return None
    m = _AGE_RE.match(s)
    if not m:
        return None
    age = int(m.group(1))
    if 0 <= age <= 120:
        return age
    return None


def _census_year(record_type: str) -> int | None:
    m = _CENSUS_YEAR_RE.search(record_type or "")
    return int(m.group(1)) if m else None


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


def score_record(person: Person, rec: Record) -> tuple[int, dict[str, int]]:
    """Simple additive score. Returns (total, breakdown) where breakdown
    maps a component key to its signed delta (components that contribute
    zero are omitted). Manual-link rows return (0, {}).
    """
    if "manual" in rec.record_type.lower():
        return 0, {}
    breakdown: dict[str, int] = {"base": 1}
    name_l = (rec.name_found or "").lower()
    place_l = (rec.place or "").lower()
    details_l = (rec.other_details or "").lower()
    haystack = name_l + " | " + place_l + " | " + details_l

    if person.forename:
        all_fnames = person.all_forenames(use_nicknames=True)
        if any(fn and fn.lower() in name_l for fn in all_fnames):
            breakdown["forename"] = 3
        elif name_l:
            breakdown["forename_mismatch"] = -4
    if person.surname and person.surname.lower() in name_l:
        breakdown["surname_primary"] = 3
    else:
        target_code = soundex(person.surname)
        if target_code:
            for tok in re.findall(r"[A-Za-z']{3,}", name_l):
                if soundex(tok) == target_code:
                    breakdown["surname_soundex"] = 2
                    break
    for variant in person.surname_variants:
        if variant and variant.lower() in name_l:
            breakdown["surname_variant"] = 1
            break
    if person.county and person.county.lower() in haystack:
        breakdown["county"] = 2
    if person.townland and person.townland.lower() in haystack:
        breakdown["townland"] = 4
    else:
        matched_variant = False
        for variant in getattr(person, "townland_variants", []) or []:
            if variant and variant.lower() in haystack:
                breakdown["townland_variant"] = 3
                matched_variant = True
                break
        if not matched_variant and person.townland:
            t_code = soundex(person.townland)
            if t_code:
                for tok in re.findall(r"[A-Za-z']{4,}", place_l):
                    if soundex(tok) == t_code:
                        breakdown["townland_soundex"] = 2
                        break
    sparse = not rec.place and not rec.age_or_year
    if not sparse:
        matched_narrow = False
        rt_l = (rec.record_type or "").lower()
        if "census" in rt_l:
            cy = _census_year(rec.record_type)
            age = _parse_age(rec.age_or_year)
            bw = person.year_window()
            if cy and age is not None and bw:
                implied = cy - age
                # Tight (±1y) rewarded more than loose (±2y); >5y off penalised.
                if bw[0] - 1 <= implied <= bw[1] + 1:
                    breakdown["census_age"] = 4
                    matched_narrow = True
                elif bw[0] - 2 <= implied <= bw[1] + 2:
                    breakdown["census_age"] = 3
                    matched_narrow = True
                elif implied < bw[0] - 5 or implied > bw[1] + 5:
                    breakdown["census_age_mismatch"] = -3
        if not matched_narrow:
            year = _extract_year(rec.age_or_year, rec.name_found, rec.record_type)
            expected = _expected_event_window(rec.record_type, person.year_window())
            if _year_match(year, expected):
                narrow_lo, narrow_hi = expected  # type: ignore[misc]
                span = narrow_hi - narrow_lo
                breakdown["year_window"] = 3 if span <= 30 else 2
    if sparse:
        breakdown["sparse"] = -1
    return sum(breakdown.values()), breakdown
