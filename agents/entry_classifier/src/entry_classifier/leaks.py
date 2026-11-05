"""Local checks on one entry. No API call, no network.

Hard hits mean `general` clearly identifies the client or carries an injection: the entry
is red without asking Claude. Soft hits are words or numbers from `specific` that reappear
in `general`: they are passed to Claude as hints, and the entry can no longer be green.

Only the kinds of hits are logged. The matched terms are client text and stay in memory.
"""

import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Bare domains are matched in lowercase only, so "at scale.No one" is not a domain.
URL = re.compile(
    r"(?i:\bhttps?://|\bwww\.)\S+|\b[a-z0-9-]+\.(?:com|org|net|io|fi|se|no|dk|de|eu|uk|co|ai|app)\b"
)
# International or 00-prefixed numbers only: plain digit runs are too often years or amounts.
PHONE = re.compile(r"(?<!\w)(?:\+|00)\d[\d\s().-]{6,}\d")
INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:instructions?|rules|prompt|guidelines)\b"
    r"|\bsystem prompt\b"
    r"|\byou are now\b"
    r"|\b(?:classify|mark|label|route)\b[^.\n]{0,30}\bas\s+(?:green|amber|red)\b"
    r"|</?\s*(?:system|instructions?|entry|assistant|user)\s*>",
    re.IGNORECASE,
)

# Parts of a workspace name too generic to identify anyone.
GENERIC_NAME_PARTS = {
    "client", "customer", "company", "group", "holding", "holdings", "global", "international",
    "labs", "systems", "solutions", "services", "consulting", "partners", "digital", "tech",
    "team", "project", "workspace", "test", "demo", "case",
}

# Capitalized words that are standard vocabulary, not names.
COMMON_CAPITALIZED = {
    "I", "AI", "ML", "API", "APIs", "B2B", "B2C", "CEO", "CFO", "CIO", "COO", "CTO", "CRM", "ERP",
    "HR", "IT", "KPI", "KPIs", "MVP", "OKR", "OKRs", "PM", "PMs", "PO", "QA", "ROI", "RFP", "SaaS",
    "SLA", "SLAs", "SOW", "UI", "UX", "Q1", "Q2", "Q3", "Q4", "Agile", "Scrum", "Kanban", "DevOps",
    "Excel", "SQL", "GDPR", "OK",
}

WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][\w&-]*(?:'s)?")
NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*")
SENTENCE_ENDS = set(".!?:;\n\"'([*-•")


@dataclass
class LocalCheck:
    hard: list[str] = field(default_factory=list)  # kinds: email, url, phone, workspace_name, injection
    soft: list[str] = field(default_factory=list)  # kinds: shared_name, shared_number
    hints: list[str] = field(default_factory=list)  # terms behind soft hits; never logged

    @property
    def floor(self) -> str:
        if self.hard:
            return "red"
        return "amber" if self.soft else "green"


def check_entry(entry: dict) -> LocalCheck:
    general, specific = entry["general"], entry["specific"]
    result = LocalCheck()

    for kind, pattern in (("email", EMAIL), ("url", URL), ("phone", PHONE), ("injection", INJECTION)):
        if pattern.search(general):
            result.hard.append(kind)
    if _workspace_parts_in(entry["workspace"], general):
        result.hard.append("workspace_name")

    names = sorted(n for n in _proper_nouns(specific) if _has_word(general, n))
    numbers = sorted(n for n in _numbers(specific) if _has_number(general, n))
    if names:
        result.soft.append("shared_name")
    if numbers:
        result.soft.append("shared_number")
    result.hints = names + numbers
    return result


def _workspace_parts_in(workspace: str, text: str) -> bool:
    parts = [p for p in re.split(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]+", workspace) if p]
    candidates = {p for p in parts if len(p) >= 4 and not p.isdigit() and p.lower() not in GENERIC_NAME_PARTS}
    return any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text, re.IGNORECASE) for p in candidates)


def _proper_nouns(text: str) -> set[str]:
    """Capitalized words that do not start a sentence, minus standard vocabulary."""
    found = set()
    for m in WORD.finditer(text):
        word = m.group().removesuffix("'s")
        if not any(c.isupper() for c in word) or len(word) < 2 or word in COMMON_CAPITALIZED:
            continue
        before = text[: m.start()].rstrip(" \t")
        if not before or before[-1] in SENTENCE_ENDS:
            continue
        found.add(word)
    return found


def _numbers(text: str) -> set[str]:
    return {n for n in NUMBER.findall(text) if sum(c.isdigit() for c in n) >= 2}


def _has_word(text: str, word: str) -> bool:
    # Case-sensitive: "Board" in a client note is not the same signal as "board" in a lesson.
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def _has_number(text: str, number: str) -> bool:
    return re.search(rf"(?<![\w.,]){re.escape(number)}(?![\d])", text) is not None
