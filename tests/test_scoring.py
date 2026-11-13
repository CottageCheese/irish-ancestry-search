from genealogy_core.models import Person, Record, confidence_tier
from genealogy_core.scoring import score_record
from genealogy_core.kin import kin_cross_check, annotate_records


def _person():
    return Person(
        surname="Furey", forename="John", surname_variants=["Fury"],
        birth_year_approx=1875, year_range=5,
        county="Galway", townland="Ballinasloe",
        father="Thomas Furey", mother="Mary Kelly", spouse="Bridget Hynes",
    )


def _rec(**kw):
    base = dict(
        person_searched="John Furey", site="NAI", record_type="Census 1901",
        name_found="", age_or_year="", place="", other_details="", link="",
    )
    base.update(kw)
    return Record(**base)


def test_manual_rows_score_zero():
    r = _rec(record_type="Manual lookup", name_found="John Furey")
    assert score_record(_person(), r) == 0


def test_forename_and_surname_both_matched():
    r = _rec(name_found="John Furey", place="Galway")
    s = score_record(_person(), r)
    assert s >= 1 + 3 + 3 + 2  # base + forename + surname + county


def test_townland_variant_scores_when_main_misses():
    p = Person(surname="Furey", forename="John", birth_year_approx=1875, year_range=5,
               county="Galway", townland="Ballinasloe",
               townland_variants=["Ballybeg", "Ballybeag"])
    r = _rec(name_found="John Furey", place="Ballybeag, County Galway")
    baseline = _rec(name_found="John Furey", place="Dublin")
    assert score_record(p, r) - score_record(p, baseline) >= 3


def test_townland_soundex_fallback():
    p = Person(surname="Furey", forename="John", birth_year_approx=1875, year_range=5,
               county="Galway", townland="Ballinasloe")
    r = _rec(name_found="John Furey", place="Ballinasloo")  # misspell, soundex match
    baseline = _rec(name_found="John Furey", place="Dublin")
    assert score_record(p, r) - score_record(p, baseline) >= 2


def test_townland_match_adds_four():
    with_t = _rec(name_found="John Furey", place="Ballinasloe")
    without_t = _rec(name_found="John Furey", place="Dublin")
    assert score_record(_person(), with_t) - score_record(_person(), without_t) >= 4


def test_year_in_window_adds_two():
    inside = _rec(name_found="John Furey", age_or_year="1878")
    outside = _rec(name_found="John Furey", age_or_year="1500")
    assert score_record(_person(), inside) > score_record(_person(), outside)


def test_soundex_fallback_when_surname_misspelled():
    # Feury shares F600 soundex with Furey; give it a place to avoid the
    # sparse-row penalty so the +2 fuzzy bonus shows through cleanly.
    r = _rec(name_found="John Feury", place="Dublin")
    assert score_record(_person(), r) >= 1 + 3 + 2  # base + forename + soundex


def test_sparse_row_penalised():
    r = _rec(name_found="Someone")  # no place no year
    assert score_record(_person(), r) <= 0


def test_confidence_tier_buckets():
    assert confidence_tier(0) == "Manual"
    assert confidence_tier(1) == "Weak"
    assert confidence_tier(5) == "Possible"
    assert confidence_tier(10) == "Likely"


def test_kin_cross_check_matches_father():
    r = _rec(name_found="John Furey", other_details="Thomas head of household")
    assert "father" in kin_cross_check(_person(), r)


def test_kin_cross_check_excludes_own_surname():
    """A record for 'Furey, John' shouldn't trigger a father match just
    because father 'Thomas Furey' shares the surname token."""
    r = _rec(name_found="John Furey", other_details="")
    assert kin_cross_check(_person(), r) == ""


def test_annotate_adds_kin_bonus():
    r = _rec(name_found="John Furey", place="Ballinasloe",
             other_details="Thomas and Mary Kelly in same house")
    before = score_record(_person(), r)
    annotate_records(_person(), [r])
    assert r.score == before + 5
    assert "father" in r.kin_hits
