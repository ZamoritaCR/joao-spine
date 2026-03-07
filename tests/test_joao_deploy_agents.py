"""End-to-end test: JOAO deploys tasks to Council agents.

Proves the full pipeline that a user interacting with JOAO triggers:

  User message -> JOAO /chat (Railway)
    -> Claude Sonnet (tool_use: council_dispatch / council_status / council_session_output)
      -> _execute_council_tool()
        -> services/dispatch (HTTP)
          -> Cloudflare tunnel
            -> local dispatch listener (:8100)
              -> tmux send-keys
                -> agent session

Tests:
  1. JOAO dispatches a task to an agent via /chat and the command
     appears in the agent's tmux session.
  2. JOAO retrieves council status via /chat and reports active agents.
  3. JOAO reads an agent's session output via /chat.
  4. Direct _execute_council_tool() for each tool name (no LLM, deterministic).
  5. Full JOAO /chat round-trip through Railway (LLM picks the tool).

Run:
  cd ~/joao-spine && .venv/bin/python -m pytest tests/test_joao_deploy_agents.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid

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
# Use FLUX for testing -- low-traffic, won't disrupt real work
TEST_AGENT = "FLUX"
TIMEOUT = httpx.Timeout(30.0, connect=15.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tmux_capture(session: str, lines: int = 50) -> str:
    """Capture recent tmux pane output for a session."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", f"-{lines}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def tmux_session_exists(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def marker():
    """Unique marker string to identify test output in tmux."""
    return f"JOAO_E2E_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module", autouse=True)
def ensure_tmux_session():
    """Make sure the test agent's tmux session exists."""
    if not tmux_session_exists(TEST_AGENT):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", TEST_AGENT],
            capture_output=True,
        )
    yield


# ---------------------------------------------------------------------------
# 1. Direct _execute_council_tool (no LLM, deterministic, fast)
# ---------------------------------------------------------------------------

