"""Dedupe: exact (record_id) pass + semantic (type/name/place/year) pass."""
from genealogy_core.models import Record
from genealogy_scrape.runner import dedupe_records


def _rec(**kw) -> Record:
    base = dict(
        person_searched="Michael Murphy",
        site="irishgenealogy_civil",
        record_type="Birth",
        name_found="Michael Murphy",
        age_or_year="1870",
        place="Cork",
        other_details="",
        link="https://example.com/x",
        score=5,
    )
    base.update(kw)
    return Record(**base)


def test_exact_dedupe_same_record_id():
    r1 = _rec(link="https://civilrecords.irishgenealogy.ie/details?record_id=12345")
    r2 = _rec(link="https://civilrecords.irishgenealogy.ie/details?record_id=12345",
              name_found="Mick Murphy")
    out = dedupe_records([r1, r2])
    assert len(out) == 1


def test_semantic_dedupe_distinct_ids_same_event():
    # Same person/site/type/name/place/year, different URLs — collapse.
    r1 = _rec(link="https://a.example/1", score=5)
    r2 = _rec(link="https://a.example/2", score=9)
    r3 = _rec(link="https://a.example/3", score=3)
    out = dedupe_records([r1, r2, r3])
    assert len(out) == 1
    assert out[0].score == 9  # highest score wins


def test_semantic_keeps_distinct_years():
    r1 = _rec(age_or_year="1870", link="https://a.example/1")
    r2 = _rec(age_or_year="1872", link="https://a.example/2")
    out = dedupe_records([r1, r2])
    assert len(out) == 2


def test_semantic_keeps_distinct_record_types():
    r1 = _rec(record_type="Birth", link="https://a.example/1")
    r2 = _rec(record_type="Baptism", link="https://a.example/2")
    out = dedupe_records([r1, r2])
    assert len(out) == 2


def test_semantic_case_insensitive_on_name_place():
    r1 = _rec(name_found="Michael Murphy", place="Cork", link="https://a.example/1")
    r2 = _rec(name_found="MICHAEL MURPHY", place="cork ", link="https://a.example/2")
    out = dedupe_records([r1, r2])
    assert len(out) == 1


def test_different_people_not_merged():
    r1 = _rec(person_searched="Michael Murphy", link="https://a.example/1")
    r2 = _rec(person_searched="John Murphy", link="https://a.example/2")
    out = dedupe_records([r1, r2])
    assert len(out) == 2
