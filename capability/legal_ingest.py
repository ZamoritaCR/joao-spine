"""
Legal-by-design Ingestion -- JOAO OS compliant content ingestion.

Rules:
1. Allowlist domains only (no arbitrary crawling)
2. Rate limits per domain (respectful scraping)
3. robots.txt / ToS awareness (obey disallow rules)
4. No paywall / login bypass (refuse gated content)
5. YouTube: captions/metadata only when available (no download)
6. WU data classification (flag internal data, block external API forwarding)
"""

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# DOMAIN ALLOWLIST
# ──────────────────────────────────────────────

ALLOWED_DOMAINS = {
    # Public knowledge / open-access
    "en.wikipedia.org",
    "arxiv.org",
    "github.com",
    "raw.githubusercontent.com",
    "docs.python.org",
    "docs.anthropic.com",
    "platform.openai.com",
    "huggingface.co",
    "pypi.org",
    "stackoverflow.com",
    "developer.mozilla.org",
    "fastapi.tiangolo.com",
    "peps.python.org",
    "learn.microsoft.com",
    "cloud.google.com",

    # JOAO ecosystem
    "joao.theartofthepossible.io",
    "theartofthepossible.io",

    # YouTube (metadata/captions only)
    "www.youtube.com",
    "youtube.com",
    "youtu.be",

    # News / research (public)
    "news.ycombinator.com",
    "techcrunch.com",
    "arstechnica.com",
    "bbc.com",
    "bbc.co.uk",
    "reuters.com",

    # Railway / deployment
    "docs.railway.app",
    "railway.app",

    # Supabase docs
    "supabase.com",
}

# Domains that require special handling
YOUTUBE_DOMAINS = {"www.youtube.com", "youtube.com", "youtu.be"}
PAYWALL_INDICATORS = [
    "subscribe to continue",
    "sign in to read",
    "create an account",
    "paywall",
    "premium content",
    "member-only",
    "exclusive content",
    "log in to access",
]

# ──────────────────────────────────────────────
# RATE LIMITING
# ──────────────────────────────────────────────

# Per-domain rate: {domain: (max_requests_per_minute, last_request_times[])}
_rate_limits: dict[str, list[float]] = {}
_DEFAULT_RATE_LIMIT = 10  # requests per minute per domain
_STRICT_RATE_DOMAINS = {
    "github.com": 30,
    "api.github.com": 30,
    "stackoverflow.com": 5,
    "youtube.com": 5,
    "www.youtube.com": 5,
}


def _check_rate_limit(domain: str) -> bool:
    """Check if we're within rate limit for this domain. Returns True if OK."""
    now = time.time()
    limit = _STRICT_RATE_DOMAINS.get(domain, _DEFAULT_RATE_LIMIT)

    if domain not in _rate_limits:
        _rate_limits[domain] = []

    # Clean old entries (older than 60s)
    _rate_limits[domain] = [t for t in _rate_limits[domain] if now - t < 60]

    if len(_rate_limits[domain]) >= limit:
        return False

    _rate_limits[domain].append(now)
    return True


# ──────────────────────────────────────────────
# ROBOTS.TXT
# ──────────────────────────────────────────────

_robots_cache: dict[str, dict] = {}  # domain -> {fetched_at, disallowed_paths}


def _check_robots_txt(url: str) -> dict:
    """Check robots.txt for the given URL.

    Returns: {allowed: bool, reason: str}
    """
    parsed = urlparse(url)
    domain = parsed.netloc
    path = parsed.path

    # Check cache
    if domain in _robots_cache:
        cached = _robots_cache[domain]
        # Cache for 1 hour
        if time.time() - cached.get("fetched_at", 0) < 3600:
            for disallowed in cached.get("disallowed_paths", []):
                if path.startswith(disallowed):
                    return {"allowed": False, "reason": f"robots.txt disallows {disallowed}"}
            return {"allowed": True, "reason": "robots.txt permits access"}

    # Fetch robots.txt
    try:
        import httpx
        robots_url = f"{parsed.scheme}://{domain}/robots.txt"
        resp = httpx.get(robots_url, timeout=5, follow_redirects=True)
        if resp.status_code == 200:
            disallowed = []
            in_user_agent_all = False
            for line in resp.text.split("\n"):
                line = line.strip()
                if line.lower().startswith("user-agent:"):
                    agent = line.split(":", 1)[1].strip()
                    in_user_agent_all = agent == "*"
                elif in_user_agent_all and line.lower().startswith("disallow:"):
                    path_rule = line.split(":", 1)[1].strip()
                    if path_rule:
                        disallowed.append(path_rule)

            _robots_cache[domain] = {
                "fetched_at": time.time(),
                "disallowed_paths": disallowed,
            }

            for d in disallowed:
                if path.startswith(d):
                    return {"allowed": False, "reason": f"robots.txt disallows {d}"}
            return {"allowed": True, "reason": "robots.txt permits access"}
        else:
            # No robots.txt = everything allowed
            _robots_cache[domain] = {"fetched_at": time.time(), "disallowed_paths": []}
            return {"allowed": True, "reason": "No robots.txt found (all permitted)"}
    except Exception as e:
        logger.debug("robots.txt fetch failed for %s: %s", domain, e)
        return {"allowed": True, "reason": f"robots.txt unreachable: {e}"}


