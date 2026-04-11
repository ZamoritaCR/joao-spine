# JOAO Capability OS -- Phase 0: Inventory (Hard Facts)

**Audit date:** 2026-04-11 (v2 -- supersedes 2026-04-10 v1)
**Auditor:** BYTE (spec-cert-v2)
**Method:** READ-ONLY runtime observation + git inspection
**Spec used:** `JOAO_OS_DESIGN.md` v2.0 (2026-04-10) from `/home/zamoritacr/taop-repos/joao-spine/joao-capability-os-spec/`

---

## A. Repository Inventory

| # | Path | Branch | HEAD SHA | Remote |
|---|------|--------|----------|--------|
| 1 | `/home/zamoritacr/joao-spine` (LIVE) | `audit/joao-spec-cert-v1` | `a3e72aea87b835f` | `github.com/ZamoritaCR/joao-spine.git` |
| 2 | `/home/zamoritacr/joao-interface` | `master` | `f28f6a2d0127d98` | `github.com/ZamoritaCR/joao-interface.git` |
| 3 | `/home/zamoritacr/joao-mcp` | `master` | `cc30642316578269` | `github.com/ZamoritaCR/joao-mcp.git` |
| 4 | `/home/zamoritacr/joao-voice` | `master` | `daf209eb9ce78ed` | `github.com/ZamoritaCR/joao-voice.git` |
| 5 | `/home/zamoritacr/joao_autonomy` | `master` | `5d0af6ce3e931c8` | `github.com/ZamoritaCR/joao-autonomy.git` |
| 6 | `/home/zamoritacr/taop-repos/joao-spine` (SPEC) | `master` | `292f698829` | **(none -- local only)** |
| 7 | `/home/zamoritacr/projects/joao-spine` | `main` | `c47342c73386118` | `github.com/ZamoritaCR/joao-spine.git` |
| 8 | `/home/zamoritacr/projects/joao-computer-use` | `master` | `72980c1b` | `github.com/ZamoritaCR/joao-computer-use.git` |
| 9 | `/home/zamoritacr/projects/joao_flutter` | `master` | `f35270b4` | `github.com/ZamoritaCR/joao-flutter.git` |

**Critical observations:**
- Three distinct joao-spine checkouts at different SHAs/branches. The LIVE spine is #1.
- The spec source (#6) has NO git remote -- diverged and untracked.
- joao-spine is on audit branch, not main/master -- indicates prior audit work ongoing.

### Non-Git JOAO Directories

| Path | Contents |
|------|----------|
| `/home/zamoritacr/joao-brain/` | chat-backups/ only |
| `/home/zamoritacr/joao-memory/` | security_incident_2025_04_03.md only |

---

## B. Runtime Inventory (Observed 2026-04-11 ~20:00 UTC)

### Systemd User Services

| Service | Status | PID | Since | RAM | Details |
|---------|--------|-----|-------|-----|---------|
| joao-spine-local | active | 2900533 | Apr 10 18:52 | 214.9 MB | uvicorn main:app :7778 |
| joao-dispatch | active | 605381 | Apr 07 22:17 | 151.6 MB | gunicorn 4w :8100 |
| joao-tunnel | active | 1855 | Apr 07 18:15 | 13.9 MB | cloudflared -> :7777 |

### Network Ports

| Port | Process | Purpose |
|------|---------|---------|
| 7777 | uvicorn joao_local_dispatch | Dispatch (uvicorn, standalone) |
| 7778 | uvicorn main:app | **JOAO Spine (production)** |
| 7800 | uvicorn browser_agent | Browser agent |
| 7801 | uvicorn os_agent | OS autonomy agent |
| 8001 | uvicorn main:app | Secondary app instance |
| 8100 | gunicorn dispatch (4w) | Dispatch (gunicorn, systemd managed) |
| 8503 | uvicorn drdata_v2_app | Dr. Data V2 |
| 11434 | ollama | Local LLM inference |

**Anomaly:** Two dispatch listeners running concurrently (:7777 and :8100). Tunnel routes to :7777.

### Cloudflared Tunnels (7 processes)

| PID | Target | Type |
|-----|--------|------|
| 482840 | /etc/cloudflared/config.yml | Named tunnel (system) |
| 1675 | localhost:7778 | Quick tunnel (spine) |
| 1854 | localhost:7778 | **DUPLICATE** quick tunnel (spine) |
| 1855 | localhost:7777 | Tunnel service (dispatch) |
| 1672 | localhost:8502 | Dr. Data |
| 1673 | localhost:8001 | Secondary app |
| 1674 | localhost:8200 | Unknown service |

### tmux Sessions (26 total)

**Uppercase (Apr 7-8):** APEX, ARIA, BYTE, CJ, CORE, DEX, FLUX, GEMMA, IRIS, MAX, NOVA, SAGE, SCOUT, SOFIA, VOLT (15)
**Lowercase (Apr 10):** aria, byte, cj, dex, gemma, sofia (6)
**Council-prefixed (Apr 10):** council_LEX, council_MAX, council_NOVA, council_SCOUT (4)
**Infrastructure:** dispatch (1)

**Anomaly:** 6 agents have duplicate sessions (upper+lower). Indicates re-launch without cleanup.

### Ollama Models

| Model | Size |
|-------|------|
| deepseek-coder-v2:latest | 8.9 GB |
| phi4:latest | 9.1 GB |
| llama3.1:8b | 4.9 GB |

**Missing:** qwen3:8b (spec Section 7.1 lists it; CLAUDE.md references it; not installed).

---

## C. Codebase Metrics (joao-spine, excl. .venv)

| Metric | Value |
|--------|-------|
| Python files | ~60 |
| Total Python LOC (est.) | ~17,000 |
| FastAPI routers | 16 |
| MCP servers | 2 (main + TAOP) |
| API endpoints (est.) | 70+ |
| Capability modules | 5 (registry, artifact_store, tableau_to_powerbi, mood_playlist, music) |
| Exocortex modules | 4 (ledgers, learning, digest, receipts) |
| Service modules | 10+ |
| Provenance JSONL files | 4 (intents, outcomes, deltas, locks) |
| Shell scripts | 3+ |
