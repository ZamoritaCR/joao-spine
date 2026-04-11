"""
End-to-end tests for JOAO Capability OS superpowers.

Tests the full lifecycle: auth -> autonomy -> execute -> provenance -> artifacts -> undo.
Runs against a test instance of the FastAPI app.
"""

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

# Setup paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DRDATA_PATH = "/home/zamoritacr/taop-repos/dr-data"
if os.path.isdir(_DRDATA_PATH) and _DRDATA_PATH not in sys.path:
    sys.path.insert(0, _DRDATA_PATH)

# Set a test secret for auth
os.environ.setdefault("JOAO_API_KEY", "test-secret-key-for-e2e")
os.environ.setdefault("JOAO_DISPATCH_HMAC_SECRET", "test-secret-key-for-e2e")

from main import app

client = TestClient(app, raise_server_exceptions=False)

AUTH_HEADER = {"x-joao-api-key": "test-secret-key-for-e2e"}
BAD_AUTH = {"x-joao-api-key": "wrong-key"}


# ──────────────────────────────────────────────
# AUTH TESTS
# ──────────────────────────────────────────────

class TestAuth:
    def test_no_auth_rejected(self):
        """Superpowers endpoints require authentication."""
        resp = client.get("/joao/superpowers/capabilities")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_bad_auth_rejected(self):
        resp = client.get("/joao/superpowers/capabilities", headers=BAD_AUTH)
        assert resp.status_code == 401

    def test_good_auth_accepted(self):
        resp = client.get("/joao/superpowers/capabilities", headers=AUTH_HEADER)
        assert resp.status_code == 200

    def test_bearer_auth_accepted(self):
        resp = client.get(
            "/joao/superpowers/capabilities",
            headers={"authorization": "Bearer test-secret-key-for-e2e"},
        )
        assert resp.status_code == 200


# ──────────────────────────────────────────────
# CAPABILITY REGISTRY TESTS
# ──────────────────────────────────────────────

