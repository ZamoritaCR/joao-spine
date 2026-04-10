# Phase 5a: Gap Register

**Ranked by severity (CRITICAL > HIGH > MEDIUM > LOW) then blast radius.**

---

## CRITICAL

| # | Gap | REQs | Blast Radius | Risk |
|---|-----|------|-------------|------|
| G-01 | **Path traversal in file upload** -- user-supplied filename used directly in Path concatenation (`artifact_store.py:28`). Attacker can write to arbitrary paths. | REQ-0046 | Arbitrary file overwrite on server | Remote exploitation if superpowers endpoints are reachable |
| G-02 | **No auth on superpowers endpoints** -- Tableau upload, playlist, artifact download have zero authentication. | REQ-0049-0056 | Unauthorized processing, data exfiltration | Any network-accessible client can abuse |
| G-03 | **No upload size limit** -- `file.read()` loads entire upload into memory. No max size. | REQ-0036 | Server OOM crash | Single request can take down spine |

## HIGH

| # | Gap | REQs | Blast Radius | Risk |
|---|-----|------|-------------|------|
| G-04 | **Autonomy enforcement not wired** -- Parser and lock manager exist but no middleware enforces levels. All endpoints effectively run at L4. | REQ-0005,0007,0008,0011,0013 | Governance bypass -- any operation can execute without locks | Design intent violated; no safety net |
| G-05 | **SSRF protection disabled** -- `JOAO_INSPECT_ALLOW_PRIVATE=true` in production startup. Inspector can reach internal networks. | REQ-0045 | Internal network scanning via JOAO | Should be false in production |
| G-06 | **Provenance not wired to superpowers** -- `record_intent()`/`record_outcome()` exist but superpowers.py never calls them. | REQ-0002,0003,0020 | Capability executions leave no audit trail | Violates core "every action auditable" claim |
| G-07 | **No undo executor** -- Undo recipes defined in spec; no code to execute them. | REQ-0004,0025,0059 | Actions cannot be reversed via API | Missing safety net for L3/L4 operations |
| G-08 | **No artifact path validation on download** -- `job_id` and `filename` from URL not validated against ARTIFACTS_DIR boundary. | REQ-0046 | Path traversal on read (arbitrary file read) | Pair with G-01 for full compromise |

## MEDIUM

| # | Gap | REQs | Blast Radius | Risk |
|---|-----|------|-------------|------|
| G-09 | **Only 3 of 10 capabilities implemented** -- Registry has tableau, playlist, general. Missing: git_scan, git_write, git_ship, context_build, ollama_generate, tunnel_status, file_ingest. | REQ-0015,0031,0034,0035,0060-0064 | Feature completeness at 30% | Expected per spec "Must Be Built" |
| G-10 | **No context pack builder** -- Storage helpers exist but no assembly logic. | REQ-0026-0030 | No context enrichment before capability execution | Phase 3 dependency |
| G-11 | **Multi-brain review uses paid APIs, not Ollama-first** -- QA pipeline uses Claude+GPT-4+Opus, not Ollama as spec requires. | REQ-0042 | Violates CLAUDE.md ALL-BRAIN PROTOCOL (Ollama first, free) | Cost and compliance issue |
| G-12 | **No JSONL file locking** -- Concurrent writes can interleave partial JSON lines. | REQ-0022 | Ledger corruption under load | Low probability on single-user system |
| G-13 | **No ledger tamper evidence** -- No hash chaining, no signatures. | REQ-0003 | Audit trail can be modified undetectably | Reduces trust in provenance |
| G-14 | **Unbounded artifact storage** -- No cleanup, TTL, or rotation for superpower_artifacts/. | REQ-0046 | Disk exhaustion over time | Operational risk |
| G-15 | **routers/joao.py is 2,390 lines** -- God-module handling dispatch, council, content pipeline, logging. | N/A | Maintenance burden, merge conflicts, cognitive load | Refactoring opportunity |
| G-16 | **Spine not running under systemd** -- Started from shell command, not joao-spine-local.service. No auto-restart. | REQ-0079 | Manual restart needed after crash | Reliability risk |

## LOW

| # | Gap | REQs | Blast Radius | Risk |
|---|-----|------|-------------|------|
| G-17 | **No rate limiting** on any endpoint. Relies entirely on Cloudflare. | N/A | Abuse if Cloudflare bypassed | Low if Cloudflare stays in path |
| G-18 | **Two dispatch instances** (gunicorn :8100 + uvicorn :7777). Unclear canonical. | N/A | Confusion, inconsistent behavior | Should consolidate |
| G-19 | **qwen3:8b missing from Ollama** -- Listed in spec but not installed. | REQ-0081 | Reduced multi-brain coverage | `ollama pull qwen3:8b` to fix |
| G-20 | **Hardcoded paths** throughout codebase (`/home/zamoritacr/...`). | N/A | Breaks on user/machine change | Use relative paths or env vars |
| G-21 | **No tests** -- No test directory, no pytest config, no CI. | REQ-0078 | Regressions undetected | Significant DX gap |
| G-22 | **Supabase client created per call** -- No connection pooling in ledgers.py. | N/A | Latency on every write | Performance optimization |
| G-23 | **No agent_callback endpoint** -- Spec describes /joao/agent_callback but it doesn't exist. | REQ-0041 | Agent results must be polled, not pushed | Affects dispatch latency |
| G-24 | **Redis running as ollama user** -- Unexpected permission scope. | N/A | Minor security hygiene | Should run as dedicated user |

---

**Total gaps: 24**
- CRITICAL: 3
- HIGH: 5
- MEDIUM: 8
- LOW: 8
