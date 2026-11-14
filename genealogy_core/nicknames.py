"""Irish/Gaelic nickname dictionary and bidirectional forename expansion."""

NICKNAMES: dict[str, list[str]] = {
    "john":      ["jack", "johnny", "sean", "shane"],
    "james":     ["jim", "jimmy", "jem", "seamus"],
    "william":   ["will", "bill", "billy", "liam"],
    "patrick":   ["pat", "paddy", "patsy"],
    "michael":   ["mick", "mickey", "mike"],
    "thomas":    ["tom", "tommy", "tam"],
    "daniel":    ["dan", "danny"],
    "david":     ["dave", "davy", "davie", "dai"],
    "edward":    ["ed", "ted", "ned", "eddie"],
    "robert":    ["rob", "bob", "bobby", "robbie"],
    "richard":   ["rick", "dick", "richie"],
    "joseph":    ["joe", "joey", "jos"],
    "peter":     ["pete", "piaras"],
    "francis":   ["frank", "francie", "proinsias"],
    "charles":   ["charlie", "chas", "cathal"],
    "george":    ["geo"],
    "henry":     ["harry", "hal", "enri"],
    "cornelius": ["con", "connie"],
    "denis":     ["dennis", "denny"],
    "bartholomew": ["bart", "batt", "bartley"],
    "timothy":   ["tim", "timmy"],
    "mary":      ["maria", "molly", "polly", "mamie", "mae", "may", "maire"],
    "margaret":  ["maggie", "meg", "peggy", "madge", "margery", "mairead"],
    "catherine": ["kate", "katie", "kathleen", "cat", "kitty", "caitriona"],
    "elizabeth": ["eliza", "liz", "lizzie", "beth", "betty", "bess"],
    "bridget":   ["bride", "biddy", "bridie", "brigid"],
    "ellen":     ["nellie", "nell", "helen"],
    "anne":      ["ann", "annie", "nan", "nancy"],
    "sarah":     ["sally", "sadie", "sorcha"],
    "johanna":   ["hannah", "hanora", "nora", "honoria"],
    "honora":    ["nora", "honor", "hanora", "johanna"],
    "eileen":    ["aileen", "eilish", "ellen"],
    "winifred":  ["winnie", "una"],
}

MALE_NAMES: frozenset[str] = frozenset(
    k for k in ("john","james","william","patrick","michael","thomas","daniel",
                "david","edward","robert","richard","joseph","peter","francis",
                "charles","george","henry","cornelius","denis","bartholomew","timothy")
    for k in ([k] + NICKNAMES.get(k, []))
)

FEMALE_NAMES: frozenset[str] = frozenset(
    k for k in ("mary","margaret","catherine","elizabeth","bridget","ellen",
                "anne","sarah","johanna","honora","eileen","winifred")
    for k in ([k] + NICKNAMES.get(k, []))
)


def expand_forename(name: str) -> list[str]:
    """Return the forename plus every known nickname/variant. Walks both
    directions of NICKNAMES so e.g. 'Jack' expands to 'john' too.
    """
    original = (name or "").strip()
    if not original:
        return []
    key = original.lower()
    variants: list[str] = [original]
    seen = {key}
    for v in NICKNAMES.get(key, []):
        if v not in seen:
            variants.append(v)
            seen.add(v)
    for canonical, nicks in NICKNAMES.items():
        if key in nicks and canonical not in seen:
            variants.append(canonical)
            seen.add(canonical)
            for v in nicks:
                if v not in seen:
                    variants.append(v)
                    seen.add(v)
    return variants
