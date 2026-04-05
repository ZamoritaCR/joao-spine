"""Home Assistant cockpit client -- ADHD-optimized scene control.

Wraps the HA REST API with graceful offline handling.
Pi at 192.168.0.31 may be offline -- every call returns a clean
error dict instead of raising exceptions.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HA_URL = os.getenv("HA_URL", "http://192.168.0.31:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")
TIMEOUT = 5.0


class HACockpit:
    def __init__(self):
        self.base = HA_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        }
        self.last_scene: str = "none"

    def _offline(self, reason: str = "Pi offline") -> dict:
        return {"error": str(reason)[:200], "status": "offline"}

    async def ping(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.get(f"{self.base}/api/", headers=self.headers)
                r.raise_for_status()
                return {"status": "online", "version": r.json().get("version", "unknown")}
        except Exception as e:
            return self._offline(str(e))

    async def get_states(self) -> list | dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.get(f"{self.base}/api/states", headers=self.headers)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            return self._offline(str(e))

    async def get_state(self, entity_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.get(f"{self.base}/api/states/{entity_id}", headers=self.headers)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            return self._offline(str(e))

    async def call_service(self, domain: str, service: str, entity_id: str | None = None, **kwargs) -> dict:
        try:
            data = dict(kwargs)
            if entity_id:
                data["entity_id"] = entity_id
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.post(
                    f"{self.base}/api/services/{domain}/{service}",
                    headers=self.headers,
                    json=data,
                )
                r.raise_for_status()
                return {"status": "ok", "result": r.json()}
        except Exception as e:
            return self._offline(str(e))

    async def fire_event(self, event_type: str, data: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.post(
                    f"{self.base}/api/events/{event_type}",
                    headers=self.headers,
                    json=data or {},
                )
                r.raise_for_status()
                return {"status": "ok", "result": r.json()}
        except Exception as e:
            return self._offline(str(e))

    # -- ADHD Scenes --

    async def scene_focus(self) -> dict:
        """Cool white 5000K, 80% brightness, DND mode."""
        self.last_scene = "focus"
        return await self.call_service("scene", "turn_on", entity_id="scene.focus")

    async def scene_chill(self) -> dict:
        """Warm dim 2700K, 40% brightness."""
        self.last_scene = "chill"
        return await self.call_service("scene", "turn_on", entity_id="scene.chill")

    async def scene_hyperfocus(self) -> dict:
        """Bright cool white 100%, all lights on."""
        self.last_scene = "hyperfocus"
        return await self.call_service("scene", "turn_on", entity_id="scene.hyperfocus")

    async def scene_low_energy(self) -> dict:
        """Minimal warm 20% brightness."""
        self.last_scene = "low_energy"
        return await self.call_service("scene", "turn_on", entity_id="scene.low_energy")

    async def scene_sleep(self) -> dict:
        """All lights off, warm nightlight only."""
        self.last_scene = "sleep"
        return await self.call_service("scene", "turn_on", entity_id="scene.sleep")

    async def scene_morning(self) -> dict:
        """Gradual ramp from 20% to 80% warm white."""
        self.last_scene = "morning"
        return await self.call_service("scene", "turn_on", entity_id="scene.morning")

    async def all_off(self) -> dict:
        """Turn off all lights."""
        self.last_scene = "all_off"
        return await self.call_service("light", "turn_off", entity_id="all")

    async def all_on(self) -> dict:
        """Turn on all lights at 70%."""
        self.last_scene = "all_on"
        return await self.call_service("light", "turn_on", entity_id="all", brightness_pct=70)


SCENES = {
    "focus": "scene_focus",
    "chill": "scene_chill",
    "hyperfocus": "scene_hyperfocus",
    "low_energy": "scene_low_energy",
    "sleep": "scene_sleep",
    "morning": "scene_morning",
    "lights_on": "all_on",
    "lights_off": "all_off",
}

cockpit = HACockpit()
