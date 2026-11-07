"""Diff this run's records against the previous results.csv."""
import csv
import re
from pathlib import Path

from .models import Record

RECORD_ID_RE = re.compile(r"[?&#](?:record_id|id)=([^&]+)")


def _record_diff_key(r: Record) -> tuple:
    """Stable identity used for diffing results against the previous run."""
    m = RECORD_ID_RE.search(r.link or "")
    if m:
        return (r.person_searched, r.site, m.group(1))
    return (r.person_searched, r.site, r.record_type, r.name_found, r.age_or_year, r.place)


def load_previous_keys(path: Path) -> set[tuple]:
    if not path.exists():
        return set()
    keys: set[tuple] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rec = Record(
                    person_searched=row.get("person_searched", ""),
                    site=row.get("site", ""),
                    record_type=row.get("record_type", ""),
                    name_found=row.get("name_found", ""),
                    age_or_year=row.get("age_or_year", ""),
                    place=row.get("place", ""),
                    other_details=row.get("other_details", ""),
                    link=row.get("link", ""),
                )
                keys.add(_record_diff_key(rec))
    except Exception:
        return set()
    return keys


def mark_new_records(records: list[Record], prev_keys: set[tuple]) -> int:
    if not prev_keys:
        return 0
    n = 0
    for r in records:
        if _record_diff_key(r) not in prev_keys:
            r.is_new = True
            n += 1
    return n
