"""Entry format from entry-schema.md, plus the field the classifier adds."""

REQUIRED_FIELDS = {
    "entry_id": str,
    "workspace": str,
    "sources": list,
    "specific": str,
    "general": str,
}

# Fields written by pipeline agents. Stripped before an entry is compared or stored again.
PIPELINE_FIELDS = ("route", "k", "matched_ids", "cluster_id")

COLORS = ("green", "amber", "red")
SEVERITY = {color: rank for rank, color in enumerate(COLORS)}


def most_severe(*colors: str) -> str:
    return max(colors, key=SEVERITY.__getitem__)


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
    """Copy of the entry without fields added by pipeline agents."""
    return {key: value for key, value in entry.items() if key not in PIPELINE_FIELDS}
