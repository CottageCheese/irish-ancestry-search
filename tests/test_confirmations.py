from genealogy_core.confirmations import (
    apply_confirmations,
    filter_rejected,
    load_confirmations,
    record_key,
    save_confirmations,
    set_status,
)
from genealogy_core.models import Record


def _mk(**kw) -> Record:
    base = dict(
        person_searched="John Furey", site="NAI", record_type="Census 1901",
        name_found="J Furey", age_or_year="26", place="Galway",
        other_details="", link="http://x/?id=1",
    )
    base.update(kw)
    return Record(**base)


def test_record_key_stable_and_unique():
    r1 = _mk()
    r2 = _mk()
    r3 = _mk(link="http://x/?id=2")
    assert record_key(r1) == record_key(r2)
    assert record_key(r1) != record_key(r3)
    assert len(record_key(r1)) == 12


def test_set_status_and_clear():
    data: dict = {}
    k = "abc123"
    set_status(data, k, "confirmed", "John Furey")
    assert data[k]["status"] == "confirmed"
    set_status(data, k, "rejected", "John Furey")
    assert data[k]["status"] == "rejected"
    set_status(data, k, "", "John Furey")
    assert k not in data


def test_set_status_rejects_invalid():
    data: dict = {}
    set_status(data, "k", "weird", "p")
    assert "k" not in data


def test_apply_confirmations_stamps_status():
    r = _mk()
    data = {record_key(r): {"status": "confirmed", "person": "John Furey"}}
    apply_confirmations([r], data)
    assert r.status == "confirmed"


def test_apply_confirmations_clears_when_missing():
    r = _mk()
    r.status = "confirmed"
    apply_confirmations([r], {})
    assert r.status == ""


def test_filter_rejected_drops_rejected():
    r_keep = _mk()
    r_drop = _mk(link="http://x/?id=2")
    data = {record_key(r_drop): {"status": "rejected", "person": "x"}}
    out = filter_rejected([r_keep, r_drop], data)
    assert out == [r_keep]


def test_load_save_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    assert load_confirmations(p) == {}
    data = {"abc": {"status": "confirmed", "person": "x"}}
    save_confirmations(p, data)
    assert load_confirmations(p) == data


def test_load_handles_corrupt(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("not json", encoding="utf-8")
    assert load_confirmations(p) == {}
