from genealogy_core.models import Record
from genealogy_core.render import (
    render_results_html,
    render_person_page,
    render_index,
    render_person_table,
    summarise_person,
    slugify,
)


def _rec(**kw):
    base = dict(
        person_searched="John Furey", site="NAI (1901)",
        record_type="Census 1901", name_found="John Furey",
        age_or_year="26", place="Galway",
        other_details="head of household", link="http://x/?id=1",
    )
    base.update(kw)
    return Record(**base)


def test_slugify_filesystem_safe():
    assert slugify("John Furey") == "john_furey"
    assert slugify("Mary O'Brien") == "mary_o_brien"
    assert slugify("") == "unknown"
    assert slugify("!@#") == "unknown"


def test_summarise_person_counts_by_type():
    recs = [
        _rec(record_type="Census 1901"),
        _rec(record_type="Census 1911"),
        _rec(record_type="Civil Birth"),
        _rec(record_type="Manual lookup"),
    ]
    out = summarise_person(recs)
    assert "2 census hits" in out
    assert "1 civil births" in out
    assert "1 manual link" in out


def test_summarise_empty():
    assert summarise_person([]) == "no records"


def test_results_html_contains_record_fields():
    recs = [_rec(score=12, name_found="John Furey", place="Ballinasloe")]
    out = render_results_html(recs, stamp="2026-11-07 10:00")
    assert "<!doctype html>" in out
    assert "John Furey" in out
    assert "Ballinasloe" in out
    assert "2026-11-07 10:00" in out
    assert "tier-Likely" in out
    assert "</body></html>" in out


def test_results_html_escapes_html():
    recs = [_rec(name_found="<script>x</script>")]
    out = render_results_html(recs, stamp="t")
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_results_html_empty_shows_empty_message():
    out = render_results_html([], stamp="t")
    assert "No records found" in out


def test_weak_rows_collapsed_by_default():
    weak = _rec(score=2, name_found="Weak hit")  # score 2 => Weak
    strong = _rec(score=12, name_found="Strong hit")
    out = render_results_html([strong, weak], stamp="t")
    assert "<details>" in out
    assert "weak-tier record" in out


def test_weak_rows_inline_when_collapse_false():
    weak = _rec(score=2)
    out = render_results_html([weak], stamp="t", collapse_weak=False)
    assert "<details>" not in out


def test_new_badge_rendered():
    r = _rec(is_new=True)
    assert "NEW" in render_person_table([r])


def test_thumbnail_rendered_as_img():
    r = _rec(thumbnail="http://x/scan.jpg")
    out = render_person_table([r])
    assert "img" in out and "scan.jpg" in out


def test_person_page_has_back_link():
    out = render_person_page("John Furey", [_rec()], stamp="t",
                             back_href="../results.html")
    assert "../results.html" in out
    assert "back to index" in out


def test_card_has_record_icon():
    recs = [_rec(record_type="Civil Birth", score=12)]
    out = render_person_table(recs)
    assert "👶" in out  # birth icon


def test_card_has_star_rating_by_tier():
    out = render_person_table([_rec(score=12)])  # Likely
    assert "★★★" in out
    out = render_person_table([_rec(score=6)])  # Possible
    assert "★★" in out


def test_card_sentence_includes_name_and_place():
    out = render_person_table([_rec(name_found="Patrick Furey", place="Ballinasloe", score=12)])
    assert "Patrick Furey" in out
    assert "Ballinasloe" in out


def test_card_kin_chip_rendered():
    out = render_person_table([_rec(kin_hits="Mary (spouse)", score=12)])
    assert "chip-kin" in out
    assert "Mary" in out


def test_card_variant_chip_when_name_differs():
    r = _rec(score=12, name_used="Johannes Furey")
    out = render_person_table([r])
    assert "chip-variant" in out
    assert "Johannes" in out


def test_card_confirmed_status_class():
    out = render_person_table([_rec(score=12, status="confirmed")])
    assert "rec-card" in out
    assert "confirmed" in out


def test_person_hero_shows_counts():
    recs = [_rec(score=12), _rec(score=6), _rec(score=2)]
    out = render_person_page("John Furey", recs, stamp="t", back_href="x")
    assert "person-hero" in out
    assert "progress" in out
    # avatar initials
    assert "JF" in out


def test_record_icon_griffiths():
    out = render_person_table([_rec(record_type="Griffith's Valuation", score=12)])
    assert "🏡" in out


def test_index_lists_people_and_counts():
    recs = [
        _rec(person_searched="John Furey", score=12),
        _rec(person_searched="John Furey", score=2),
        _rec(person_searched="Mary Kelly", score=7),
    ]
    out = render_index(recs, {"John Furey": "p/j.html", "Mary Kelly": "p/m.html"},
                       stamp="t")
    assert "p/j.html" in out and "p/m.html" in out
    assert "John Furey" in out and "Mary Kelly" in out
    assert "2 record(s)" in out
    assert "tier-Likely" in out
