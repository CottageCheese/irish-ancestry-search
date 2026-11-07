from genealogy_core.nicknames import expand_forename


def test_john_expands_to_jack_and_sean():
    out = [v.lower() for v in expand_forename("John")]
    assert "jack" in out
    assert "sean" in out


def test_jack_expands_back_to_john():
    out = [v.lower() for v in expand_forename("Jack")]
    assert "john" in out


def test_empty_returns_empty():
    assert expand_forename("") == []
    assert expand_forename("  ") == []


def test_unknown_name_returns_itself_only():
    assert expand_forename("Zephyr") == ["Zephyr"]


def test_original_case_preserved():
    assert expand_forename("John")[0] == "John"
