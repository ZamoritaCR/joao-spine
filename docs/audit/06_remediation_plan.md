# Phase 5b: Remediation Plan

**Principle:** Smallest safe steps. Fix security first, then governance, then features.
**Date:** 2026-04-11 (v2)

---

## Sprint 0: Security Hardening (IMMEDIATE -- before any feature work)

**Estimated effort:** 1-2 hours
**Risk if skipped:** Publicly exploitable vulnerabilities

| Task | Gap | Fix | File(s) | Effort |
|------|-----|-----|---------|--------|
| 0.1 | G-01 | Sanitize upload filenames: resolve against job_dir, reject if final path escapes it. Use `Path.resolve()` and check `str(resolved).startswith(str(job_dir))`. | `capability/artifact_store.py:28` | 15 min |
| 0.2 | G-02 | Same sanitization for artifact download: validate filename does not contain `..` or path separators. | `routers/superpowers.py:186-199` | 15 min |
| 0.3 | G-03 | Add `require_dispatch_auth` or a new `require_api_key` dependency to all superpowers endpoints. | `routers/superpowers.py` | 30 min |
| 0.4 | G-04 | Add upload size limit: `File(..., max_length=100_000_000)` or custom middleware. | `routers/superpowers.py:62` | 10 min |
| 0.5 | G-08 | Restrict CORS origins from `"*"` to `["https://joao.theartofthepossible.io", "http://localhost:7778"]`. | `main.py` CORS config | 5 min |

**Verification:** After Sprint 0, run:
```bash
# Path traversal test
curl -s -X POST -F "file=@/tmp/test.twb;filename=../../etc/evil.txt" http://127.0.0.1:7778/joao/superpowers/tableau
# Should return 400 or sanitized filename

# Auth test
curl -s http://127.0.0.1:7778/joao/superpowers/capabilities
# Should return 401/403

# Size limit test
dd if=/dev/zero bs=1M count=200 > /tmp/big.twb
curl -s -X POST -F "file=@/tmp/big.twb" http://127.0.0.1:7778/joao/superpowers/tableau
# Should return 413
```

---

## Sprint 1: Governance Layer (Spec Phase 1)

**Estimated effort:** 1 session
**Depends on:** Sprint 0 completed
**Gaps addressed:** G-05, G-06, G-07, G-18, G-19

| Task | Gap | Fix | Details |
|------|-----|-----|---------|
| 1.1 | G-05 | Create `middleware/autonomy.py` -- FastAPI dependency that extracts autonomy level from request (call `parse_flags()` on body/header) and injects it into request state. | New file |
| 1.2 | G-05 | Add `min_autonomy` field to each capability in `registry.py` and check it in the autonomy middleware. | `capability/registry.py` |
| 1.3 | G-06 | Create `check_lock()` function in `exocortex/ledgers.py` that validates: lock exists, not expired, scope matches target. Wire it into autonomy middleware for L3/L4. | `exocortex/ledgers.py` |
| 1.4 | G-07 | Add provenance recording calls in `routers/superpowers.py` after each capability execution. Call `record_intent()` before and `record_outcome()` after. | `routers/superpowers.py` |
| 1.5 | G-18 | Add `fcntl.flock()` around JSONL writes in `_dual_write()`. | `exocortex/ledgers.py:44-50` |
| 1.6 | G-19 | Add SHA-256 hash of previous entry to each new entry (hash chain). | `exocortex/ledgers.py` |

**Success criteria:**
- Tableau upload at L1 -> 403 (min_autonomy is L2)
- Tableau upload at L2 -> succeeds + provenance entry recorded
- Git write attempt without WRITE_LOCK -> 403
- Two concurrent writes -> both entries intact in JSONL

---

## Sprint 2: Git Adapter + Missing Endpoints (Spec Phase 2)

**Estimated effort:** 1 session
**Depends on:** Sprint 1 (needs lock enforcement)
**Gaps addressed:** G-09, G-14, G-17

