from genealogy_core.soundex import soundex, expand_surnames


def test_soundex_collapses_furey_variants():
    assert soundex("Furey") == "F600"
    assert soundex("Fury") == "F600"
    assert soundex("Feury") == "F600"


def test_soundex_classic_examples():
    assert soundex("Robert") == "R163"
    assert soundex("Rupert") == "R163"
    assert soundex("Ashcraft") == "A261"


def test_soundex_empty_and_nonalpha():
    assert soundex("") == ""
    assert soundex("123") == ""


def test_expand_surnames_dedupes_case_insensitive():
    out = expand_surnames(["Furey", "furey", "FUREY", "Fury", ""], use_soundex=True)
    assert out == ["Furey", "Fury"]


def test_expand_surnames_does_not_collapse_same_soundex():
    out = expand_surnames(["Furey", "Fury"], use_soundex=True)
    assert "Furey" in out and "Fury" in out