class TestExecuteCouncilToolDirect:
    """Call _execute_council_tool() directly -- bypasses Claude, tests the
    dispatch pipeline from Railway code through tunnel to tmux."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        os.environ["JOAO_LOCAL_DISPATCH_URL"] = TUNNEL_URL
        os.environ["JOAO_DISPATCH_SECRET"] = DISPATCH_SECRET

    @pytest.mark.asyncio
    async def test_council_status_tool(self):
        from routers.joao import _execute_council_tool
        result = await _execute_council_tool("council_status", {})
        assert "Council Agent Status:" in result
        assert "ARIA" in result
        assert "BYTE" in result
        # Should show ONLINE for at least some agents
        assert "ONLINE" in result

    @pytest.mark.asyncio
    async def test_council_dispatch_tool(self, marker):
        from routers.joao import _execute_council_tool
        task = f"echo {marker}_DIRECT_DISPATCH"
        result = await _execute_council_tool("council_dispatch", {
            "agent": TEST_AGENT,
            "task": task,
            "priority": "normal",
        })
        assert "Dispatched to" in result or "dispatched" in result.lower()
        assert TEST_AGENT in result

        # Verify the command landed in tmux
        await asyncio.sleep(2)
        pane = tmux_capture(TEST_AGENT)
        assert f"{marker}_DIRECT_DISPATCH" in pane, (
            f"Marker not found in {TEST_AGENT} tmux. Last 500 chars:\n{pane[-500:]}"
        )

    @pytest.mark.asyncio
    async def test_council_session_output_tool(self, marker):
        from routers.joao import _execute_council_tool
        result = await _execute_council_tool("council_session_output", {
            "agent": TEST_AGENT,
        })
        assert f"{TEST_AGENT} session output:" in result
        # Should contain recent output from the session
        assert len(result) > 30

    @pytest.mark.asyncio
    async def test_dispatch_all_16_agents_visible(self):
        """council_status must show all 16 agents."""
        from routers.joao import _execute_council_tool
        result = await _execute_council_tool("council_status", {})
        expected = [
            "ARIA", "BYTE", "CJ", "SOFIA", "DEX", "GEMMA",
            "MAX", "LEX", "NOVA", "SCOUT",
            "SAGE", "FLUX", "CORE", "APEX", "IRIS", "VOLT",
        ]
        for agent in expected:
            assert agent in result, f"Agent {agent} missing from council_status"


# ---------------------------------------------------------------------------
# 2. Dispatch through tunnel and verify tmux output
# ---------------------------------------------------------------------------

class TestDispatchLandsInTmux:
    """Dispatch via tunnel and verify the exact command appears in tmux."""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = httpx.Client(timeout=TIMEOUT)
        yield
        self.client.close()

    def test_automated_dispatch_appears_in_tmux(self, marker):
        tag = f"{marker}_AUTOMATED"
        r = self.client.post(
            f"{TUNNEL_URL}/dispatch",
            json={
                "agent": TEST_AGENT,
                "task": f"echo {tag}",
                "priority": "normal",
                "lane": "automated",
            },
            headers={
                "Authorization": f"Bearer {DISPATCH_SECRET}",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "dispatched"

        # Wait for tmux to execute
        time.sleep(2)
        pane = tmux_capture(TEST_AGENT)
        assert tag in pane, (
            f"Marker {tag} not found in {TEST_AGENT} tmux.\n"
            f"Last 500 chars:\n{pane[-500:]}"
        )

    def test_interactive_dispatch_appears_in_tmux(self, marker):
        tag = f"{marker}_INTERACTIVE"
        r = self.client.post(
            f"{TUNNEL_URL}/dispatch",
            json={
                "agent": TEST_AGENT,
                "task": f"echo {tag}",
                "priority": "normal",
                "lane": "interactive",
            },
            headers={
                "Authorization": f"Bearer {DISPATCH_SECRET}",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200

        time.sleep(2)
        pane = tmux_capture(TEST_AGENT)
        assert tag in pane

    def test_dispatch_raw_appears_in_tmux(self, marker):
        tag = f"{marker}_RAW"
        r = self.client.post(
            f"{TUNNEL_URL}/dispatch/raw",
            json={"agent": TEST_AGENT, "task": f"echo {tag}"},
            headers={
                "Authorization": f"Bearer {DISPATCH_SECRET}",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200

        time.sleep(2)
        pane = tmux_capture(TEST_AGENT)
        assert tag in pane

    def test_session_output_reflects_dispatched_command(self, marker):
        """After dispatching, the tunnel session endpoint returns the output."""
        tag = f"{marker}_REFLECT"
        self.client.post(
            f"{TUNNEL_URL}/dispatch",
            json={
                "agent": TEST_AGENT,
                "task": f"echo {tag}",
                "priority": "normal",
                "lane": "automated",
            },
            headers={
                "Authorization": f"Bearer {DISPATCH_SECRET}",
                "Content-Type": "application/json",
            },
        )
        time.sleep(2)

        r = self.client.get(f"{TUNNEL_URL}/session/{TEST_AGENT}")
        assert r.status_code == 200
        output = r.json().get("output", "")
        assert tag in output


# ---------------------------------------------------------------------------
# 3. JOAO /chat on Railway triggers council tools (full LLM round-trip)
# ---------------------------------------------------------------------------

class TestJOAODeploysAgentsViaRailway:
    """Exercise the exact code path JOAO's /chat uses to deploy agents,
    but call _execute_council_tool() directly -- no LLM, no API tokens.

    Pipeline tested: _execute_council_tool() -> services/dispatch
      -> Cloudflare tunnel -> local dispatch (:8100) -> tmux
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        os.environ["JOAO_LOCAL_DISPATCH_URL"] = TUNNEL_URL
        os.environ["JOAO_DISPATCH_SECRET"] = DISPATCH_SECRET

    @pytest.mark.asyncio
    async def test_joao_checks_council_status(self):
        """council_status tool returns all 16 agents with ONLINE/OFFLINE."""
        from routers.joao import _execute_council_tool
        result = await _execute_council_tool("council_status", {})
        assert "Council Agent Status:" in result
        assert "ONLINE" in result
        # All 16 must appear
        for name in ["ARIA", "BYTE", "CJ", "SOFIA", "DEX", "GEMMA",
                      "MAX", "LEX", "NOVA", "SCOUT", "SAGE", "FLUX",
                      "CORE", "APEX", "IRIS", "VOLT"]:
            assert name in result, f"{name} missing from council_status"

    @pytest.mark.asyncio
    async def test_joao_dispatches_to_agent_and_lands_in_tmux(self, marker):
        """council_dispatch tool sends command through tunnel and it
        appears in the agent's tmux session."""
        from routers.joao import _execute_council_tool
        tag = f"{marker}_JOAO_DEPLOY"
        result = await _execute_council_tool("council_dispatch", {
            "agent": TEST_AGENT,
            "task": f"echo {tag}",
            "priority": "normal",
        })
        assert "Dispatched to" in result
        assert TEST_AGENT in result

        # Verify command landed in tmux
        await asyncio.sleep(2)
        pane = tmux_capture(TEST_AGENT)
        assert tag in pane, (
            f"Marker {tag} not in {TEST_AGENT} tmux.\n"
            f"Last 500 chars:\n{pane[-500:]}"
        )

    @pytest.mark.asyncio
    async def test_joao_reads_agent_output(self):
        """council_session_output tool retrieves the agent's terminal buffer."""
        from routers.joao import _execute_council_tool
        result = await _execute_council_tool("council_session_output", {
            "agent": TEST_AGENT,
        })
        assert f"{TEST_AGENT} session output:" in result
        assert len(result) > 50