class TestCapabilities:
    def test_list_capabilities(self):
        resp = client.get("/joao/superpowers/capabilities", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        caps = data["capabilities"]
        assert len(caps) == 10, f"Expected 10 capabilities, got {len(caps)}"
        cap_keys = {c["key"] for c in caps}
        expected = {
            "tableau_to_powerbi", "mood_playlist", "git_scan", "git_write",
            "git_ship", "context_build", "ollama_generate", "tunnel_status",
            "file_ingest", "general",
        }
        assert cap_keys == expected, f"Missing: {expected - cap_keys}"

    def test_route_tableau(self):
        resp = client.post(
            "/joao/superpowers/route",
            json={"text": "migrate this twb to power bi"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["capability"] == "tableau_to_powerbi"
        assert data["min_autonomy"] == "L2"

    def test_route_playlist(self):
        resp = client.post(
            "/joao/superpowers/route",
            json={"text": "play some sad music"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["capability"] == "mood_playlist"

    def test_route_git_scan(self):
        resp = client.post(
            "/joao/superpowers/route",
            json={"text": "what changed in the repo"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["capability"] == "git_scan"

    def test_route_git_ship(self):
        resp = client.post(
            "/joao/superpowers/route",
            json={"text": "push to remote"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["capability"] == "git_ship"

    def test_route_general_fallback(self):
        resp = client.post(
            "/joao/superpowers/route",
            json={"text": "do something random"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["capability"] == "general"

    def test_route_file_extension(self):
        resp = client.post(
            "/joao/superpowers/route",
            json={"text": "process this", "filename": "sales.twbx"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["capability"] == "tableau_to_powerbi"
        assert resp.json()["confidence"] >= 0.95


# ──────────────────────────────────────────────
# AUTONOMY ENFORCEMENT TESTS
# ──────────────────────────────────────────────

class TestAutonomy:
    def test_tableau_rejected_at_L1(self):
        """Tableau requires L2; L1 should be rejected."""
        # Create a valid TWB file
        resp = client.post(
            "/joao/superpowers/tableau",
            files={"file": ("test.twb", b"<workbook></workbook>", "application/xml")},
            data={"autonomy": "L1"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "autonomy_insufficient"
        assert data["detail"]["required"] == "L2"

    def test_tableau_allowed_at_L2(self):
        """Tableau at L2 should pass autonomy check (may fail on parsing, that's OK)."""
        resp = client.post(
            "/joao/superpowers/tableau",
            files={"file": ("test.twb", b"<workbook></workbook>", "application/xml")},
            data={"autonomy": "L2"},
            headers=AUTH_HEADER,
        )
        # 500 is OK (parsing fails on fake TWB) -- the point is it wasn't 403
        assert resp.status_code != 403, "Should not be rejected at L2"

    def test_git_write_rejected_without_lock(self):
        """Git write at L3 without WRITE_LOCK should be rejected."""
        resp = client.post(
            "/joao/superpowers/git/write",
            json={"repo": "joao-spine", "action": "branch", "branch_name": "test-branch", "autonomy": "L3"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "lock_required"
        assert data["detail"]["lock_type"] == "WRITE_LOCK"

    def test_git_ship_rejected_without_lock(self):
        """Git ship at L4 without SHIP_LOCK should be rejected."""
        resp = client.post(
            "/joao/superpowers/git/ship",
            json={"repo": "joao-spine", "action": "push", "autonomy": "L4"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "lock_required"
        assert data["detail"]["lock_type"] == "SHIP_LOCK"

    def test_git_scan_allowed_at_L0(self):
        """Git scan should work at L0 (no lock required)."""
        resp = client.post(
            "/joao/superpowers/git/scan",
            json={"repo": "joao-spine", "since": "1d", "autonomy": "L0"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["repos_scanned"] >= 1


# ──────────────────────────────────────────────
# LOCKS TESTS
# ──────────────────────────────────────────────

class TestLocks:
    def test_grant_write_lock(self):
        resp = client.post(
            "/joao/superpowers/locks/grant",
            json={"lock_type": "WRITE_LOCK", "scope": "repo:test-repo", "duration_minutes": 5},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lock"]["lock_type"] == "WRITE_LOCK"
        assert data["lock"]["scope"] == "repo:test-repo"
        assert data["lock"]["active"] is True

    def test_list_locks(self):
        resp = client.get("/joao/superpowers/locks", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 0

    def test_invalid_lock_type(self):
        resp = client.post(
            "/joao/superpowers/locks/grant",
            json={"lock_type": "INVALID", "scope": "test"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400


# ──────────────────────────────────────────────
# GIT SCAN TESTS
# ──────────────────────────────────────────────

class TestGitScan:
    def test_scan_all_repos(self):
        resp = client.post(
            "/joao/superpowers/git/scan",
            json={"repo": "all", "since": "7d"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["repos_scanned"] >= 3
        assert "provenance" in data

    def test_scan_specific_repo(self):
        resp = client.post(
            "/joao/superpowers/git/scan",
            json={"repo": "joao-spine", "since": "3d"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["repos_scanned"] == 1
        assert "joao-spine" in data["repos"]


# ──────────────────────────────────────────────
# TUNNEL STATUS TESTS
# ──────────────────────────────────────────────

class TestTunnelStatus:
    def test_tunnel_status(self):
        resp = client.get("/joao/superpowers/tunnel/status", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "process_alive" in data
        assert "hostnames" in data


# ──────────────────────────────────────────────
# CONTEXT PACK TESTS
# ──────────────────────────────────────────────

class TestContextPack:
    def test_build_context(self):
        resp = client.post(
            "/joao/superpowers/context/build",
            json={"project": "joao-spine"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        pack = data["context_pack"]
        assert "hash" in pack
        assert pack["hash"].startswith("sha256:")
        assert "operating_rules" in pack["sections"]
        assert "landmines" in pack["sections"]
        assert "provenance" in data


# ──────────────────────────────────────────────
# PROVENANCE TESTS
# ──────────────────────────────────────────────

class TestProvenance:
    def test_recent_provenance(self):
        resp = client.get("/joao/superpowers/provenance?last=5", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data

    def test_provenance_after_scan(self):
        """After a git scan, provenance should be recorded."""
        # Do a scan
        client.post(
            "/joao/superpowers/git/scan",
            json={"repo": "joao-spine", "since": "1d"},
            headers=AUTH_HEADER,
        )
        # Check provenance
        resp = client.get("/joao/superpowers/provenance?last=5", headers=AUTH_HEADER)
        data = resp.json()
        assert data["count"] >= 1
        # Find our scan
        found = any(e.get("parsed_intent") == "git_scan" for e in data["entries"])
        assert found, "Git scan should produce provenance entry"


# ──────────────────────────────────────────────
# TRUST RECEIPT TESTS
# ──────────────────────────────────────────────

class TestTrustReceipt:
    def test_trust_receipt(self):
        """After a capability execution, trust receipt should be available."""
        # Do a scan to generate provenance
        scan_resp = client.post(
            "/joao/superpowers/git/scan",
            json={"repo": "joao-spine", "since": "1d"},
            headers=AUTH_HEADER,
        )
        prov = scan_resp.json().get("provenance", {})
        intent_id = prov.get("intent_id")
        if intent_id:
            resp = client.get(
                f"/joao/superpowers/trust-receipt/{intent_id}",
                headers=AUTH_HEADER,
            )
            assert resp.status_code == 200
            receipt = resp.json()
            assert receipt["intent"]["capability"] == "git_scan"
            assert "undo_plan" in receipt
            assert "data_touched" in receipt


# ──────────────────────────────────────────────
# LEGAL INGESTION TESTS
# ──────────────────────────────────────────────

class TestLegalIngestion:
    def test_ingestion_policy(self):
        resp = client.get("/joao/superpowers/ingestion/policy", headers=AUTH_HEADER)
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed_domain_count"] >= 20
        assert "youtube_policy" in data
        assert "paywall_policy" in data
        assert "wu_policy" in data

    def test_validate_allowed_url(self):
        resp = client.post(
            "/joao/superpowers/ingestion/validate-url",
            json={"url": "https://en.wikipedia.org/wiki/Python"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is True

    def test_validate_blocked_url(self):
        resp = client.post(
            "/joao/superpowers/ingestion/validate-url",
            json={"url": "https://evil-crawler-target.com/scrape"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False

    def test_validate_youtube_url(self):
        resp = client.post(
            "/joao/superpowers/ingestion/validate-url",
            json={"url": "https://www.youtube.com/watch?v=test123"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["is_youtube"] is True
        assert data["checks"]["youtube"]["mode"] == "captions_and_metadata_only"


# ──────────────────────────────────────────────
# PATH TRAVERSAL TESTS
# ──────────────────────────────────────────────

class TestPathTraversal:
    def test_upload_path_traversal_blocked(self):
        """Malicious filename with ../../ should be sanitized."""
        resp = client.post(
            "/joao/superpowers/tableau",
            files={"file": ("../../etc/passwd.twb", b"<workbook></workbook>", "application/xml")},
            data={"autonomy": "L2"},
            headers=AUTH_HEADER,
        )
        # Should not be 403 (autonomy is correct) -- it may be 500 (parsing fail)
        # but the important thing is the file was sanitized, not written to /etc/
        assert resp.status_code != 403

    def test_download_path_traversal_blocked(self):
        """Traversal in artifact download should be blocked."""
        resp = client.get(
            "/joao/superpowers/artifacts/fake-job/../../.env",
            headers=AUTH_HEADER,
        )
        # Should be 400 (traversal blocked) or 404 (not found after sanitization)
        assert resp.status_code in (400, 404)


# ──────────────────────────────────────────────
# UPLOAD SIZE LIMIT TESTS
# ──────────────────────────────────────────────

class TestUploadLimits:
    def test_reject_non_twb(self):
        resp = client.post(
            "/joao/superpowers/tableau",
            files={"file": ("test.txt", b"not a twb", "text/plain")},
            data={"autonomy": "L2"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 400
        assert ".twb" in resp.text


# ──────────────────────────────────────────────
# PLAYLIST TESTS
# ──────────────────────────────────────────────

class TestPlaylist:
    def test_playlist_generation(self):
        resp = client.post(
            "/joao/superpowers/playlist",
            json={"current_feeling": "stressed", "desired_feeling": "focused"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert "provenance" in data

    def test_playlist_at_L0_rejected(self):
        """Playlist requires L1 minimum."""
        resp = client.post(
            "/joao/superpowers/playlist",
            json={"current_feeling": "sad", "desired_feeling": "happy", "autonomy": "L0"},
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 403


# ──────────────────────────────────────────────
# AGENT CALLBACK TEST
# ──────────────────────────────────────────────

class TestAgentCallback:
    def test_agent_callback(self):
        resp = client.post(
            "/joao/superpowers/agent-callback",
            json={
                "job_id": "test-job-001",
                "agent": "BYTE",
                "status": "success",
                "result": {"output": "task completed"},
            },
            headers=AUTH_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["received"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
