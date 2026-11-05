"""Entry format from entry-schema.md, plus the fields the counter adds."""

REQUIRED_FIELDS = {
    "entry_id": str,
    "workspace": str,
    "sources": list,
    "specific": str,
    "general": str,
}

# Fields written by the counter. Stripped when an entry moves between files.
QUARANTINE_FIELDS = ("k", "matched_ids")
ACCEPTED_FIELDS = ("route", "k", "cluster_id")


def validate_entry(entry: object) -> dict:
    """Return the entry if it matches the schema, raise ValueError otherwise.

    Error messages name fields only, never their content.
    """
    if not isinstance(entry, dict):
        raise ValueError("entry must be a JSON object")
    for field, kind in REQUIRED_FIELDS.items():
        if field not in entry:
            raise ValueError(f"missing field: {field}")
        if not isinstance(entry[field], kind):
            raise ValueError(f"wrong type for field: {field}")
    if not all(isinstance(s, str) for s in entry["sources"]):
        raise ValueError("wrong type for field: sources")
    if not entry["workspace"].strip():
        raise ValueError("empty field: workspace")
    if not entry["general"].strip():
        raise ValueError("empty field: general")
    if not entry["entry_id"].startswith(entry["workspace"] + "#"):
        raise ValueError("entry_id must start with '<workspace>#'")
    return entry


def base_entry(entry: dict) -> dict:
    """Copy of the entry without the counter's own fields."""
    extra = set(QUARANTINE_FIELDS) | set(ACCEPTED_FIELDS)
    return {key: value for key, value in entry.items() if key not in extra}
