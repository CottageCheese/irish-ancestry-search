"""Shared runtime flags. Mutable module-level dict so CLI can toggle features
without threading a config object through every call site. Imported by name
so the same dict instance is visible everywhere.
"""
RUNTIME_FLAGS: dict[str, bool] = {
    "soundex": True,
    "collapse_weak": True,
    "fetch_household": False,
}
