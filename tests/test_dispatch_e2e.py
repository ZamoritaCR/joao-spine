"""End-to-end tests for the JOAO dispatch pipeline.

Tests the full path that each MCP tool takes:
  dispatch_agent       -> dispatch.dispatch_raw_to_agent() -> POST /dispatch/raw
  joao_council_status  -> dispatch.get_agents()            -> GET  /agents
  joao_council_dispatch-> dispatch.dispatch_to_agent()     -> POST /dispatch
  joao_agent_output    -> dispatch.get_session()           -> GET  /session/{agent}

Each test verifies:
  1. _tunnel_config() resolves the correct URL (unit)
  2. Local dispatch listener responds (localhost:8100)
  3. Cloudflare tunnel reaches the listener (dispatch.theartofthepossible.io)
  4. Railway spine reaches through the tunnel (joao-spine-production.up.railway.app)
"""

from __future__ import annotations

import asyncio
import os
import sys
import json
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_DISPATCH = "http://localhost:8100"
TUNNEL_URL = "https://dispatch.theartofthepossible.io"
RAILWAY_URL = "https://joao-spine-production.up.railway.app"
DISPATCH_SECRET = os.environ.get(
    "JOAO_DISPATCH_SECRET",
    "4S1nLyumC1MfqZ1HDx20Z-MSkWu-sUIdT9IEm18DMXE",
)
AUTH_HEADERS = {
    "Authorization": f"Bearer {DISPATCH_SECRET}",
    "Content-Type": "application/json",
}
TEST_AGENT = "FLUX"  # low-traffic agent, won't disrupt real work
TIMEOUT = httpx.Timeout(15.0, connect=10.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    return dict(AUTH_HEADERS)


def _dispatch_payload(task: str, lane: str = "automated") -> dict:
    return {
        "agent": TEST_AGENT,
        "task": task,
        "priority": "normal",
        "lane": lane,
    }


# ---------------------------------------------------------------------------
# 1. Unit: _tunnel_config resolves correctly for every env-var scenario
# ---------------------------------------------------------------------------

class TestTunnelConfigUnit:
    """Verify _tunnel_config() produces the right URL regardless of env input."""

    @staticmethod
    def _get_url(env_value: str) -> str:
        os.environ["JOAO_LOCAL_DISPATCH_URL"] = env_value
        os.environ.setdefault("JOAO_DISPATCH_SECRET", "test")
        # Re-import each time to pick up env changes (function reads live)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from services.dispatch import _tunnel_config
        url, _ = _tunnel_config()
        return url

    @pytest.mark.parametrize("env_value", [
        "",
        "http://localhost:8100",
        "http://127.0.0.1:8100",
    ])
    def test_empty_or_localhost_defaults_to_tunnel(self, env_value):
        url = self._get_url(env_value)
        assert url == TUNNEL_URL, f"env={env_value!r} -> got {url!r}"

    def test_trailing_slash_stripped(self):
        url = self._get_url("https://dispatch.theartofthepossible.io/")
        assert url == TUNNEL_URL

    def test_os_proxy_suffix_stripped(self):
        url = self._get_url("https://dispatch.theartofthepossible.io/os-proxy")
        assert url == TUNNEL_URL

    def test_os_proxy_trailing_slash_stripped(self):
        url = self._get_url("https://dispatch.theartofthepossible.io/os-proxy/")
        assert url == TUNNEL_URL

    def test_correct_value_unchanged(self):
        url = self._get_url("https://dispatch.theartofthepossible.io")
        assert url == TUNNEL_URL

    def test_no_double_slash_in_constructed_urls(self):
        """The actual URLs that dispatch functions build must never have //."""
        url = self._get_url("")
        for path in ["/dispatch", "/dispatch/raw", "/agents", "/session/ARIA"]:
            full = f"{url}{path}"
            # After scheme, no double slashes allowed
            after_scheme = full.split("://", 1)[1]
            assert "//" not in after_scheme, f"Double slash in {full}"


# ---------------------------------------------------------------------------
# 2. Local dispatch listener (localhost:8100)
# ---------------------------------------------------------------------------

class TestLocalDispatch:
    """Verify the local dispatch listener is running and responds correctly."""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = httpx.Client(timeout=TIMEOUT)
        yield
        self.client.close()

    def test_health(self):
        r = self.client.get(f"{LOCAL_DISPATCH}/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "alive"

    def test_agents_returns_16(self):
        r = self.client.get(f"{LOCAL_DISPATCH}/agents")
        assert r.status_code == 200
        agents = r.json()["agents"]
        assert len(agents) == 16

    def test_dispatch_requires_auth(self):
        r = self.client.post(
            f"{LOCAL_DISPATCH}/dispatch",
            json=_dispatch_payload("echo NO_AUTH_TEST"),
        )
        assert r.status_code == 401

    def test_dispatch_with_auth(self):
        r = self.client.post(
            f"{LOCAL_DISPATCH}/dispatch",
            json=_dispatch_payload("echo LOCAL_E2E_OK"),
            headers=_auth_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "dispatched"
        assert data["agent"] == TEST_AGENT

    def test_dispatch_raw_with_auth(self):
        r = self.client.post(
            f"{LOCAL_DISPATCH}/dispatch/raw",
            json={"agent": TEST_AGENT, "task": "echo LOCAL_RAW_OK"},
            headers=_auth_headers(),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "sent"

    def test_session_output(self):
        r = self.client.get(f"{LOCAL_DISPATCH}/session/{TEST_AGENT}")
        assert r.status_code == 200
        data = r.json()
        assert data["agent"] == TEST_AGENT
        assert "output" in data

    def test_dispatch_rejects_interactive_in_automated_lane(self):
        r = self.client.post(
            f"{LOCAL_DISPATCH}/dispatch",
            json=_dispatch_payload("claude do something", lane="automated"),
            headers=_auth_headers(),
        )
        assert r.status_code == 422

    def test_dispatch_unknown_agent(self):
        payload = {"agent": "NOBODY", "task": "echo fail", "priority": "normal", "lane": "automated"}
        r = self.client.post(
            f"{LOCAL_DISPATCH}/dispatch",
            json=payload,
            headers=_auth_headers(),
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 3. Cloudflare tunnel (dispatch.theartofthepossible.io)
# ---------------------------------------------------------------------------

class TestCloudfareTunnel:
    """Verify the Cloudflare tunnel routes correctly to the local dispatch."""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = httpx.Client(timeout=TIMEOUT)
        yield
        self.client.close()

    def test_health_through_tunnel(self):
        r = self.client.get(f"{TUNNEL_URL}/health")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

    def test_agents_through_tunnel(self):
        r = self.client.get(f"{TUNNEL_URL}/agents")
        assert r.status_code == 200
        agents = r.json()["agents"]
        assert len(agents) == 16

    def test_dispatch_through_tunnel(self):
        r = self.client.post(
            f"{TUNNEL_URL}/dispatch",
            json=_dispatch_payload("echo TUNNEL_E2E_OK"),
            headers=_auth_headers(),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "dispatched"

    def test_dispatch_raw_through_tunnel(self):
        r = self.client.post(
            f"{TUNNEL_URL}/dispatch/raw",
            json={"agent": TEST_AGENT, "task": "echo TUNNEL_RAW_OK"},
            headers=_auth_headers(),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "sent"

    def test_session_through_tunnel(self):
        r = self.client.get(f"{TUNNEL_URL}/session/{TEST_AGENT}")
        assert r.status_code == 200
        data = r.json()
        assert data["agent"] == TEST_AGENT

    def test_os_proxy_routes_to_os_agent(self):
        """The /os-proxy/ path should forward to localhost:7801 (os-agent)."""
        r = self.client.get(
            f"{TUNNEL_URL}/os-proxy/status",
            headers=_auth_headers(),
        )
        # 200 if os-agent is running, 502 if not -- but NOT 404
        assert r.status_code in (200, 502)


# ---------------------------------------------------------------------------
# 4. Railway spine -> tunnel -> local dispatch (full round trip)
# ---------------------------------------------------------------------------

class TestRailwaySpine:
    """Verify Railway can reach the local dispatch through the tunnel."""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = httpx.Client(timeout=httpx.Timeout(30.0, connect=15.0))
        yield
        self.client.close()

    def test_spine_is_live(self):
        r = self.client.get(f"{RAILWAY_URL}/joao/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_spine_status_healthy(self):
        r = self.client.get(f"{RAILWAY_URL}/joao/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("healthy", "degraded")
        ssh_check = data["checks"]["ssh"]
        assert ssh_check["ok"] is True, f"ssh check failed: {ssh_check}"
        assert "dispatch.theartofthepossible.io" in ssh_check.get("target", "")

    def test_spine_sees_tmux_sessions(self):
        r = self.client.get(f"{RAILWAY_URL}/joao/status")
        assert r.status_code == 200
        tmux = r.json()["checks"]["tmux"]
        assert tmux["ok"] is True
        assert len(tmux.get("sessions", [])) > 0


# ---------------------------------------------------------------------------
# 5. Full MCP tool simulation (async, matches actual tool code paths)
# ---------------------------------------------------------------------------

class TestMCPToolPaths:
    """Simulate each MCP tool's actual async code path end-to-end.

    These mirror the exact calls in services/dispatch.py that the MCP
    tools invoke, but hit the tunnel directly to prove the pipeline.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        os.environ["JOAO_LOCAL_DISPATCH_URL"] = TUNNEL_URL
        os.environ["JOAO_DISPATCH_SECRET"] = DISPATCH_SECRET

    @pytest.mark.asyncio
    async def test_dispatch_agent_path(self):
        """dispatch_agent -> dispatch.dispatch_raw_to_agent()"""
        from services.dispatch import dispatch_raw_to_agent
        result = await dispatch_raw_to_agent(TEST_AGENT, "echo MCP_RAW_E2E")
        assert result["status"] == "sent"
        assert result["agent"] == TEST_AGENT

    @pytest.mark.asyncio
    async def test_joao_council_status_path(self):
        """joao_council_status -> dispatch.get_agents()"""
        from services.dispatch import get_agents
        result = await get_agents()
        agents = result.get("agents", {})
        assert len(agents) == 16
        # Verify structure
        for name, info in agents.items():
            assert "session" in info
            assert "active" in info

    @pytest.mark.asyncio
    async def test_joao_council_dispatch_path(self):
        """joao_council_dispatch -> dispatch.dispatch_to_agent()"""
        from services.dispatch import dispatch_to_agent
        result = await dispatch_to_agent(
            agent=TEST_AGENT,
            task="echo MCP_DISPATCH_E2E",
            priority="normal",
        )
        assert result["status"] == "dispatched"
        assert result["agent"] == TEST_AGENT

    @pytest.mark.asyncio
    async def test_joao_agent_output_path(self):
        """joao_agent_output -> dispatch.get_session()"""
        from services.dispatch import get_session
        result = await get_session(TEST_AGENT)
        assert result["agent"] == TEST_AGENT
        assert "output" in result

    @pytest.mark.asyncio
    async def test_dispatch_agent_with_wait(self):
        """dispatch_agent with wait=True: dispatch_raw + sleep + get_session."""
        from services.dispatch import dispatch_raw_to_agent, get_session

        # Step 1: send command
        send_result = await dispatch_raw_to_agent(TEST_AGENT, "echo E2E_WAIT_TEST_$(date +%s)")
        assert send_result["status"] == "sent"

        # Step 2: brief wait (mirrors mcp_server.py dispatch_agent logic)
        await asyncio.sleep(3)

        # Step 3: capture output
        session_result = await get_session(TEST_AGENT)
        assert session_result["agent"] == TEST_AGENT
        assert "E2E_WAIT_TEST_" in session_result.get("output", "")


# ---------------------------------------------------------------------------
# 6. Regression: URL construction never produces double slashes
# ---------------------------------------------------------------------------

class TestURLRegression:
    """Guard against the /os-proxy// double-slash bug recurring."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    @pytest.mark.parametrize("bad_env", [
        "https://dispatch.theartofthepossible.io/",
        "https://dispatch.theartofthepossible.io/os-proxy",
        "https://dispatch.theartofthepossible.io/os-proxy/",
        "",
        "http://localhost:8100",
        "http://localhost:8100/",
    ])
    def test_no_double_slash_in_tunnel_url(self, bad_env):
        os.environ["JOAO_LOCAL_DISPATCH_URL"] = bad_env
        os.environ.setdefault("JOAO_DISPATCH_SECRET", "test")
        from services.dispatch import _tunnel_config
        url, _ = _tunnel_config()
        after_scheme = url.split("://", 1)[1]
        assert "//" not in after_scheme, f"Double slash with env={bad_env!r}: {url}"
        assert not url.endswith("/"), f"Trailing slash with env={bad_env!r}: {url}"
        assert "/os-proxy" not in url, f"os-proxy leak with env={bad_env!r}: {url}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
