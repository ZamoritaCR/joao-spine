# Phase 4: Deep Code Review Report

**Codebase:** `/home/zamoritacr/joao-spine` (~17,000 LOC Python)
**Date:** 2026-04-11 (v2)
**Reviewer:** BYTE
**Commit:** `a3e72ea`

---

## 1. Architecture & Boundaries

### What Runs Where

| Component | Location | Port | Runtime |
|-----------|----------|------|---------|
| JOAO Spine (FastAPI) | Railway + local | 7778 | uvicorn, systemd |
| Local Dispatch | ROG Strix only | 7777 + 8100 | uvicorn + gunicorn |
| MCP Servers (2) | Embedded in spine | /mcp, /taop/mcp | SSE + Streamable HTTP |
| 16 Council Agents | ROG Strix tmux | N/A | Claude Code sessions |
| Ollama | ROG Strix | 11434 | ollama serve |
| Cloudflared | ROG Strix | N/A | Named + quick tunnels |
| Supabase | Cloud (hosted) | N/A | REST API |

### Communication Flow

```
Internet -> Cloudflare Tunnel -> ROG Strix
  |-> :7778 (spine) -> FastAPI routers -> services
  |-> :8100/:7777 (dispatch) -> tmux sessions -> Claude Code
  |-> :11434 (Ollama) -> local LLM inference
  
Railway (cloud spine) -> Cloudflare Tunnel -> ROG dispatch
  |-> :8100 -> tmux -> agents
```

### Boundary Analysis

- **Network boundary:** Cloudflare tunnel with HMAC auth (dispatch) or MCP host allowlist (MCP tools)
- **Process boundary:** Each agent runs in isolated tmux pane
- **Data boundary:** Supabase is shared state; local JSONL is per-service
- **Trust boundary:** Railway env vars are the secret store; local .env files supplement

**Key finding:** The spine runs BOTH on Railway (cloud) and locally (systemd). The local instance is the production one (port 7778). Railway instance also exists but dispatch routing complexity creates ambiguity about which spine handles what.

---

## 2. Security Model

### Authentication

| Mechanism | Where Used | Strength |
|-----------|-----------|----------|
| HMAC-SHA256 + timestamp | Dispatch endpoints, cockpit | Strong (constant-time compare, 300s skew) |
| Bearer token | Local dispatch listener | Medium (single shared secret) |
| API key header | Voice endpoints | Medium (shared secret) |
| MCP host allowlist | MCP tools | Medium (IP-based, spoofable on same network) |
| WebSocket token | Terminal | Medium (HMAC-compared) |
| None | Superpowers endpoints | **NONE** -- publicly accessible |

### SSRF Protection

`routers/inspector.py` implements proper SSRF protection:
- Domain allowlist (*.theartofthepossible.io)
- Private IP range blocking (127.0.0.0/8, 10.0.0.0/8, 192.168.0.0/16, 172.16.0.0/12)
- HTTPS-only enforcement

**Evidence:** `inspector.py:50-90` -- `_is_safe_target()` function.

### Path Traversal Vulnerability

**CRITICAL -- `capability/artifact_store.py:28`**

```python
def save_upload(job_id: str, filename: str, content: bytes) -> str:
    dest = _job_dir(job_id) / filename  # <-- filename from user input
    dest.write_bytes(content)
```

A filename like `../../etc/cron.d/evil` would escape the job directory. The `_job_dir()` function creates the directory but does not validate that the final path stays within it.

Similarly, `routers/superpowers.py:186-199` `download_artifact()` passes user-supplied `filename` to `load_artifact()` without sanitization.

### CORS

`main.py` CORS configuration:
```python
allow_origins=["*"]
```
This allows any website to make authenticated requests to the spine if cookies are used. Currently mitigated by the fact that auth uses headers, not cookies.

### Secret Handling

All 5 spec-defined secrets sourced from environment:
- SUPABASE_SERVICE_ROLE_KEY -- `os.environ.get()` in `supabase_client.py:15-20`
- TELEGRAM_BOT_TOKEN -- `os.environ.get()` in `telegram.py:8`
- SSH private key -- env PEM or file path in `dispatch.py:60-80`
- JOAO_DISPATCH_HMAC_SECRET -- `os.environ.get()` in `middleware/auth.py:15`
- API keys (OpenAI, Anthropic) -- `os.environ.get()` in respective clients

**Grep confirms zero hardcoded secrets in Python source.**

---

## 3. Autonomy Dial & Lock Enforcement

### Current State

| Component | Exists | Enforced |
|-----------|--------|----------|
| Autonomy level definitions (L0-L3) | Yes (`exocortex/ledgers.py:95,293`) | **NO** |
| Autonomy parser (`parse_flags()`) | Yes (`ledgers.py:288-320`) | Only in exocortex router |
| WRITE_LOCK grant | Yes (`ledgers.py:225-260`) | **NO** (can be granted but never checked) |
| SHIP_LOCK grant | Yes (same) | **NO** |
| Lock expiration | Yes (duration-based) | Never validated before operations |
| Lock scope matching | Yes (field in lock record) | Never matched against operation target |

