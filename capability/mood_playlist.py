"""
MrDP Mood Playlist capability -- REAL output.

Takes current feeling + desired feeling + constraints.
Produces a playlist with streaming links and warm, validating rationale.
MrDP tone: warm, real, non-judgmental. Not a therapist. A friend who
knows music and knows you.
"""

import json
from typing import Optional
from capability.music import (
    resolve_mood_seeds,
    search_spotify_tracks,
    build_apple_music_url,
    build_local_payload,
    MOOD_SEEDS,
)
from capability.artifact_store import save_artifact


# MrDP voice templates -- warm, direct, validating
RATIONALE_TEMPLATES = {
    ("sad", "happy"): (
        "You're feeling low, and that's okay -- you don't need to perform happiness. "
        "This playlist starts where you are and gently opens the door. By track 5 or 6, "
        "you might notice your foot tapping. No pressure."
    ),
    ("angry", "chill"): (
        "Rage is energy. We're not stuffing it down -- we're channeling it. "
        "The first few tracks let you feel the burn, then we ease into something "
        "that lets your shoulders drop. You earned the calm."
    ),
    ("anxious", "peaceful"): (
        "Your mind's running laps. These tracks are wide open spaces -- "
        "ambient textures that give your brain permission to stop solving things "
        "for a minute. Headphones recommended."
    ),
    ("tired", "energetic"): (
        "Running on fumes? We start slow and warm, then the tempo picks up. "
        "Not an assault on your senses -- more like a sunrise that becomes noon. "
        "Give it three tracks."
    ),
    ("stressed", "focused"): (
        "When everything's loud, you need a cocoon. Lo-fi and minimal textures "
        "that create a pocket of space. Not background noise -- a clean room "
        "for your mind to work in."
    ),
}

DEFAULT_RATIONALE = (
    "Music finds us where we are and takes us where we want to go. "
    "This playlist is built for your transition -- let it breathe, "
    "and give yourself permission to feel the shift."
)


def _get_rationale(current: str, desired: str) -> str:
    """Get MrDP-voice rationale for the mood transition."""
    key = (current.lower().strip(), desired.lower().strip())
    return RATIONALE_TEMPLATES.get(key, DEFAULT_RATIONALE)


def _build_curated_playlist(current: str, desired: str, constraints: dict) -> list[dict]:
    """Build a curated playlist from internal knowledge when Spotify is unavailable.

    Returns a hand-picked track list based on mood transition.
    """
    genre_pref = constraints.get("genre", "").lower()
    language = constraints.get("language", "any").lower()
    max_tracks = constraints.get("max_tracks", 15)

    # Curated playlists by mood transition
    playlists = {
        ("sad", "happy"): [
            {"name": "Skinny Love", "artist": "Bon Iver", "genre": "indie-folk"},
            {"name": "Holocene", "artist": "Bon Iver", "genre": "indie-folk"},
            {"name": "Dog Days Are Over", "artist": "Florence + The Machine", "genre": "indie-pop"},
            {"name": "Home", "artist": "Edward Sharpe & The Magnetic Zeros", "genre": "indie-folk"},
            {"name": "Mr. Blue Sky", "artist": "Electric Light Orchestra", "genre": "pop-rock"},
            {"name": "Here Comes the Sun", "artist": "The Beatles", "genre": "pop-rock"},
            {"name": "Walking on Sunshine", "artist": "Katrina and the Waves", "genre": "pop"},
            {"name": "Don't Stop Me Now", "artist": "Queen", "genre": "rock"},
            {"name": "Happy", "artist": "Pharrell Williams", "genre": "pop"},
            {"name": "Good as Hell", "artist": "Lizzo", "genre": "pop"},
        ],
        ("angry", "chill"): [
            {"name": "Break Stuff", "artist": "Limp Bizkit", "genre": "nu-metal"},
            {"name": "Killing in the Name", "artist": "Rage Against the Machine", "genre": "metal"},
            {"name": "Smells Like Teen Spirit", "artist": "Nirvana", "genre": "grunge"},
            {"name": "Everlong", "artist": "Foo Fighters", "genre": "alt-rock"},
            {"name": "Under the Bridge", "artist": "Red Hot Chili Peppers", "genre": "alt-rock"},
            {"name": "Teardrop", "artist": "Massive Attack", "genre": "trip-hop"},
            {"name": "Breathe Me", "artist": "Sia", "genre": "indie-pop"},
            {"name": "Saturn", "artist": "Sleeping at Last", "genre": "ambient"},
            {"name": "Weightless", "artist": "Marconi Union", "genre": "ambient"},
            {"name": "Clair de Lune", "artist": "Debussy", "genre": "classical"},
        ],
        ("anxious", "peaceful"): [
            {"name": "Weightless", "artist": "Marconi Union", "genre": "ambient"},
            {"name": "Electra", "artist": "Airstream", "genre": "ambient"},
            {"name": "Strawberry Swing", "artist": "Coldplay", "genre": "alt-rock"},
            {"name": "Gymnop\u00e9die No. 1", "artist": "Erik Satie", "genre": "classical"},
            {"name": "An Ending (Ascent)", "artist": "Brian Eno", "genre": "ambient"},
            {"name": "Experience", "artist": "Ludovico Einaudi", "genre": "classical"},
            {"name": "Nuvole Bianche", "artist": "Ludovico Einaudi", "genre": "classical"},
            {"name": "River Flows in You", "artist": "Yiruma", "genre": "classical"},
            {"name": "Bloom", "artist": "The Paper Kites", "genre": "indie-folk"},
            {"name": "Sunset Lover", "artist": "Petit Biscuit", "genre": "electronic"},
        ],
        ("tired", "energetic"): [
            {"name": "Intro", "artist": "The xx", "genre": "indie"},
            {"name": "Gooey", "artist": "Glass Animals", "genre": "indie-pop"},
            {"name": "Electric Feel", "artist": "MGMT", "genre": "indie-pop"},
            {"name": "Take Me Out", "artist": "Franz Ferdinand", "genre": "indie-rock"},
            {"name": "Galvanize", "artist": "The Chemical Brothers", "genre": "electronic"},
            {"name": "Around the World", "artist": "Daft Punk", "genre": "electronic"},
            {"name": "Levels", "artist": "Avicii", "genre": "dance"},
            {"name": "Can't Hold Us", "artist": "Macklemore & Ryan Lewis", "genre": "hip-hop"},
            {"name": "Lose Yourself", "artist": "Eminem", "genre": "hip-hop"},
            {"name": "Eye of the Tiger", "artist": "Survivor", "genre": "rock"},
        ],
        ("stressed", "focused"): [
            {"name": "Intro", "artist": "The xx", "genre": "indie"},
            {"name": "Re: Stacks", "artist": "Bon Iver", "genre": "indie-folk"},
            {"name": "To Build a Home", "artist": "The Cinematic Orchestra", "genre": "ambient"},
            {"name": "Divenire", "artist": "Ludovico Einaudi", "genre": "classical"},
            {"name": "Comptine d'un autre ete", "artist": "Yann Tiersen", "genre": "classical"},
            {"name": "Oblivion", "artist": "M83", "genre": "electronic"},
            {"name": "Midnight City (slowed)", "artist": "M83", "genre": "electronic"},
            {"name": "Daylight", "artist": "Joji", "genre": "r-n-b"},
            {"name": "Flume", "artist": "Bon Iver", "genre": "indie-folk"},
            {"name": "Opus", "artist": "Eric Prydz", "genre": "electronic"},
        ],
    }

    # Default playlist for any other mood combination
    default = [
        {"name": "Let It Be", "artist": "The Beatles", "genre": "pop-rock"},
        {"name": "Lean on Me", "artist": "Bill Withers", "genre": "soul"},
        {"name": "Three Little Birds", "artist": "Bob Marley", "genre": "reggae"},
        {"name": "What a Wonderful World", "artist": "Louis Armstrong", "genre": "jazz"},
        {"name": "Lovely Day", "artist": "Bill Withers", "genre": "soul"},
        {"name": "Put Your Records On", "artist": "Corinne Bailey Rae", "genre": "indie-pop"},
        {"name": "Better Days", "artist": "OneRepublic", "genre": "pop"},
        {"name": "Rise Up", "artist": "Andra Day", "genre": "soul"},
        {"name": "Budapest", "artist": "George Ezra", "genre": "pop"},
        {"name": "On Top of the World", "artist": "Imagine Dragons", "genre": "pop-rock"},
    ]

    key = (current.lower().strip(), desired.lower().strip())
    tracks = playlists.get(key, default)

    # Filter by genre if specified
    if genre_pref:
        filtered = [t for t in tracks if genre_pref in t.get("genre", "")]
        if filtered:
            tracks = filtered

    return tracks[:max_tracks]


