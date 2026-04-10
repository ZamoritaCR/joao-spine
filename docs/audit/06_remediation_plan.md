# Phase 5b: Remediation Plan

**Principle:** Smallest safe steps. Fix security first, then governance, then features.

---

## Sprint 0: Security Hardening (IMMEDIATE -- before any feature work)

### S0-1: Fix path traversal (G-01, G-08)

**File:** `capability/artifact_store.py`

```python
# In save_upload() and load_artifact():
import os
safe_name = os.path.basename(filename)  # Strip all path components
# Then validate: resolved_path.is_relative_to(ARTIFACTS_DIR)
```

**Effort:** 15 minutes. **Risk:** None (additive validation).

### S0-2: Add upload size limit (G-03)

**File:** `routers/superpowers.py`

Add to `tableau_upload()`:
```python
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
content = await file.read()
if len(content) > MAX_UPLOAD_SIZE:
    raise HTTPException(413, "File too large")
```

**Effort:** 5 minutes. **Risk:** None.

### S0-3: Disable SSRF bypass (G-05)

Remove `JOAO_INSPECT_ALLOW_PRIVATE=true` from the spine startup command/env.

**Effort:** 2 minutes. **Risk:** Inspector will block private IPs (intended behavior).

### S0-4: Add basic auth to superpowers (G-02)

**File:** `routers/superpowers.py`

Add `require_api_key` dependency from `middleware/auth.py` to all superpowers endpoints.

**Effort:** 30 minutes. **Risk:** Low -- existing auth middleware, just not applied.

---

## Sprint 1: Provenance Wiring (Phase 1 from go_live_plan)

### S1-1: Wire provenance to superpowers (G-06)

In `superpowers.py`, wrap each capability execution:
1. Call `record_intent()` before execution
2. Call `record_outcome()` after execution (success or failure)
3. Include timing, artifacts list, capability chain

**Effort:** 1-2 hours. **Risk:** Low -- additive logging, doesn't change execution.

### S1-2: Build autonomy enforcement middleware (G-04)

Create `middleware/autonomy.py`:
1. Extract autonomy level from request (call `parse_control_flags`)
2. Compare against endpoint's `min_autonomy` annotation
3. Check locks via `check_lock()` if L3/L4
4. Return 403 with lock requirement if violated

Apply as FastAPI dependency to superpowers + git endpoints.

**Effort:** 2-3 hours. **Risk:** Medium -- could block legitimate operations if misconfigured. Test thoroughly.

### S1-3: Add provenance API endpoints (G-06)

Add to `routers/superpowers.py`:
- `GET /superpowers/provenance/{run_id}` -- calls `get_intent()` + `get_outcomes()`
- `GET /superpowers/provenance?last=N` -- calls `get_intents(last_n=N)`

**Effort:** 30 minutes. **Risk:** None -- read-only endpoints.

### S1-4: Add file locking to JSONL writes (G-12)

In `ledgers.py:_append_jsonl()`, wrap with `fcntl.flock()`:
```python
import fcntl
with open(path, "a") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.write(json.dumps(record) + "\n")
    # flock auto-released on close
```

**Effort:** 10 minutes. **Risk:** None.

---

## Sprint 2: Git Adapter + Undo (Phase 2 from go_live_plan)

### S2-1: Implement git adapter

Create `capability/git_adapter.py`:
- `git_scan(repo, since)` -- subprocess git commands (status, log, diff)
- `git_write(repo, action, branch, message)` -- checkout, commit (behind lock check)
- `git_ship(repo, action)` -- push (behind lock check)

### S2-2: Add git API endpoints

Add to superpowers router:
- `POST /superpowers/git/scan`
- `POST /superpowers/git/write` (checks WRITE_LOCK)
- `POST /superpowers/git/ship` (checks SHIP_LOCK)

### S2-3: Implement undo executor (G-07)

Create `capability/undo.py`:
- `execute_undo(run_id)` -- reads provenance, executes undo recipe
- Handle: delete_artifacts, git_revert, noop
- Add `POST /superpowers/undo/{run_id}`

**Effort:** 1 session. **Risk:** Medium -- git operations affect repos.

---

## Sprint 3: Context Packs + Multi-Brain (Phase 3 from go_live_plan)

### S3-1: Build context pack assembler (G-10)

Create `capability/context_builder.py`:
- Read CLAUDE.md + MEMORY.md for operating_rules
- Query last 20 intents for session_history
- Populate landmines from hardcoded list + CLAUDE.md
- Compute SHA-256 hash of assembled pack

### S3-2: Fix multi-brain to use Ollama-first (G-11)

Modify QA pipeline to call Ollama before paid APIs, per CLAUDE.md mandate.

**Effort:** 1 session. **Risk:** Low -- additive changes.

---

## Sprint 4: Operational Hardening

### S4-1: Run spine under systemd (G-16)

Enable `joao-spine-local.service` and stop the manual uvicorn process.

### S4-2: Add artifact cleanup cron (G-14)

Add cron job to delete artifacts older than 30 days.

### S4-3: Consolidate dispatch instances (G-18)

Choose gunicorn :8100 as canonical. Remove uvicorn :7777 dev instance.

### S4-4: Add tests (G-21)

Create `tests/` with pytest. Start with smoke tests from capability_registry.yaml.

**Effort:** 1 session. **Risk:** None.

---

## Priority Summary

| Sprint | Focus | Gaps Addressed | Effort |
|--------|-------|---------------|--------|
| S0 | Security fixes | G-01,02,03,05,08 | 1 hour |
| S1 | Governance wiring | G-04,06,12 | 1 session |
| S2 | Git + Undo | G-07,09 (partial) | 1 session |
| S3 | Context + Multi-brain | G-10,11 | 1 session |
| S4 | Operations | G-14,16,18,21 | 1 session |