### Gap

The autonomy dial is a **data model only**. It records what autonomy level a user requested and what locks they granted, but NO code path refuses an operation based on autonomy level or lock status. This is the single largest gap between spec and implementation.

### What Would Fix It

A FastAPI middleware or dependency that:
1. Extracts autonomy level from request (via `parse_flags()`)
2. Checks if the requested capability's `min_autonomy` is satisfied
3. If L3/L4, validates an active, non-expired, scope-matching lock exists
4. Returns 403 with lock requirement message if validation fails

---

## 4. Provenance Ledger Integrity

### Current Implementation

**Files:** `provenance/intents.jsonl`, `provenance/outcomes.jsonl`, `provenance/deltas.jsonl`, `provenance/locks.jsonl`

**Write mechanism:** `exocortex/ledgers.py:44-50`
- Opens file with `"a"` (append) mode
- Writes JSON + newline
- Dual-writes to Supabase (fails gracefully)

### Append-Only Guarantee

**PROVEN:** Files are only opened in append mode. No `"w"`, `"r+"`, or truncation operations exist. No delete/update functions.

**HOWEVER:** The guarantee is filesystem-level only. Any process with write access to the directory can modify the files. No cryptographic chain (hash linking) prevents tampering.

### Tamper-Evidence

**NOT IMPLEMENTED.** The spec describes `context_pack_hash: "sha256:789..."` and file hashes for written artifacts, but:
- No SHA-256 hashing of ledger entries
- No hash chaining between entries
- No integrity verification function

### Dual-Write Reliability

**PROVEN:** `_dual_write()` always writes locally first. Supabase write is in try/except with logging. Local write failure would raise an exception (not caught), which is correct -- local is the source of truth.

**Risk:** Concurrent writes to the same JSONL file could interleave if multiple processes write simultaneously. No file locking (flock/lockfile) is used.

---

## 5. Data Handling

### File Uploads

- Accepted via `routers/superpowers.py` (Tableau) and `routers/ingest.py` (general)
- Stored to `superpower_artifacts/{job_id}/` with user-supplied filename
- **No size limit enforced** -- FastAPI default (unlimited) applies
- **No path traversal protection** on filename (see Section 2)
- **No content-type validation** beyond file extension check

### Artifact Retention

- No cleanup/retention policy
- Artifacts accumulate indefinitely in `superpower_artifacts/`
- No disk space monitoring

### Redaction

- Supabase SERVICE_ROLE_KEY not logged (proven)
- No general PII/sensitive data redaction in logs
- Session logs can contain arbitrary user input

---

## 6. Reliability

### Timeouts & Retries

| Component | Timeout | Retry | Evidence |
|-----------|---------|-------|----------|
| Dispatch HTTP | Not specified | 3x with 2s/4s backoff | `services/dispatch.py:150-180` |
| Dispatch SSH | 10s connect | No retry | `dispatch.py:60` |
| Supabase writes | None explicit | No retry (fails gracefully) | `supabase_client.py` |
| Ollama calls | None explicit | No retry | `brain_manager.py` |
| MCP SSE | 25s grace on shutdown | Reconnect logic in client | `main.py:20-50` |
| SCOUT RSS | None explicit | No retry per source | `services/scout.py` |

**Risk:** Missing timeouts on Ollama and Supabase calls. A hung Ollama inference could block the event loop indefinitely.

### Crash Recovery

- Systemd services auto-restart on failure (proven by uptime)
- tmux sessions survive process crashes (terminal persists)
- No WAL or transaction log for in-flight operations
- Partially written JSONL entries possible on crash (no flush/fsync)

### Idempotency

- Dispatch is NOT idempotent -- repeated calls send duplicate commands to tmux
- SCOUT deduplicates via SHA256(title|url) -- idempotent
- Provenance writes are NOT idempotent -- no unique constraint on intent_id

---

## 7. Observability

### Logging

- JSON structured logging via `middleware/logging_config.py`
- All logs include: timestamp, level, service, request_id
- Request middleware logs latency in ms
- MCP routes excluded from logging (to avoid SSE noise)

### Metrics

- No Prometheus/StatsD metrics exported
- No request count, error rate, or latency histograms
- SCOUT tracks item counts internally
- QA pipeline tracks scores but no time-series

### Error Reporting

- No Sentry/Bugsnag integration
- Errors logged to stdout/JSON
- Supabase failures logged but not alerted
- No dead-letter queue for failed dispatches

---

## 8. DX & Maintainability

### Structure

Good separation: routers/ (HTTP layer), services/ (business logic), capability/ (superpowers), exocortex/ (learning), models/ (schemas), middleware/ (cross-cutting).

