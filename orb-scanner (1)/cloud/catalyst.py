"""
Catalyst detection — identical logic to the backend version, just
self-contained (no import from the FastAPI app package) so the cloud path
has zero dependency on backend/.
"""
import re
from dataclasses import dataclass


@dataclass
class NewsItem:
    headline: str
    url: str = ""
    published_at: str = ""
    source: str = ""


CATALYST_PATTERNS: dict[str, list[str]] = {
    "FDA": [r"\bfda\b", r"food and drug administration", r"\bapproval\b", r"\bclearance\b"],
    "Clinical Trial": [r"clinical trial", r"phase [123i]{1,3}\b", r"\btrial results\b", r"\btopline\b"],
    "Earnings": [r"\bearnings\b", r"quarterly results", r"\bq[1-4] results\b", r"\brevenue\b.*\bbeat\b"],
    "Acquisition": [r"\bacqui(re|sition|red)\b", r"\bbuyout\b", r"to be acquired"],
    "Merger": [r"\bmerger\b", r"\bmerge[sd]?\b", r"business combination"],
    "Government Contract": [r"government contract", r"\bdod\b", r"department of defense", r"federal contract"],
    "Partnership": [r"\bpartnership\b", r"\bcollaborat", r"strategic alliance", r"joint venture"],
    "AI": [r"\bartificial intelligence\b", r"\bai\b(?!r)", r"machine learning", r"\bllm\b"],
    "Biotech": [r"\bbiotech", r"\bpharmaceutical", r"\bdrug candidate\b", r"\btherapeutic\b"],
    "Patent": [r"\bpatent\b", r"intellectual property"],
    "Press Release": [r"press release", r"announces"],
}

_COMPILED = {tag: [re.compile(p, re.IGNORECASE) for p in patterns] for tag, patterns in CATALYST_PATTERNS.items()}


def detect_catalysts(news: list[NewsItem]) -> tuple[list[str], NewsItem | None]:
    if not news:
        return [], None

    matched: set[str] = set()
    best_item: NewsItem | None = None

    for item in news:
        text = item.headline or ""
        hit = False
        for tag, patterns in _COMPILED.items():
            if any(p.search(text) for p in patterns):
                matched.add(tag)
                hit = True
        if hit and best_item is None:
            best_item = item

    if best_item is None and news:
        best_item = news[0]

    ordered = [tag for tag in CATALYST_PATTERNS if tag in matched]
    return ordered, best_item


def news_quality_score(tags: list[str]) -> float:
    if not tags:
        return 0.0

    weight_by_tag = {
        "FDA": 100, "Acquisition": 95, "Merger": 90, "Clinical Trial": 85,
        "Government Contract": 80, "Earnings": 70, "Patent": 60, "AI": 55,
        "Biotech": 50, "Partnership": 45, "Press Release": 25,
    }
    return max(weight_by_tag.get(tag, 30) for tag in tags)
