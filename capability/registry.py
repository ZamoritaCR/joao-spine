"""
Capability registry -- intent classification and routing.

Maps user intents to capabilities, resolves which agent handles what,
and dispatches execution.
"""

import re
from typing import Optional


# Capability definitions
CAPABILITIES = {
    "tableau_to_powerbi": {
        "name": "Tableau to Power BI Migration",
        "description": "Parse Tableau workbooks and produce Power BI migration artifacts",
        "keywords": [
            "tableau", "twb", "twbx", "power bi", "powerbi", "pbi", "migrate",
            "migration", "convert", "dashboard", "dax", "pbix", "pbip",
        ],
        "file_extensions": [".twb", ".twbx"],
        "default_agent": "BYTE",
        "module": "capability.tableau_to_powerbi",
    },
    "mood_playlist": {
        "name": "MrDP Mood Playlist",
        "description": "Generate mood-transition playlists with streaming links",
        "keywords": [
            "playlist", "music", "mood", "song", "play", "listen", "vibe",
            "chill", "pump", "sad", "happy", "focus", "energetic", "relax",
            "spotify", "apple music", "mrdp", "play some",
        ],
        "file_extensions": [],
        "default_agent": "ARIA",
        "module": "capability.mood_playlist",
    },
    "general": {
        "name": "General Agent Task",
        "description": "Route to an agent brain for general-purpose processing",
        "keywords": [],
        "file_extensions": [],
        "default_agent": "CJ",
        "module": None,
    },
}


def classify_intent(text: str, filename: Optional[str] = None) -> str:
    """Classify user intent into a capability name.

    Uses keyword matching + file extension detection.
    Returns capability key string.
    """
    text_lower = text.lower().strip()

    # Check file extension first (strongest signal)
    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        for cap_key, cap in CAPABILITIES.items():
            if ext in cap.get("file_extensions", []):
                return cap_key

    # Keyword scoring
    scores = {}
    for cap_key, cap in CAPABILITIES.items():
        if cap_key == "general":
            continue
        score = 0
        for kw in cap.get("keywords", []):
            if kw in text_lower:
                # Longer keyword matches are more specific
                score += len(kw)
        if score > 0:
            scores[cap_key] = score

    if scores:
        return max(scores, key=scores.get)

    return "general"


def get_capability(name: str) -> Optional[dict]:
    """Get capability definition by name."""
    return CAPABILITIES.get(name)


def get_default_agent(capability: str) -> str:
    """Get the default agent for a capability."""
    cap = CAPABILITIES.get(capability, {})
    return cap.get("default_agent", "CJ")


def list_capabilities() -> list[dict]:
    """List all registered capabilities."""
    return [
        {"key": k, "name": v["name"], "description": v["description"]}
        for k, v in CAPABILITIES.items()
    ]


def route(text: str, filename: Optional[str] = None) -> dict:
    """Classify intent and return routing decision.

    Returns dict with: capability, agent, confidence, description
    """
    cap_key = classify_intent(text, filename)
    cap = CAPABILITIES[cap_key]

    # Rough confidence based on how many keywords matched
    text_lower = text.lower()
    matched = sum(1 for kw in cap.get("keywords", []) if kw in text_lower)
    total = len(cap.get("keywords", [])) or 1
    confidence = min(matched / total * 3, 1.0)  # Scale up, cap at 1.0

    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in cap.get("file_extensions", []):
            confidence = max(confidence, 0.95)

    if cap_key == "general":
        confidence = 0.3

    return {
        "capability": cap_key,
        "agent": cap.get("default_agent", "CJ"),
        "confidence": round(confidence, 2),
        "description": cap["description"],
    }