# ──────────────────────────────────────────────
# WU DATA CLASSIFICATION
# ──────────────────────────────────────────────

_WU_INDICATORS = [
    "western union",
    "wu.com",
    "westernunion.com",
    "money transfer",
    "wupos",
    "wu-internal",
    "agent portal",
    "compliance report",
]


def classify_wu_data(text: str) -> dict:
    """Classify whether text contains WU-internal data.

    Returns: {is_wu: bool, confidence: float, indicators_found: list}
    """
    text_lower = text.lower()
    found = [ind for ind in _WU_INDICATORS if ind in text_lower]
    confidence = min(len(found) / 3.0, 1.0)  # 3+ indicators = high confidence

    return {
        "is_wu": len(found) >= 2,
        "confidence": round(confidence, 2),
        "indicators_found": found,
    }


# ──────────────────────────────────────────────
# MAIN INGESTION GATE
# ──────────────────────────────────────────────

def validate_url(url: str) -> dict:
    """Validate a URL for legal ingestion.

    Returns: {allowed: bool, reason: str, domain: str, is_youtube: bool, checks: dict}
    """
    checks = {}

    # Parse URL
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            return {"allowed": False, "reason": "Invalid URL: no domain", "domain": "", "checks": {}}
    except Exception:
        return {"allowed": False, "reason": "Invalid URL format", "domain": "", "checks": {}}

    # 1. Domain allowlist
    domain_clean = domain.replace("www.", "") if domain.startswith("www.") else domain
    in_allowlist = domain in ALLOWED_DOMAINS or domain_clean in ALLOWED_DOMAINS
    checks["domain_allowlist"] = {"passed": in_allowlist, "domain": domain}
    if not in_allowlist:
        return {
            "allowed": False,
            "reason": f"Domain '{domain}' not in allowlist. Add to ALLOWED_DOMAINS if this is a legitimate public source.",
            "domain": domain,
            "is_youtube": False,
            "checks": checks,
        }

    # 2. Rate limit
    rate_ok = _check_rate_limit(domain)
    checks["rate_limit"] = {"passed": rate_ok, "domain": domain}
    if not rate_ok:
        return {
            "allowed": False,
            "reason": f"Rate limit exceeded for '{domain}'. Wait before retrying.",
            "domain": domain,
            "is_youtube": domain in YOUTUBE_DOMAINS,
            "checks": checks,
        }

    # 3. robots.txt
    robots = _check_robots_txt(url)
    checks["robots_txt"] = robots
    if not robots["allowed"]:
        return {
            "allowed": False,
            "reason": f"Blocked by robots.txt: {robots['reason']}",
            "domain": domain,
            "is_youtube": domain in YOUTUBE_DOMAINS,
            "checks": checks,
        }

    # 4. YouTube special handling
    is_youtube = domain in YOUTUBE_DOMAINS
    if is_youtube:
        checks["youtube"] = {
            "mode": "captions_and_metadata_only",
            "note": "Full video download is not permitted. Captions and metadata only.",
        }

    return {
        "allowed": True,
        "reason": "All checks passed",
        "domain": domain,
        "is_youtube": is_youtube,
        "checks": checks,
    }


def check_paywall(content: str) -> dict:
    """Check if fetched content appears to be behind a paywall.

    Returns: {is_paywalled: bool, indicators_found: list}
    """
    content_lower = content.lower()
    found = [ind for ind in PAYWALL_INDICATORS if ind in content_lower]
    return {
        "is_paywalled": len(found) >= 1,
        "indicators_found": found,
    }


def validate_file_upload(filename: str, content: bytes, max_size_mb: int = 100) -> dict:
    """Validate a file upload for legal ingestion.

    Returns: {allowed: bool, reason: str, wu_check: dict, size_mb: float}
    """
    size_mb = len(content) / (1024 * 1024)

    # Size limit
    if size_mb > max_size_mb:
        return {
            "allowed": False,
            "reason": f"File too large: {size_mb:.1f}MB > {max_size_mb}MB limit",
            "size_mb": round(size_mb, 2),
        }

    # WU data check on text-like files
    wu_check = {"is_wu": False, "confidence": 0.0, "indicators_found": []}
    text_extensions = {".txt", ".md", ".csv", ".json", ".xml", ".twb", ".twbx", ".py", ".js"}
    ext = Path(filename).suffix.lower()
    if ext in text_extensions:
        try:
            sample = content[:10000].decode("utf-8", errors="ignore")
            wu_check = classify_wu_data(sample)
        except Exception:
            pass

    return {
        "allowed": True,
        "reason": "File upload permitted",
        "size_mb": round(size_mb, 2),
        "wu_check": wu_check,
    }


def get_ingestion_policy() -> dict:
    """Return the current ingestion policy for transparency."""
    return {
        "allowed_domains": sorted(ALLOWED_DOMAINS),
        "allowed_domain_count": len(ALLOWED_DOMAINS),
        "rate_limits": {
            "default_per_minute": _DEFAULT_RATE_LIMIT,
            "strict_domains": _STRICT_RATE_DOMAINS,
        },
        "youtube_policy": "Captions and metadata only. No full video download.",
        "paywall_policy": "Content behind paywalls is rejected. No login bypass.",
        "wu_policy": "WU-internal data is flagged and blocked from external APIs.",
        "robots_policy": "robots.txt is respected. Disallowed paths are not accessed.",
    }
