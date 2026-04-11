"""
Capability registry -- intent classification and routing.

Maps user intents to capabilities, resolves which agent handles what,
and dispatches execution. All 10 capabilities from JOAO OS spec v2.
"""

from typing import Optional


# Capability definitions -- all 10 from the spec
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
        "min_autonomy": "L2",
        "lock_type": None,
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
        "min_autonomy": "L1",
        "lock_type": None,
        "module": "capability.mood_playlist",
    },
    "git_scan": {
        "name": "Git Scan",
        "description": "Scan repos: status, diff, log, recent commits, uncommitted changes",
        "keywords": [
            "git", "status", "diff", "log", "scan", "what changed",
            "repo", "recent commit", "show diff", "branches",
        ],
        "file_extensions": [],
        "default_agent": "CJ",
        "min_autonomy": "L0",
        "lock_type": None,
        "module": "capability.git_adapter",
    },
    "git_write": {
        "name": "Git Write",
        "description": "Create branches, commit changes, draft PRs (requires WRITE_LOCK)",
        "keywords": [
            "create branch", "commit", "branch off", "pr draft",
            "git commit", "git branch",
        ],
        "file_extensions": [],
        "default_agent": "BYTE",
        "min_autonomy": "L3",
        "lock_type": "WRITE_LOCK",
        "module": "capability.git_adapter",
    },
    "git_ship": {
        "name": "Git Ship",
        "description": "Push, deploy, restart services (requires SHIP_LOCK)",
        "keywords": [
            "push", "deploy", "ship", "go live", "merge main",
            "git push", "restart service",
        ],
        "file_extensions": [],
        "default_agent": "BYTE",
        "min_autonomy": "L4",
        "lock_type": "SHIP_LOCK",
        "module": "capability.git_adapter",
    },
    "context_build": {
        "name": "Context Pack Builder",
        "description": "Assemble structured context pack for any project/task",
        "keywords": [
            "context pack", "build context", "what do you know about",
            "brief me", "catch up", "project context",
        ],
        "file_extensions": [],
        "default_agent": "SOFIA",
        "min_autonomy": "L1",
        "lock_type": None,
        "module": "capability.context_builder",
    },
    "ollama_generate": {
        "name": "Ollama Generate",
        "description": "Local LLM completion via Ollama (free, WU-safe)",
        "keywords": [
            "ollama", "local llm", "offline generate", "think about",
            "ask ollama", "phi4", "deepseek", "llama",
        ],
        "file_extensions": [],
        "default_agent": "CJ",
        "min_autonomy": "L1",
        "lock_type": None,
        "module": None,
    },
    "tunnel_status": {
        "name": "Cloudflared Tunnel Status",
        "description": "Check cloudflared tunnel health, hostnames, connections",
        "keywords": [
            "tunnel", "cloudflare", "cloudflared", "tunnel status",
            "is tunnel up", "tunnel health",
        ],
        "file_extensions": [],
        "default_agent": "BYTE",
        "min_autonomy": "L0",
        "lock_type": None,
        "module": "capability.tunnel_adapter",
    },
    "file_ingest": {
        "name": "File Ingest",
        "description": "Upload any file, extract metadata, route to processor",
        "keywords": [
            "upload", "ingest", "accept file", "process file",
        ],
        "file_extensions": [],
        "default_agent": "BYTE",
        "min_autonomy": "L1",
        "lock_type": None,
        "module": None,
    },
    "general": {
        "name": "General Agent Task",
        "description": "Route to an agent brain for general-purpose processing",
        "keywords": [],
        "file_extensions": [],
        "default_agent": "CJ",
        "min_autonomy": "L1",
        "lock_type": None,
        "module": None,
    },
}


def classify_intent(text: str, filename: Optional[str] = None) -> str:
    """Classify user intent into a capability name.

    Priority stack (per spec Section 3.4):
    1. File extension match (strongest signal)
    2. Explicit capability name in text
    3. Keyword scoring (longer keywords = more specific)
    4. Fallback to general
    """
    text_lower = text.lower().strip()

    # 1. File extension match
    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        for cap_key, cap in CAPABILITIES.items():
            if ext in cap.get("file_extensions", []):
                return cap_key

    # 2. Explicit capability name
    for cap_key in CAPABILITIES:
        if cap_key != "general" and cap_key in text_lower:
            return cap_key

    # 3. Keyword scoring
    scores = {}
    for cap_key, cap in CAPABILITIES.items():
        if cap_key == "general":
            continue
        score = 0
        for kw in cap.get("keywords", []):
            if kw in text_lower:
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
        {
            "key": k,
            "name": v["name"],
            "description": v["description"],
            "min_autonomy": v.get("min_autonomy", "L1"),
            "lock_type": v.get("lock_type"),
            "default_agent": v.get("default_agent", "CJ"),
        }
        for k, v in CAPABILITIES.items()
    ]


def route(text: str, filename: Optional[str] = None) -> dict:
    """Classify intent and return routing decision."""
    cap_key = classify_intent(text, filename)
    cap = CAPABILITIES[cap_key]

    # Confidence scoring
    text_lower = text.lower()
    matched = sum(1 for kw in cap.get("keywords", []) if kw in text_lower)
    total = len(cap.get("keywords", [])) or 1
    confidence = min(matched / total * 3, 1.0)

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
        "min_autonomy": cap.get("min_autonomy", "L1"),
        "lock_type": cap.get("lock_type"),
    }
