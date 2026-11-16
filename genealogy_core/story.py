"""Build a plain-English life story for a person from their matched records.

Pure logic. No I/O, no HTML. Returns a list of sentences; callers render them.
"""
import re

from .models import Person, Record


# Timeline fun-fact events the story can riff on.
# (year, phrase) — phrase slots into "{name} was {age} when {phrase} ({year})."
STORY_EVENTS: list[tuple[int, str]] = [
    (1845, "the Great Famine began"),
    (1864, "Ireland started keeping civil birth records"),
    (1901, "the 1901 census was taken"),
    (1911, "the 1911 census was taken"),
    (1912, "the Titanic sank"),
    (1914, "World War I began"),
    (1916, "the Easter Rising happened in Dublin"),
    (1918, "World War I ended"),
    (1922, "the Irish Free State was founded"),
    (1926, "the 1926 census was taken"),
    (1939, "World War II began"),
]


_YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


def _extract_year(*fields: str) -> int | None:
    """Pick the first 4-digit year (1500-2099) from any of the given strings."""
    for s in fields:
        if not s:
            continue
        m = _YEAR_RE.search(s)
        if m:
            return int(m.group(1))
    return None


def _story_source(records: list[Record]) -> list[Record]:
    """Prefer confirmed records; fall back to Likely (score>=10) if none confirmed."""
    confirmed = [r for r in records if r.status == "confirmed"]
    if confirmed:
        return confirmed
    return [r for r in records if r.score >= 10 and r.status != "rejected"]


def build_story(person: Person, records: list[Record]) -> list[str]:
    """Return a chronological list of kid-friendly sentences about person.

    Empty list = not enough data; caller should show a fallback.
    """
    source = _story_source(records)
    if not source:
        return []

    name = (person.forename or "They").strip()
    events: list[tuple[int, str]] = []
    seen: set[tuple[str, int]] = set()

    for r in source:
        rt = (r.record_type or "").lower()
        place = (r.place or "").strip()
        year = _extract_year(r.age_or_year, r.record_type, r.other_details)

        if year is None:
            continue

        kind: str | None = None
        template: str | None = None
        if "birth" in rt or "baptism" in rt:
            kind = "birth"
            template = f"{name} was born"
        elif "marriage" in rt:
            kind = "marriage"
            template = f"{name} got married"
        elif "death" in rt or "burial" in rt:
            kind = "death"
            template = f"{name} died"
        elif "census" in rt:
            kind = "census"
            template = f"In {year}, {name} was counted in the census"
        elif "griffith" in rt:
            kind = "griffith"
            template = f"{name} was listed in Griffith's Valuation"

        if kind is None or template is None:
            continue
        key = (kind, year)
        if key in seen:
            continue
        seen.add(key)

        sentence = template
        if kind == "census":
            if place:
                sentence += f" in {place}"
        elif kind == "griffith":
            if place:
                sentence += f" ({place})"
        else:
            if place:
                sentence += f" in {place}"
            sentence += f" in {year}" if kind != "birth" else f" around {year}"

        if not sentence.endswith("."):
            sentence += "."
        events.append((year, sentence))

    events.sort(key=lambda e: e[0])
    sentences = [s for _, s in events]

    # Fun facts from the timeline — need a birth year. Skip events whose year
    # already appears in the story (e.g. don't say "age 31 when the 1901 census
    # was taken" if we already listed the 1901 census above).
    birth = person.birth_year_approx
    if birth:
        story_years = {y for y, _ in events}
        added = 0
        for ev_year, phrase in sorted(STORY_EVENTS):
            if ev_year in story_years:
                continue
            age = ev_year - birth
            if 5 <= age <= 85:
                sentences.append(
                    f"\u2728 {name} was {age} when {phrase} ({ev_year})."
                )
                added += 1
                if added >= 2:
                    break

    return sentences
