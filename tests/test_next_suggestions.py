"""Unit tests for _next_suggestions in web.py — the 'Who next?' done-panel helper."""
import importlib

web = importlib.import_module("web")
_next_suggestions = web._next_suggestions


def _row(**kw):
    base = {c: "" for c in web.CSV_COLUMNS}
    base.update(kw)
    return base


def test_empty_when_person_not_in_csv():
    rows = [_row(forename="Mary", surname="Smith")]
    assert _next_suggestions("William Furey", rows) == []


def test_generic_parent_slots_when_no_relatives_recorded():
    """Even with no father/mother recorded, offer prefill-only slots so
    the user can quickly add parents with surname + guessed birth year."""
    rows = [_row(forename="William", surname="Furey", birth_year_approx="1870",
                 county="Galway")]
    out = _next_suggestions("William Furey", rows)
    assert len(out) == 2  # dad + mum, no spouse
    dad = next(s for s in out if s["sex"] == "M")
    assert dad["prefill_only"] is True
    assert dad["forename"] == ""
    assert dad["surname"] == "Furey"  # inherits subject's surname
    assert dad["birth_year"] == "1840"
    mum = next(s for s in out if s["sex"] == "F")
    assert mum["prefill_only"] is True
    assert mum["forename"] == ""
    assert mum["surname"] == ""  # maiden name unknown


def test_empty_when_person_missing_entirely():
    assert _next_suggestions("Nobody Else", [_row(forename="William", surname="Furey")]) == []


def test_father_suggestion_with_surname_fallback():
    """Father recorded as forename-only → inherit subject's surname.
    Mother absent → generic prefill slot for mum."""
    rows = [_row(forename="William", surname="Furey", birth_year_approx="1870",
                 father="John")]
    out = _next_suggestions("William Furey", rows)
    dad = next(s for s in out if s["sex"] == "M")
    assert dad["forename"] == "John"
    assert dad["surname"] == "Furey"
    assert dad["birth_year"] == "1840"
    assert dad["prefill_only"] is False


def test_mother_suggestion_no_surname_fallback():
    """Maiden name unknown → surname stays blank."""
    rows = [_row(forename="William", surname="Furey", birth_year_approx="1870",
                 mother="Mary")]
    out = _next_suggestions("William Furey", rows)
    mum = next(s for s in out if s["sex"] == "F")
    assert mum["forename"] == "Mary"
    assert mum["surname"] == ""
    assert mum["prefill_only"] is False


def test_spouse_uses_subject_birth_year():
    rows = [_row(forename="William", surname="Furey", birth_year_approx="1870",
                 spouse="Bridget Walsh")]
    out = _next_suggestions("William Furey", rows)
    spouse = next(s for s in out if s["label"].endswith("partner"))
    assert spouse["forename"] == "Bridget"
    assert spouse["surname"] == "Walsh"
    assert spouse["birth_year"] == "1870"
    assert spouse["prefill_only"] is False


def test_all_three_relatives():
    rows = [_row(forename="William", surname="Furey", birth_year_approx="1870",
                 father="John Furey", mother="Mary", spouse="Bridget Walsh",
                 county="Galway")]
    out = _next_suggestions("William Furey", rows)
    labels = [s["label"] for s in out]
    assert labels == ["William's dad", "William's mum", "William's partner"]
    assert all(s["county"] == "Galway" for s in out)
    assert all(not s["prefill_only"] for s in out)


def test_named_relative_already_in_csv_is_skipped():
    """Dad already added as his own row → dad slot disappears entirely
    (not demoted to generic prefill, since the name is known and used)."""
    rows = [
        _row(forename="William", surname="Furey", father="John Furey"),
        _row(forename="John", surname="Furey"),  # dad already added
    ]
    out = _next_suggestions("William Furey", rows)
    # Only mum (generic prefill) remains.
    assert len(out) == 1
    assert out[0]["sex"] == "F"
    assert out[0]["prefill_only"] is True


def test_handles_missing_birth_year():
    rows = [_row(forename="William", surname="Furey", father="John Furey")]
    out = _next_suggestions("William Furey", rows)
    dad = next(s for s in out if s["sex"] == "M")
    assert dad["birth_year"] == ""


def test_bad_birth_year_tolerated():
    rows = [_row(forename="William", surname="Furey",
                 birth_year_approx="unknown", father="John Furey")]
    out = _next_suggestions("William Furey", rows)
    dad = next(s for s in out if s["sex"] == "M")
    assert dad["birth_year"] == ""


def test_label_strips_paren_suffix():
    """RUN.label may read 'William Furey (saved)' — strip the suffix."""
    rows = [_row(forename="William", surname="Furey", father="John Furey")]
    out = _next_suggestions("William Furey (saved)", rows)
    dad = next(s for s in out if s["sex"] == "M")
    assert dad["forename"] == "John"


def test_empty_label_returns_empty():
    assert _next_suggestions("", [_row(forename="x", surname="y")]) == []
