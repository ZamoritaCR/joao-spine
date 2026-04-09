"""
Music adapter -- resolves playlists to streaming service links.
Supports Spotify (via spotipy), Apple Music (URL construction), and local payloads.
"""

import os
from typing import Optional

# Genre/mood -> seed track mapping for playlist generation
MOOD_SEEDS = {
    "chill": {"genres": ["chill", "ambient"], "energy": 0.3, "valence": 0.5},
    "happy": {"genres": ["pop", "happy"], "energy": 0.7, "valence": 0.9},
    "sad": {"genres": ["sad", "acoustic"], "energy": 0.2, "valence": 0.2},
    "angry": {"genres": ["metal", "punk"], "energy": 0.9, "valence": 0.3},
    "focused": {"genres": ["study", "classical"], "energy": 0.4, "valence": 0.5},
    "energetic": {"genres": ["dance", "electronic"], "energy": 0.9, "valence": 0.8},
    "romantic": {"genres": ["r-n-b", "soul"], "energy": 0.4, "valence": 0.7},
    "melancholy": {"genres": ["indie", "folk"], "energy": 0.3, "valence": 0.3},
    "pumped": {"genres": ["hip-hop", "workout"], "energy": 0.95, "valence": 0.7},
    "peaceful": {"genres": ["new-age", "ambient"], "energy": 0.15, "valence": 0.6},
}

# Transition map: current_feeling -> desired_feeling -> bridging genres
TRANSITION_MAP = {
    ("sad", "happy"): ["indie-pop", "soul", "pop"],
    ("angry", "chill"): ["post-rock", "ambient", "chill"],
    ("anxious", "peaceful"): ["ambient", "classical", "new-age"],
    ("tired", "energetic"): ["indie", "pop", "dance"],
    ("stressed", "focused"): ["lo-fi", "study", "classical"],
}


def _get_spotify():
    """Get authenticated Spotify client, or None."""
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        return spotipy.Spotify(auth_manager=auth)
    except Exception:
        return None


def resolve_mood_seeds(current: str, desired: str) -> dict:
    """Resolve mood transition to genre seeds and audio features."""
    current = current.lower().strip()
    desired = desired.lower().strip()

    # Check transition map for specific bridges
    key = (current, desired)
    if key in TRANSITION_MAP:
        return {
            "genres": TRANSITION_MAP[key],
            "energy": (MOOD_SEEDS.get(current, {}).get("energy", 0.5) +
                       MOOD_SEEDS.get(desired, {}).get("energy", 0.5)) / 2,
            "valence": MOOD_SEEDS.get(desired, {}).get("valence", 0.5),
        }

    # Fall back to desired mood seeds
    if desired in MOOD_SEEDS:
        return MOOD_SEEDS[desired]

    # Default
    return {"genres": ["pop"], "energy": 0.5, "valence": 0.5}


def search_spotify_tracks(
    query: str,
    seeds: dict,
    limit: int = 15,
) -> list[dict]:
    """Search Spotify for tracks matching mood seeds.

    Returns list of {name, artist, album, spotify_url, preview_url, duration_ms}.
    """
    sp = _get_spotify()
    if not sp:
        return []

    try:
        # Try recommendations API first
        genres = seeds.get("genres", ["pop"])[:5]
        results = sp.recommendations(
            seed_genres=genres,
            target_energy=seeds.get("energy", 0.5),
            target_valence=seeds.get("valence", 0.5),
            limit=limit,
        )
        tracks = []
        for t in results.get("tracks", []):
            tracks.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album": t["album"]["name"],
                "spotify_url": t["external_urls"].get("spotify", ""),
                "preview_url": t.get("preview_url", ""),
                "duration_ms": t["duration_ms"],
            })
        return tracks
    except Exception:
        # Fall back to search
        try:
            genre_str = " ".join(seeds.get("genres", ["pop"]))
            results = sp.search(q=f"genre:{genre_str}", type="track", limit=limit)
            tracks = []
            for t in results["tracks"]["items"]:
                tracks.append({
                    "name": t["name"],
                    "artist": ", ".join(a["name"] for a in t["artists"]),
                    "album": t["album"]["name"],
                    "spotify_url": t["external_urls"].get("spotify", ""),
                    "preview_url": t.get("preview_url", ""),
                    "duration_ms": t["duration_ms"],
                })
            return tracks
        except Exception:
            return []


def build_apple_music_url(query: str) -> str:
    """Build an Apple Music search deep link."""
    from urllib.parse import quote
    return f"https://music.apple.com/search?term={quote(query)}"


def build_local_payload(tracks: list[dict]) -> dict:
    """Build a local play command payload for Home Assistant / Telegram."""
    return {
        "action": "play_playlist",
        "tracks": [
            {"name": t["name"], "artist": t["artist"]}
            for t in tracks
        ],
        "count": len(tracks),
    }