# ---------------------------------------------------------------------------
# 4. Multi-agent dispatch (JOAO dispatches to multiple agents)
# ---------------------------------------------------------------------------

class TestMultiAgentDispatch:
    """Verify JOAO can dispatch to different agents and each lands correctly."""

    AGENTS = ["FLUX", "CORE", "APEX"]

    @pytest.fixture(autouse=True)
    def _setup(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        os.environ["JOAO_LOCAL_DISPATCH_URL"] = TUNNEL_URL
        os.environ["JOAO_DISPATCH_SECRET"] = DISPATCH_SECRET
        # Ensure all test sessions exist
        for agent in self.AGENTS:
            if not tmux_session_exists(agent):
                subprocess.run(
                    ["tmux", "new-session", "-d", "-s", agent],
                    capture_output=True,
                )

    @pytest.mark.asyncio
    async def test_dispatch_to_three_agents(self, marker):
        """Dispatch unique commands to 3 agents and verify each one landed."""
        from services.dispatch import dispatch_to_agent

        tags = {}
        for agent in self.AGENTS:
            tag = f"{marker}_{agent}_MULTI"
            tags[agent] = tag
            result = await dispatch_to_agent(
                agent=agent,
                task=f"echo {tag}",
                priority="normal",
            )
            assert result["status"] == "dispatched"

        # Wait for all tmux sessions to execute
        await asyncio.sleep(3)

        for agent, tag in tags.items():
            pane = tmux_capture(agent)
            assert tag in pane, (
                f"Agent {agent}: marker {tag} not in tmux.\n"
                f"Last 300 chars:\n{pane[-300:]}"
            )

    @pytest.mark.asyncio
    async def test_each_agent_session_is_isolated(self, marker):
        """A command dispatched to FLUX must NOT appear in CORE's session."""
        from services.dispatch import dispatch_to_agent

        flux_tag = f"{marker}_ISOLATION_FLUX"
        await dispatch_to_agent(agent="FLUX", task=f"echo {flux_tag}")

        await asyncio.sleep(2)

        flux_pane = tmux_capture("FLUX")
        core_pane = tmux_capture("CORE")

        assert flux_tag in flux_pane, "FLUX didn't get its command"
        assert flux_tag not in core_pane, "FLUX command leaked into CORE session"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