| Task | Gap | Fix | Details |
|------|-----|-----|---------|
| 2.1 | G-09 | Create `capability/git_adapter.py` with scan/write/ship functions. | New file |
| 2.2 | G-14 | Add endpoints: `/superpowers/git/scan`, `/superpowers/git/write`, `/superpowers/git/ship` | `routers/superpowers.py` |
| 2.3 | G-14 | Add endpoints: `/superpowers/provenance/{run_id}`, `/superpowers/undo/{run_id}` | `routers/superpowers.py` |
| 2.4 | G-14 | Add endpoint: `/superpowers/tunnel/status` | `routers/superpowers.py` |
| 2.5 | G-17 | Add `POST /joao/agent_callback` endpoint to receive structured results from agents. | `routers/joao.py` or new router |
| 2.6 | G-09 | Register git_scan, git_write, git_ship, tunnel_status, file_ingest in `capability/registry.py`. | `capability/registry.py` |

---

## Sprint 3: Context Packs + Multi-Brain + Undo (Spec Phase 3)

**Estimated effort:** 1 session
**Depends on:** Sprint 1 (needs provenance)
**Gaps addressed:** G-11, G-12, G-13, G-15

| Task | Gap | Fix | Details |
|------|-----|-----|---------|
| 3.1 | G-12 | Create `capability/context_builder.py` -- reads MEMORY.md, CLAUDE.md, queries last 20 provenance entries, assembles context pack per spec Section 5. | New file |
| 3.2 | G-12 | Add `/superpowers/context/build` endpoint. | `routers/superpowers.py` |
| 3.3 | G-15 | Refactor `services/qa_pipeline.py` to query Ollama first, then paid APIs for review. Respect CLAUDE.md canon mandate. | `services/qa_pipeline.py` |
| 3.4 | G-11 | Create `capability/undo_executor.py` -- implements all 5 undo types (delete_artifacts, git_revert, git_delete_branch, noop, service_rollback). | New file |
| 3.5 | G-13 | Create `capability/chain_resolver.py` -- resolves intent to capability chain, executes sequentially with intermediate artifacts. | New file |

---

## Sprint 4: Polish + Operational (Spec Phase 4-5)

**Estimated effort:** 1 session
**Gaps addressed:** G-16, G-20, G-21, G-22, G-24, G-25, G-26, G-27

| Task | Gap | Fix | Details |
|------|-----|-----|---------|
| 4.1 | G-22 | `ollama pull qwen3:8b` | One command |
| 4.2 | G-20 | Kill duplicate tmux sessions (lowercase duplicates). | Script in `scripts/cleanup_tmux.sh` |
| 4.3 | G-21 | Stop the standalone uvicorn dispatch on :7777 (gunicorn on :8100 is the systemd-managed one). | Stop process, update tunnel target |
| 4.4 | G-16 | Add WU data classification: flag ingest sources that may contain WU data, block external API calls for flagged data. | `routers/ingest.py`, `services/brain_manager.py` |
| 4.5 | G-24 | Create `scripts/smoke_test.sh` that runs all smoke tests from `capability_registry.yaml`. | New script |
| 4.6 | G-25 | Add cron job to delete artifacts older than 30 days. | `scripts/cleanup_artifacts.sh` |
| 4.7 | G-26 | Update spec Section 7.1 to reflect 16 council agents (not 7 brains). | Spec update |
| 4.8 | G-27 | Add `egress_summary` field to provenance entries. Log which external APIs were called. | `exocortex/ledgers.py` |
| 4.9 | G-23 | Run `ruff --fix .` to auto-fix 90 unused imports. | One command |

---

## Priority Summary

| Sprint | When | Gaps Fixed | Safety Impact |
|--------|------|-----------|---------------|
| 0 | IMMEDIATE | G-01 to G-04, G-08 | Eliminates exploitable vulns |
| 1 | Next session | G-05 to G-07, G-18, G-19 | Enables governance model |
| 2 | After Sprint 1 | G-09, G-14, G-17 | Completes API surface |
| 3 | After Sprint 1 | G-11 to G-13, G-15 | Completes intelligence layer |
| 4 | After Sprint 2+3 | G-16, G-20-G-28 | Operational polish |
