# Phase 5a: Gap Register

**Ranked by severity (CRITICAL > HIGH > MEDIUM > LOW) then blast radius.**
**Date:** 2026-04-11 (v2)

---

## CRITICAL (Exploitable Now)

| ID | Gap | REQs | Impact | Blast Radius |
|----|-----|------|--------|-------------|
| G-01 | **Path traversal in artifact_store.py:28** -- `save_upload()` uses user-supplied filename without sanitization. `../../etc/cron.d/evil` would write outside job dir. | REQ-0006, REQ-0050 | Arbitrary file write on server | Full server compromise |
| G-02 | **Path traversal in artifact download** -- `superpowers.py:186` passes user `filename` to `load_artifact()` without path validation. `../../.env` would leak secrets. | REQ-0050 | Arbitrary file read on server | Secret exposure |
| G-03 | **No auth on superpowers endpoints** -- `/joao/superpowers/tableau`, `/joao/superpowers/playlist`, all artifact endpoints are publicly accessible. | REQ-0047 | Unauthorized access to compute + data | Public API abuse |
| G-04 | **No upload size limit** -- FastAPI default allows unlimited upload size. | REQ-0006 | OOM crash, disk exhaustion | Service outage |

## HIGH (Safety Guarantees Missing)

| ID | Gap | REQs | Impact | Blast Radius |
|----|-----|------|--------|-------------|
| G-05 | **Autonomy levels defined but NOT enforced** -- No middleware checks autonomy before operation execution. | REQ-0005 to REQ-0008, REQ-0011, REQ-0013 | Any operation runs regardless of declared autonomy | Core safety model bypassed |
| G-06 | **Locks grantable but never checked** -- WRITE_LOCK/SHIP_LOCK can be granted and stored, but no code validates them before L3/L4 operations. | REQ-0007, REQ-0008, REQ-0011 | L3/L4 operations run without authorization | Unauthorized writes/deploys |
| G-07 | **No provenance on capability execution** -- Superpowers router does not call any provenance recording function after executing tableau/playlist. | REQ-0001, REQ-0002, REQ-0022 | Operations unauditable | Compliance/governance gap |
| G-08 | **CORS wildcard in production** -- `allow_origins=["*"]` allows any website to make API requests. | REQ-0047 | Cross-site request potential | Data exfiltration if auth cookies added |
| G-09 | **Only 3 of 10 spec capabilities implemented** -- Registry has tableau, playlist, general. Missing: git_scan, git_write, git_ship, context_build, ollama_generate, tunnel_status, file_ingest. | REQ-0015 | 7 capabilities unavailable | Spec promise unfulfilled |
| G-10 | **No sandbox enforcement at L2** -- Files can be written anywhere, not just superpower_artifacts/ and /tmp/joao-*. | REQ-0006, REQ-0050 | Uncontrolled file writes | Data integrity risk |

## MEDIUM (Functionality Gaps)

| ID | Gap | REQs | Impact | Blast Radius |
|----|-----|------|--------|-------------|
| G-11 | **No undo executor** -- Five undo types defined in spec YAML, but no code executes any of them. | REQ-0003, REQ-0025 | Operations not reversible | Manual cleanup required |
| G-12 | **No context pack builder** -- Spec defines rich context assembly (operating_rules, history, landmines, etc.) but no builder exists. | REQ-0027 to REQ-0031 | Capabilities run without context | Reduced quality |
| G-13 | **No capability chaining** -- Spec defines chains (e.g., file_ingest -> tableau -> git_write) but no chain resolver or executor exists. | REQ-0016 | Complex intents require manual multi-step | User experience gap |
| G-14 | **7 superpowers API endpoints missing** -- provenance, undo, git/scan, git/write, git/ship, tunnel/status, context/build. | REQ-0060 to REQ-0066 | API surface incomplete | Spec promise unfulfilled |
| G-15 | **QA pipeline uses paid APIs, not Ollama-first** -- `qa_pipeline.py` dispatches to Claude Sonnet + GPT-4o + Claude Opus. Spec mandates Ollama drafts first. | REQ-0046 | Unnecessary API cost | CLAUDE.md canon violation |
| G-16 | **No WU data classification** -- No code flags or prevents WU-internal data from reaching external APIs. | REQ-0048, REQ-0049 | WU data could leak to OpenAI/Anthropic | Compliance risk |
| G-17 | **No agent callback endpoint** -- Spec describes `/joao/agent_callback` but it does not exist. | REQ-0044 | Results must be polled, not pushed | Dispatch latency |
| G-18 | **Concurrent JSONL write risk** -- No file locking on provenance JSONL files. | REQ-0026 | Interleaved/corrupted entries under load | Data integrity |
| G-19 | **No provenance hash chaining** -- Ledger entries have no cryptographic linkage. | REQ-0028 | Tamper-evidence not guaranteed | Audit weakness |

## LOW (Operational/Cosmetic)

| ID | Gap | REQs | Impact | Blast Radius |
|----|-----|------|--------|-------------|
| G-20 | **Duplicate tmux sessions** -- 26 sessions, 12 are duplicates (upper+lowercase). | -- | Resource waste | Memory consumption |
| G-21 | **Two dispatch listeners on different ports** -- :7777 (uvicorn) and :8100 (gunicorn) both running. | -- | Confusion about which handles requests | Debugging difficulty |
| G-22 | **qwen3:8b model not installed** -- Referenced in CLAUDE.md but not pulled in Ollama. | REQ-0041 | Errors if qwen requested | Feature gap |
| G-23 | **166 ruff lint warnings** -- Unused imports, variables, etc. | -- | Code cleanliness | Developer experience |
| G-24 | **No automated smoke test runner** -- Smoke tests defined in YAML but no harness executes them. | REQ-0019 | Manual testing only | Regression risk |
| G-25 | **No artifact retention policy** -- Files accumulate indefinitely. | -- | Disk space exhaustion over time | Gradual degradation |
| G-26 | **Spec mentions 7 brains; code has 16 agents** -- Spec Section 7.1 lists 7 brains; actual system has 16 council agents. | REQ-0041 | Spec/reality mismatch | Documentation debt |
| G-27 | **No egress tracking in provenance** -- No record of which external APIs were called per operation. | REQ-0049 | Cannot audit external data flow | Compliance gap |
| G-28 | **Duplicate cloudflared tunnel to :7778** -- PIDs 1675 and 1854 both tunnel to same port. | -- | Resource waste | Minor |

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 4 |
| HIGH | 6 |
| MEDIUM | 9 |
| LOW | 9 |
| **Total** | **28** |
