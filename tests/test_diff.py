import csv
from pathlib import Path

from genealogy_core.models import Record
from genealogy_core.diff import load_previous_keys, mark_new_records


def _write_csv(path: Path, rows: list[dict]):
    fields = [
        "person_searched", "site", "record_type", "name_found",
        "age_or_year", "place", "other_details", "link",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def test_mark_new_flags_only_unseen(tmp_path):
    prev = tmp_path / "prev.csv"
    _write_csv(prev, [{
        "person_searched": "John Furey", "site": "NAI",
        "record_type": "Census 1901", "name_found": "J Furey",
        "age_or_year": "26", "place": "Galway",
        "link": "http://x/?id=1",
    }])
    keys = load_previous_keys(prev)
    assert len(keys) == 1

    r_old = Record(person_searched="John Furey", site="NAI",
                   record_type="Census 1901", name_found="J Furey",
                   age_or_year="26", place="Galway",
                   other_details="", link="http://x/?id=1")
    r_new = Record(person_searched="John Furey", site="NAI",
                   record_type="Census 1901", name_found="J Furey",
                   age_or_year="26", place="Galway",
                   other_details="", link="http://x/?id=2")
    n = mark_new_records([r_old, r_new], keys)
    assert n == 1
    assert r_old.is_new is False
    assert r_new.is_new is True


def test_load_previous_keys_missing_file(tmp_path):
    assert load_previous_keys(tmp_path / "nope.csv") == set()
