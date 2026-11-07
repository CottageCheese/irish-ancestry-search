"""Core dataclasses + confidence_tier bucket. No I/O, no Playwright."""
from dataclasses import dataclass, field

from .config import RUNTIME_FLAGS
from .nicknames import expand_forename
from .soundex import expand_surnames


@dataclass
class Person:
    """One row from ancestors.csv."""

    surname: str
    forename: str
    surname_variants: list[str] = field(default_factory=list)
    birth_year_approx: int | None = None
    year_range: int = 5
    county: str = ""
    townland: str = ""
    father: str = ""
    mother: str = ""
    spouse: str = ""
    notes: str = ""

    def all_surnames(self) -> list[str]:
        names = [self.surname] + list(self.surname_variants)
        return expand_surnames(names, use_soundex=RUNTIME_FLAGS.get("soundex", True))

    def all_forenames(self, use_nicknames: bool) -> list[str]:
        if not use_nicknames:
            return [self.forename] if self.forename else [""]
        variants = expand_forename(self.forename)
        return variants or [""]

    def year_window(self) -> tuple[int, int] | None:
        if self.birth_year_approx is None:
            return None
        return (
            self.birth_year_approx - self.year_range,
            self.birth_year_approx + self.year_range,
        )

    def label(self) -> str:
        return f"{self.forename} {self.surname}".strip() or "(unknown)"


@dataclass
class Record:
    """One row in results.csv."""

    person_searched: str
    site: str
    record_type: str
    name_found: str
    age_or_year: str
    place: str
    other_details: str
    link: str
    score: int = 0
    kin_hits: str = ""
    is_new: bool = False
    thumbnail: str = ""
    user_notes: str = ""
    status: str = ""  # "" | "confirmed" | "rejected" — set by user via web UI


def confidence_tier(score: int) -> str:
    """Bucket a numeric score into a human label used for colouring + sorting."""
    if score >= 10:
        return "Likely"
    if score >= 5:
        return "Possible"
    if score >= 1:
        return "Weak"
    return "Manual"
