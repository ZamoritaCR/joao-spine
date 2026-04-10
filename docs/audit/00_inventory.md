# Phase 0: JOAO Project Inventory

**Audit date:** 2026-04-10
**Auditor:** BYTE (spec certification mode)

---

## A. Repository Inventory

| # | Repo | Path | Branch | HEAD SHA | Remote |
|---|------|------|--------|----------|--------|
| 1 | joao-spine (LIVE) | `/home/zamoritacr/joao-spine` | `mrdp-v1-20260405` | `0934ee8` | `github.com/ZamoritaCR/joao-spine.git` |
| 2 | council | `/home/zamoritacr/council` | `master` | `3478938` | `github.com/ZamoritaCR/council.git` |
| 3 | joao-interface | `/home/zamoritacr/joao-interface` | `master` | `f28f6a2` | `github.com/ZamoritaCR/joao-interface.git` |
| 4 | joao-mcp | `/home/zamoritacr/joao-mcp` | `master` | `cc30642` | `github.com/ZamoritaCR/joao-mcp.git` |
| 5 | joao-voice | `/home/zamoritacr/joao-voice` | `master` | `daf209e` | `github.com/ZamoritaCR/joao-voice.git` |
| 6 | joao_autonomy | `/home/zamoritacr/joao_autonomy` | `master` | `5d0af6c` | `github.com/ZamoritaCR/joao-autonomy.git` |
| 7 | taop-repos/joao-spine (spec) | `/home/zamoritacr/taop-repos/joao-spine` | `master` | `292f698` | None (local only) |
| 8 | projects/joao-spine (alt) | `/home/zamoritacr/projects/joao-spine` | `main` | `3b6ccce` | `github.com/ZamoritaCR/joao-spine.git` |
| 9 | joao-computer-use | `/home/zamoritacr/projects/joao-computer-use` | `master` | `72980c1` | `github.com/ZamoritaCR/joao-computer-use.git` |
| 10 | joao_flutter | `/home/zamoritacr/projects/joao_flutter` | `master` | `f35270b` | `github.com/ZamoritaCR/joao-flutter.git` |

**Primary codebase:** Repo #1 (`/home/zamoritacr/joao-spine`) -- this is the LIVE production spine.
**Spec source:** Repo #7 (`/home/zamoritacr/taop-repos/joao-spine/joao-capability-os-spec/`)

---

## B. Spec Document Used

**File:** `/home/zamoritacr/taop-repos/joao-spine/joao-capability-os-spec/JOAO_OS_DESIGN.md`
**Version:** 2.0
**Author:** BYTE
**Date:** 2026-04-10
**Status:** GROOMING (no implementation code)

Supporting spec files:
- `capability_registry.yaml` (534 lines) -- capability contracts
- `go_live_plan.md` (169 lines) -- phased rollout plan
- `runbooks/` -- operational runbooks (supabase, cloudflared, tmux, chatgpt)

---

## C. Runtime Inventory (observed, not modified)

### Services Running

| Service | PID | Port | Status |
|---------|-----|------|--------|
| joao-spine (uvicorn) | 2696 | 7778 | RUNNING |
| joao_local_dispatch (gunicorn 4w) | 605381+ | 8100 | RUNNING |
| joao_local_dispatch (uvicorn dev) | 471736 | 7777 | RUNNING |
| cloudflared (system tunnel) | 482840 | -- | RUNNING |
| cloudflared (cf-joao spine) | 1675 | -- | RUNNING |
| cloudflared (cf-dispatch) | 1855 | -- | RUNNING |
| cloudflared (drdata) | 1672 | -- | RUNNING |
| cloudflared (telemetry) | 1674 | -- | RUNNING |
| council-scout | 1848 | -- | RUNNING (systemd) |
| joao-os-agent | -- | 7801 | RUNNING (systemd) |
| ollama | 1678 | 11434 | RUNNING |
| redis | 2756 | 6379 | RUNNING |
| joao-interface http.server | 137306 | 7781 | RUNNING |
| context_watcher (inotifywait) | 1102 | -- | RUNNING |

### tmux Sessions (17 total)

All 15 Council agents + `byte` (this session) + `dispatch`:
APEX, ARIA, BYTE, CJ, CORE, DEX, FLUX, GEMMA, IRIS, MAX, NOVA, SAGE, SCOUT, SOFIA, VOLT

### systemd User Services (active)

- `council-scout.service` -- SCOUT Intel Scanner (24/7)
- `joao-dispatch.service` -- Local Dispatch API
- `joao-os-agent.service` -- OS Autonomy Agent (port 7801)
- `joao-tunnel-spine.service` -- Cloudflare Tunnel (Spine 7778)
- `joao-tunnel.service` -- Cloudflare Tunnel

---

## D. Codebase Metrics

| Metric | Value |
|--------|-------|
| Total Python LOC (excl. venv) | 17,135 |
| FastAPI routers | 16 |
| MCP servers | 2 |
| API endpoints (estimated) | 70+ |
| Shell scripts (council) | 8 |
| Capability modules | 5 (registry, artifact_store, tableau, playlist, music) |
| Exocortex modules | 4 (ledgers, learning, digest, receipts) |
| Provenance JSONL files | 6 (intents, outcomes, deltas, locks, experiments, inspections) |
