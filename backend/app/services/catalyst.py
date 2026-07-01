"""
Catalyst detection.

Scans recent news headlines for a ticker and tags which catalyst categories
are present. This is intentionally a fast, explainable keyword classifier —
good enough to separate "has a real headline reason to move" from "pure
technical/float squeeze" and to feed the news-quality score component.

Swap in an LLM-based classifier later behind the same `detect_catalysts`
signature if keyword matching proves too noisy on real headlines.
"""
import re

from app.providers.base import NewsItem

# Ordered roughly by how strong a same-day catalyst each category tends to be;
# used as a tiebreaker for news-quality scoring, not for filtering.
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

_COMPILED = {
    tag: [re.compile(p, re.IGNORECASE) for p in patterns] for tag, patterns in CATALYST_PATTERNS.items()
}


def detect_catalysts(news: list[NewsItem]) -> tuple[list[str], NewsItem | None]:
    """Return (matched catalyst tags, most relevant headline) for a ticker's
    recent news. If nothing matches, tags is empty and the ticker should be
    marked as having no catalyst."""
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

    # If nothing matched a specific category but there IS recent news,
    # surface the freshest headline anyway — a trader may still want to see
    # it even though it didn't hit a known catalyst keyword.
    if best_item is None and news:
        best_item = news[0]

    # Preserve a stable, human-friendly order
    ordered = [tag for tag in CATALYST_PATTERNS if tag in matched]
    return ordered, best_item


def news_quality_score(tags: list[str]) -> float:
    """0-100 sub-score used as an input to the overall scoring engine.
    Stronger, more binary catalysts (FDA, Acquisition, Merger) score higher
    than soft ones (Press Release, Partnership)."""
    if not tags:
        return 0.0

    weight_by_tag = {
        "FDA": 100,
        "Acquisition": 95,
        "Merger": 90,
        "Clinical Trial": 85,
        "Government Contract": 80,
        "Earnings": 70,
        "Patent": 60,
        "AI": 55,
        "Biotech": 50,
        "Partnership": 45,
        "Press Release": 25,
    }
    return max(weight_by_tag.get(tag, 30) for tag in tags)