### Naming

Consistent Python naming conventions. Router files match their URL prefix. Service files match their domain.

### Config

All configuration via environment variables. No config files beyond `.env`. No config validation at startup (missing vars discovered at first use).

### Docs

- JOAO_MASTER_CONTEXT.md -- comprehensive project context
- JOAO_SESSION_LOG.md -- 479KB active session log
- CLAUDE.md -- execution protocol + canon mandates
- Inline docstrings on most functions

### Issues

- 166 ruff lint warnings (mostly unused imports)
- No type annotations on most functions
- No mypy configuration
- Test directory exists but coverage unknown
- Two dispatch listeners running concurrently (port 7777 + 8100)

---

## 9. Failure Modes Table

| # | Failure Mode | Trigger | Impact | Mitigation | Status |
|---|-------------|---------|--------|------------|--------|
| 1 | Path traversal via upload filename | Malicious filename like `../../etc/cron.d/x` | Arbitrary file write on server | Sanitize filename, resolve against job_dir | **OPEN** |
| 2 | Unbounded upload size | Large file upload (>1GB) | OOM / disk full | Add upload size limit (100MB) | **OPEN** |
| 3 | CORS wildcard allows cross-origin requests | Any website can call API | Data exfiltration if auth cookies exist | Restrict to known origins | **OPEN** |
| 4 | No auth on superpowers endpoints | Public access to tableau/playlist | Unauthorized use of compute resources | Add auth dependency | **OPEN** |
| 5 | Ollama timeout blocks event loop | Slow model inference | All requests hang | Add async timeout wrapper | **OPEN** |
| 6 | Concurrent JSONL writes interleave | Multiple simultaneous capability runs | Corrupted provenance entries | Add flock() or async lock | **OPEN** |
| 7 | Duplicate tmux sessions waste resources | Re-launch without cleanup | 26 sessions, 12 duplicates | Cleanup script + idempotent launch | **OPEN** |
| 8 | Supabase SERVICE_ROLE_KEY compromise | Key leaked from Railway env | Full database access | Row-level security policies | **OPEN** |
| 9 | Dispatch replays | Stolen HMAC signature reused within 300s | Duplicate command execution | Add nonce or narrower timestamp window | LOW RISK |
| 10 | SSH key exposure in /tmp | Temp file not cleaned up | Key readable by other users | Use tempfile.NamedTemporaryFile with delete=True | LOW RISK |
| 11 | qwen3:8b model missing | Code references it, not installed | Ollama errors on qwen requests | `ollama pull qwen3:8b` | COSMETIC |
| 12 | Disk fills from artifact accumulation | No retention policy | Spine crashes | Add retention cron job | MEDIUM |
| 13 | Telegram token compromise | Token leaked | Spam/phishing via JOAO bot | Rotate token, add IP allowlist | LOW RISK |
| 14 | SCOUT RSS source goes down | External service failure | Scan produces incomplete intel | Timeout + skip failed sources | LOW RISK |
| 15 | Railway 30s idle timeout | No activity for 30s | SSE connections drop | PatchedEventSourceResponse keepalive | **MITIGATED** |

---

## 10. Performance Hotspots

| # | Location | Issue | Impact |
|---|----------|-------|--------|
| 1 | `capability/tableau_to_powerbi.py` | Synchronous XML parsing of potentially large TWBX | Blocks event loop during parse |
| 2 | `services/brain_manager.py` | Synchronous Ollama HTTP calls | Blocks during inference (could be minutes) |
| 3 | `routers/ingest.py` | Synchronous file processing (Whisper, PDF extraction) | Blocks during large file processing |
| 4 | `services/scout.py` | Sequential RSS fetch (7 sources) | Total scan time = sum of all source latencies |
| 5 | `routers/superpowers.py:72` | `await file.read()` reads entire upload into memory | Large files consume full RAM |

---

## 11. Additive-Only Guarantee

### Assessment

The spec claims (Section 10) that new features are additive -- existing capabilities continue working if new ones fail. This is **PARTIALLY PROVEN**:

**Evidence FOR additive-only:**
- Superpowers router is a separate module (`routers/superpowers.py`) that can be removed without affecting core `/joao/*` routes
- Capability registry returns `general` fallback if no capability matches
- Supabase failure doesn't block any operation (graceful degradation everywhere)
- Go-live plan explicitly includes rollback commands per phase
- Each router is independently importable

**Evidence AGAINST additive-only:**
- `main.py` imports all routers at startup -- a syntax error in any router crashes the whole spine
- No lazy loading or try/except around router imports
- Exocortex modules are imported eagerly -- a missing dependency would prevent startup
- No feature flags to disable specific capabilities at runtime

**Verdict:** Additive at the design level, but not at the failure isolation level. A broken import in any new module would take down the entire spine.
