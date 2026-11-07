"""Soundex phonetic code and surname-list dedupe. Pure functions."""
import re


def soundex(name: str) -> str:
    """Classic 4-char Soundex code. Collapses spelling variants that sound
    alike (Furey/Fury/Feury -> F600). Returns "" for empty / non-alpha input.
    """
    if not name:
        return ""
    s = re.sub(r"[^A-Za-z]", "", name).upper()
    if not s:
        return ""
    mapping = {
        **dict.fromkeys("BFPV", "1"),
        **dict.fromkeys("CGJKQSXZ", "2"),
        **dict.fromkeys("DT", "3"),
        **dict.fromkeys("L", "4"),
        **dict.fromkeys("MN", "5"),
        **dict.fromkeys("R", "6"),
    }
    first = s[0]
    tail: list[str] = []
    prev = mapping.get(first, "")
    for ch in s[1:]:
        code = mapping.get(ch, "")
        if ch in "HW":
            continue
        if code and code != prev:
            tail.append(code)
        prev = code if ch not in "AEIOUY" else ""
    return (first + "".join(tail) + "000")[:4]


def expand_surnames(surnames: list[str], use_soundex: bool) -> list[str]:
    """Dedupe a surname list case-insensitively. use_soundex is kept for API
    symmetry with scoring but does not collapse spellings, because target
    sites do substring search and "Furey" / "Fury" return different results.
    """
    _ = use_soundex
    seen: set[str] = set()
    out: list[str] = []
    for s in surnames:
        key = s.strip()
        if not key:
            continue
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(key)
    return out
