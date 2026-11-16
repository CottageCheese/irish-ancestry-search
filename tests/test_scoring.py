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


def _score(person, rec):
    return score_record(person, rec)[0]


def test_manual_rows_score_zero():
    r = _rec(record_type="Manual lookup", name_found="John Furey")
    total, bd = score_record(_person(), r)
    assert total == 0
    assert bd == {}


def test_forename_and_surname_both_matched():
    r = _rec(name_found="John Furey", place="Galway")
    s = _score(_person(), r)
    assert s >= 1 + 3 + 3 + 2  # base + forename + surname + county


def test_breakdown_sums_to_total():
    r = _rec(name_found="John Furey", place="Ballinasloe", age_or_year="1878")
    total, bd = score_record(_person(), r)
    assert sum(bd.values()) == total
    assert bd["base"] == 1
    assert bd["forename"] == 3
    assert bd["surname_primary"] == 3
    assert bd["townland"] == 4
    assert bd["year_window"] >= 2


def test_breakdown_marks_soundex_and_variant():
    r = _rec(name_found="John Feury", place="Dublin")
    _, bd = score_record(_person(), r)
    assert bd.get("surname_soundex") == 2
    assert "surname_primary" not in bd


def test_breakdown_marks_sparse_penalty():
    r = _rec(name_found="Someone")
    _, bd = score_record(_person(), r)
    assert bd.get("sparse") == -1


def test_townland_variant_scores_when_main_misses():
    p = Person(surname="Furey", forename="John", birth_year_approx=1875, year_range=5,
               county="Galway", townland="Ballinasloe",
               townland_variants=["Ballybeg", "Ballybeag"])
    r = _rec(name_found="John Furey", place="Ballybeag, County Galway")
    baseline = _rec(name_found="John Furey", place="Dublin")
    assert _score(p, r) - _score(p, baseline) >= 3
    _, bd = score_record(p, r)
    assert bd.get("townland_variant") == 3


def test_townland_soundex_fallback():
    p = Person(surname="Furey", forename="John", birth_year_approx=1875, year_range=5,
               county="Galway", townland="Ballinasloe")
    r = _rec(name_found="John Furey", place="Ballinasloo")  # misspell, soundex match
    baseline = _rec(name_found="John Furey", place="Dublin")
    assert _score(p, r) - _score(p, baseline) >= 2
    _, bd = score_record(p, r)
    assert bd.get("townland_soundex") == 2


def test_townland_match_adds_four():
    with_t = _rec(name_found="John Furey", place="Ballinasloe")
    without_t = _rec(name_found="John Furey", place="Dublin")
    assert _score(_person(), with_t) - _score(_person(), without_t) >= 4


def test_year_in_window_adds_two():
    inside = _rec(name_found="John Furey", age_or_year="1878")
    outside = _rec(name_found="John Furey", age_or_year="1500")
    assert _score(_person(), inside) > _score(_person(), outside)


def test_census_age_implies_birth_year_in_window():
    # Born ~1875 ±5 → in 1901 census expect age ~26. Age "26" implies birth 1875. Match.
    r = _rec(record_type="Census 1901", name_found="John Furey",
             age_or_year="26", place="Galway")
    _, bd = score_record(_person(), r)
    assert bd.get("census_age") == 4  # tight ±1y match
    assert "year_window" not in bd  # census-age path wins, year path skipped


def test_census_age_tolerance_two_years():
    # Birth window 1870-1880. 1901 census age 19 → implied 1882, within +2 tolerance.
    r = _rec(record_type="Census 1901", name_found="John Furey",
             age_or_year="19", place="Galway")
    _, bd = score_record(_person(), r)
    assert bd.get("census_age") == 3


def test_census_age_outside_window_no_bonus():
    # Age 60 in 1901 → implied 1841, way outside 1870-1880 window.
    r = _rec(record_type="Census 1901", name_found="John Furey",
             age_or_year="60", place="Galway")
    _, bd = score_record(_person(), r)
    assert "census_age" not in bd
    assert bd.get("census_age_mismatch") == -3


def test_census_age_with_units_parses():
    r = _rec(record_type="Census 1911", name_found="John Furey",
             age_or_year="36 years", place="Galway")
    _, bd = score_record(_person(), r)
    assert bd.get("census_age") == 4  # 1911 - 36 = 1875, tight ±1y


def test_census_infant_age_zero_parses():
    p = Person(surname="Furey", forename="Baby",
               birth_year_approx=1901, year_range=1, county="Galway")
    r = _rec(record_type="Census 1901", name_found="Baby Furey",
             age_or_year="0", place="Galway")
    _, bd = score_record(p, r)
    assert bd.get("census_age") == 4  # tight ±1y match


def test_non_census_row_ignores_age_path():
    # Birth record with explicit year string still uses year_window, not census_age.
    r = _rec(record_type="Civil Birth", name_found="John Furey",
             age_or_year="1878", place="Galway")
    _, bd = score_record(_person(), r)
    assert "census_age" not in bd
    assert bd.get("year_window", 0) >= 2


def test_soundex_fallback_when_surname_misspelled():
    # Feury shares F600 soundex with Furey; give it a place to avoid the
    # sparse-row penalty so the +2 fuzzy bonus shows through cleanly.
    r = _rec(name_found="John Feury", place="Dublin")
    assert _score(_person(), r) >= 1 + 3 + 2  # base + forename + soundex


def test_sparse_row_penalised():
    r = _rec(name_found="Someone")  # no place no year
    assert _score(_person(), r) <= 0


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


def test_kin_cross_check_uses_household():
    """Father name in household list (not name_found/other_details) triggers kin match."""
    r = _rec(name_found="John Furey", place="Ballinasloe",
             other_details="")
    r.household = ["Thomas Furey", "Mary Kelly"]
    result = kin_cross_check(_person(), r)
    assert "father" in result  # Thomas from household


def test_annotate_adds_kin_bonus():
    r = _rec(name_found="John Furey", place="Ballinasloe",
             other_details="Thomas and Mary Kelly in same house")
    before = _score(_person(), r)
    annotate_records(_person(), [r])
    assert r.score == before + 5
    assert "father" in r.kin_hits
    assert r.score_breakdown.get("kin") == 5
    assert sum(r.score_breakdown.values()) == r.score
