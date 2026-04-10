# Phase 4: Deep Code Review Report

**Codebase:** `/home/zamoritacr/joao-spine` (17,135 LOC Python)
**Date:** 2026-04-10
**Reviewer:** BYTE

---

## 1. Architecture & Boundaries

### What Runs Where

| Component | Host | Port | Process |
|-----------|------|------|---------|
| JOAO Spine (FastAPI) | ROG Strix (192.168.0.55) | 7778 | uvicorn |
| Local Dispatch API | ROG Strix | 8100 (gunicorn) + 7777 (uvicorn dev) | gunicorn/uvicorn |
| Council Agents (15x) | ROG Strix | N/A | tmux sessions |
| SCOUT | ROG Strix | N/A | systemd service |
| OS Autonomy Agent | ROG Strix | 7801 | systemd service |
| Ollama | ROG Strix | 11434 | system service |
| Redis | ROG Strix | 6379 | system service |
| Cloudflared | ROG Strix | N/A | systemd (root) |
| joao-interface | ROG Strix | 7781 | python http.server |
| Supabase | Cloud | N/A | SaaS |

### Communication Paths

```
Internet -> Cloudflare Tunnel -> ROG Strix :7778 (Spine)
                              -> ROG Strix :7777/:8100 (Dispatch)
                              -> ROG Strix :7781 (Interface)
                              -> ROG Strix :8502 (DrData)

Spine -> Dispatch: HTTP POST via tunnel (dispatch.theartofthepossible.io)
      -> Dispatch: SSH fallback (192.168.0.55:22)
      -> Supabase: HTTPS (cloud)
      -> Ollama: HTTP (localhost:11434)
      -> Telegram: HTTPS (api.telegram.org)

Dispatch -> tmux: subprocess (send-keys)
         -> Claude: subprocess (claude --print)
         -> Supabase: HTTPS (registration)
```

### Boundary Assessment

**Strengths:**
- Clear separation between Spine (API), Dispatch (agent coordination), and Agents (execution)
- Cloudflare tunnel provides TLS termination and DDoS protection
- Dual-write pattern (Supabase + local) provides resilience

**Weaknesses:**
- Spine and Dispatch both run on same machine -- no isolation between API layer and agent execution
- Two dispatch instances (gunicorn :8100 + uvicorn :7777) -- unclear which is canonical
- No container isolation (everything runs as same user `zamoritacr`)
- `joao-interface` uses Python http.server (no TLS, no security headers, single-threaded)

---

## 2. Security Model

### Authentication