def execute(
    current_feeling: str,
    desired_feeling: str,
    job_id: str,
    constraints: Optional[dict] = None,
    adapter: str = "spotify",
) -> dict:
    """Generate a mood-transition playlist.

    Args:
        current_feeling: How the user feels now (e.g. "sad", "stressed", "angry")
        desired_feeling: How they want to feel (e.g. "happy", "calm", "focused")
        job_id: Job ID for artifact storage
        constraints: Optional dict with time_minutes, genre, language, max_tracks
        adapter: "spotify", "apple_music", or "local"

    Returns:
        dict with playlist, rationale, links, adapter used
    """
    constraints = constraints or {}
    max_tracks = constraints.get("max_tracks", 15)

    # Resolve mood seeds
    seeds = resolve_mood_seeds(current_feeling, desired_feeling)

    # Try Spotify first
    tracks = []
    adapter_used = adapter
    if adapter in ("spotify", "auto"):
        tracks = search_spotify_tracks(
            query=f"{current_feeling} to {desired_feeling}",
            seeds=seeds,
            limit=max_tracks,
        )
        if tracks:
            adapter_used = "spotify"

    # Fall back to curated playlist if Spotify unavailable
    if not tracks:
        tracks = _build_curated_playlist(
            current_feeling, desired_feeling, constraints
        )
        adapter_used = "curated"

    # Build links based on adapter
    links = {}
    if adapter_used == "spotify":
        links = {
            "tracks": [t.get("spotify_url", "") for t in tracks if t.get("spotify_url")],
            "type": "spotify",
        }
    elif adapter in ("apple_music",):
        query = f"{desired_feeling} mood {constraints.get('genre', 'mix')}"
        links = {
            "search_url": build_apple_music_url(query),
            "type": "apple_music",
        }
    else:
        links = {
            "payload": build_local_payload(tracks),
            "type": "local",
        }

    # Get MrDP rationale
    rationale = _get_rationale(current_feeling, desired_feeling)

    # Calculate total duration if available
    total_ms = sum(t.get("duration_ms", 210000) for t in tracks)
    total_min = round(total_ms / 60000)

    result = {
        "status": "success",
        "playlist": {
            "tracks": tracks,
            "count": len(tracks),
            "estimated_duration_minutes": total_min,
            "mood_transition": f"{current_feeling} -> {desired_feeling}",
            "seeds": seeds,
        },
        "rationale": rationale,
        "links": links,
        "adapter_used": adapter_used,
    }

    # Save as artifact
    save_artifact(job_id, "playlist.json", result)
    result["artifacts"] = ["playlist.json"]

    return result