| Endpoint Group | Auth Method | Evidence |
|----------------|-------------|----------|
| /joao/dispatch | HMAC-SHA256 (timestamp + body) | `middleware/auth.py:26-74` |
| /joao/voice/* | API key header | `middleware/auth.py:77-87` |
| /joao/terminal | Token query param | `main.py:236` |
| /joao/superpowers/* | **NONE** | `superpowers.py` -- no auth dependency |
| /joao/council/dispatch | **NONE** (direct) | `routers/joao.py` -- no auth on council dispatch |
| Local dispatch | Bearer token | `joao_local_dispatch.py:144-149` |

**CRITICAL FINDING:** Superpowers endpoints (Tableau upload, Playlist, Artifact download) have NO authentication. Anyone with network access to the spine can upload files and trigger processing.

**Mitigating factor:** Cloudflare tunnel is the only ingress path from the internet. But internal network users on 192.168.0.x have full access.

### SSRF Protection

`routers/inspector.py:40-48`: Blocked networks include RFC1918, link-local, loopback. Domain allowlist defaults to `*.theartofthepossible.io`. Override via `JOAO_INSPECT_ALLOW_PRIVATE=true` (currently set in production -- see startup command).

**Risk:** `JOAO_INSPECT_ALLOW_PRIVATE=true` disables SSRF protection. This was likely set for debugging and should be removed.

### Command Injection Protection

`middleware/auth.py:23,96-99`: Regex blocks `[;&|`$<>]` in commands. `joao_local_dispatch.py` has `is_interactive()` function blocking dangerous commands (claude, vim, ssh, etc.) in automated lane.

**Assessment:** Good for the automated path. But the interactive lane passes arbitrary text to tmux send-keys, which could be abused if an attacker gains dispatch API access.

### Agent Allowlist

`middleware/auth.py:18-21`: 16 agents in frozen set. `joao_local_dispatch.py` validates agent name against `AGENT_SESSIONS` dict.

---

## 3. Autonomy Dial & Locks Enforcement

### What Exists

- **Parser:** `exocortex/ledgers.py:265-328` -- regex extraction of L0-L4, LEARN mode, WRITE_LOCK, SHIP_LOCK from text. Works correctly.
- **Lock storage:** `grant_lock()` and `check_lock()` at `ledgers.py:220-258`. Dual-write, expiry-aware.
- **Lock schema:** lock_id, lock_type, scope, granted_at, expires_at, granted_by, active.

### What Is Missing

- **NO autonomy middleware.** No FastAPI middleware or dependency that:
  1. Extracts autonomy level from request
  2. Checks if the requested operation's min_autonomy is satisfied
  3. Checks if required locks are active
  4. Returns 403 on violation
- **NO per-endpoint autonomy annotations.** Superpowers endpoints have no min_autonomy metadata.
- **NO sandbox enforcement.** L2 operations can write anywhere, not just sandbox dirs.

### Verdict

The autonomy system is **designed and partially coded** (parser + lock manager) but **not enforced**. This is the single largest gap between spec and implementation.

---

## 4. Provenance Ledger Integrity

### Append-Only Guarantee

`ledgers.py:34-37`:
```python
def _append_jsonl(path: Path, record: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
```

Uses `"a"` (append) mode. This is append-only at the application level. However:
- No file-level locking (concurrent writers could interleave lines)
- No OS-level immutable flag (file can be truncated or deleted)
- No cryptographic chaining (no hash linking records together)

### Tamper Evidence

**NONE.** Records have no hash, no signature, no chain linking. A malicious actor with file access could:
- Delete records
- Modify records
- Insert fake records
- Truncate the file

### Hash Integrity

The spec describes `context_pack_hash` (SHA-256 of context pack) and artifact hashes. The `record_intent()` function accepts `context_pack_hash` parameter but no code computes it.

`receipts.py:51` references `a.get('hash', 'n/a')` for artifact hashes. No code computes artifact hashes either.

### Verdict

Provenance ledger is **append-only by convention** (Python "a" mode) but has **no tamper-evidence mechanism**. For a production audit trail, this needs:
1. Record-level SHA-256 hash chaining (each record includes hash of previous)
2. File-level flock for concurrent access
3. Optional: periodic signed snapshots

---

## 5. Data Handling

### File Upload

`superpowers.py:72`: `content = await file.read()` -- reads entire file into memory. No size limit enforced. A 2GB upload would OOM the process.

`artifact_store.py:29`: `dest.write_bytes(content)` -- writes to disk without checking available space.

### Path Traversal

`artifact_store.py:28`: `dest = _job_dir(job_id) / filename` -- `filename` comes from `file.filename` which is user-controlled. A filename like `../../etc/passwd` would write outside the job dir.

**FastAPI's `UploadFile.filename`** preserves the client-supplied filename. This is a **path traversal vulnerability**.

**Mitigation needed:** Sanitize filename (strip path components, validate characters).

### Artifact Download

`superpowers.py:189`: `path = artifact_store.load_artifact(job_id, filename)` -- both `job_id` and `filename` are user-supplied URL path params. `_job_dir()` uses Path concatenation, which could be abused with `..` sequences.

**Mitigation needed:** Validate that resolved path is within ARTIFACTS_DIR.

### Data Retention

No artifact cleanup mechanism. `superpower_artifacts/` grows unbounded. No TTL, no rotation, no size limit.

---

## 6. Reliability

### Timeouts

- **Spine:** No per-route timeout. FastAPI has no default request timeout. Long-running Tableau parsing could block indefinitely.
- **Dispatch HTTP calls:** `services/dispatch.py` uses 45s timeout for dispatch, 15s for gets. Good.
- **tmux operations:** `joao_local_dispatch.py` -- no timeout on subprocess.run() for tmux commands (could hang if tmux server is unresponsive).
- **launch_agent.sh:** `timeout 120` on Claude invocation. Good.

### Retries

- **Dispatch to tunnel:** 3-attempt retry with exponential backoff (2s, 4s). Good.
- **Supabase writes:** No retry. Single attempt with silent failure. Acceptable given local fallback.

### Crash Recovery

- **Spine:** Not supervised by systemd in current deployment (started manually via uvicorn). `joao-spine-local.service` exists but spine is currently running from a shell command, not the service.
- **Dispatch:** Gunicorn with 4 workers provides process-level recovery.
- **Agents:** Watchdog (`council_watchdog.sh`) runs every 5 min via cron to restart dead HOT_POOL agents.
- **SCOUT:** systemd `Restart=always` with burst limit.

### Idempotency

- **Job IDs** use timestamp + UUID (`artifact_store.py:24`). Collision-free.
- **Intent/Outcome IDs** use timestamp + UUID. Collision-free.
- **No deduplication** on intent recording -- same request processed twice creates two records.

---

## 7. Observability

### Logging

- JSON structured logging via `python-json-logger` (`middleware/logging_config.py`)
- `RequestLoggingMiddleware` logs every request
- Per-module loggers throughout codebase
- No log aggregation or centralized logging

### Metrics

- `exocortex/digest.py:get_metrics()` computes success_rate, avg_time, reworks, undo_rate from ledger
- `exocortex/digest.py:get_switchboard()` gives real-time system status
- No Prometheus/Grafana/StatsD integration

### Error Reporting

- Exceptions logged with traceback (`superpowers.py:85`)
- No external error reporting (no Sentry, no PagerDuty)
- Telegram notifications on job completion (`services/telegram.py`)

### Assessment

Observability is **basic but functional**. Logs are structured. Metrics are computed from ledger. No external observability stack.

---

## 8. DX & Maintainability

### Code Structure

```
joao-spine/
  main.py            (376 lines -- app entrypoint)
  routers/           (16 routers)
  capability/        (5 modules -- superpowers)
  exocortex/         (4 modules -- provenance/learning)
  middleware/         (auth + logging)
  services/          (dispatch, ai_processor, telegram, qa_pipeline, supabase, scout)
  models/            (pydantic schemas)
  tools/             (chat helper)
  mcp_server.py      (MCP tools)
  joao_local_dispatch.py (565 lines -- local dispatch)
```

**Strengths:**
- Clean router separation (each router has a single concern)
- Capability modules are self-contained
- Exocortex is well-abstracted (ledgers, learning, digest, receipts)
- Pydantic models for request/response validation

**Weaknesses:**
- `routers/joao.py` at 2,390 lines is a god-module -- handles dispatch, council, content pipeline, build tracking, logs, idea-vault
- `joao_local_dispatch.py` at 565 lines mixes HTTP server, tmux management, process inspection, file I/O
- No test directory or test files found
- Hardcoded paths throughout (`/home/zamoritacr/joao-spine/...`)

### Configuration

- Secrets in `.env` (good)
- Hardcoded paths to home directory (fragile -- breaks on user change or machine migration)
- No configuration file for capability timeouts, retry counts, etc.

---

## 9. Failure Modes Table

| # | Failure Mode | Likelihood | Impact | Mitigation Status |
|---|-------------|-----------|--------|-------------------|
| 1 | Path traversal via filename in upload | MEDIUM | HIGH (arbitrary file write) | **NONE** -- needs filename sanitization |
| 2 | OOM from large file upload | MEDIUM | HIGH (spine crash) | **NONE** -- no size limit |
| 3 | SSRF via inspector with ALLOW_PRIVATE=true | LOW | HIGH (internal network scan) | **WEAKENED** -- protection disabled in prod |
| 4 | Unauthenticated superpowers access | MEDIUM | MEDIUM (file processing abuse) | **NONE** -- no auth on superpowers |
| 5 | Tmux session hijack via dispatch | LOW | HIGH (arbitrary code execution) | **PARTIAL** -- agent allowlist + command filter |
| 6 | Supabase credential leak in logs | LOW | HIGH (DB compromise) | **GOOD** -- env-only, no logging of value |
| 7 | Unbounded artifact storage fills disk | HIGH | MEDIUM (service degradation) | **NONE** -- no cleanup |
| 8 | Concurrent JSONL writes corrupt ledger | LOW | MEDIUM (audit trail integrity) | **NONE** -- no file locking |
| 9 | Spine not supervised by systemd | MEDIUM | MEDIUM (manual restart needed) | **PARTIAL** -- service file exists but not used |
| 10 | Dispatch secret exposed in systemd env | LOW | MEDIUM (unauthorized dispatch) | **ACCEPTED** -- systemd env is root-readable only |
| 11 | Two dispatch instances on different ports | LOW | LOW (confusion, stale routing) | **NONE** -- should consolidate |
| 12 | qwen3:8b model missing from Ollama | LOW | LOW (fallback to other models) | **DOCUMENTED** -- in spec gaps |
| 13 | No rate limiting on any endpoint | MEDIUM | MEDIUM (abuse/DoS) | **NONE** -- relies on Cloudflare |
| 14 | context_watcher.sh silent failure | LOW | LOW (stale context) | **PARTIAL** -- no health check |
| 15 | Redis running as ollama user | LOW | LOW (unexpected permissions) | **NONE** -- should run as dedicated user |

---

## 10. Performance Hotspots

| Hotspot | Location | Issue | Impact |
|---------|----------|-------|--------|
| `file.read()` full file into memory | `superpowers.py:72` | No streaming for large files | OOM risk |
| `_read_jsonl()` reads entire file | `ledgers.py:44` | Reads all lines, filters in Python | Slow as ledger grows |
| `check_lock()` scans all locks | `ledgers.py:242` | Linear scan of entire locks file | Slow with many locks |
| `get_intents()` with local fallback | `ledgers.py:138` | Reads 2x requested records | Unnecessary I/O |
| Supabase client created per call | `ledgers.py:58-59` | No connection pooling | Latency per write |
| `tmux list-sessions` subprocess | `digest.py:28-31` | Fork + exec for each health check | Could cache |

---

## 11. Additive-Only Guarantee

The spec states JOAO is additive-only (new features add, never break existing).

**Evidence:**

Git log shows additive commits:
```
0934ee8 Add Remote Inspector
730943b JOAO v5: Dual-loop exocortex flywheel
c081530 Wire superpowers into /joao/app UI
8370ad8 Add superpowers: Tableau-to-PowerBI + MrDP
```

All commits use "Add" or "Wire" -- no removals or breaking changes.

**The go_live_plan explicitly documents rollback plans** for each phase, and states each phase can be removed without affecting prior phases.

**Assessment:** Additive-only pattern is **observed in practice** through git history and architecture design. Each capability is a separate module; removing one doesn't break others.
