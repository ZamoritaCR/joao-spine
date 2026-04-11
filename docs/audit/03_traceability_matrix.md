# Phase 3: Traceability Matrix -- Spec Certification

**Audit date:** 2026-04-11 (v2)
**Codebase:** `/home/zamoritacr/joao-spine` @ `a3e72ea`
**Spec:** `JOAO_OS_DESIGN.md` v2.0

**Legend:**
- **PROVEN** -- Code evidence fully satisfies the claim
- **PARTIAL** -- Some evidence exists but incomplete
- **UNPROVEN** -- No code evidence found
- **CONTRADICTED** -- Code contradicts the claim

---

## Section 1: What JOAO Is

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0001 | Input -> classify -> route -> execute -> artifacts + provenance | **PARTIAL** | `capability/registry.py`, `routers/superpowers.py` | registry:48-78, superpowers:46-51 | Classify + route + execute works. Provenance NOT auto-recorded per execution. |
| REQ-0002 | Every action is auditable | **PARTIAL** | `exocortex/ledgers.py`, `services/supabase_client.py` | ledgers:80-130, supabase_client:40-80 | Ledger and Supabase modules exist. But superpowers router does NOT call ledger on execution. |
| REQ-0003 | Every action is reversible | **PARTIAL** | `capability_registry.yaml` | yaml:82,151,214,279,341 | Undo strategies defined in spec YAML. No undo executor code exists. |

---

## Section 2: Autonomy Dial

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0004 | Five autonomy levels L0-L4 | **PARTIAL** | `exocortex/ledgers.py` | 95, 288-320 | L0-L3 referenced in ledger code. L4 not explicitly handled in code (only L0-L3 parsed). Spec defines L0-L4 but code only handles L0-L3. |
| REQ-0005 | L0: capture, log, classify only | **UNPROVEN** | -- | -- | No middleware enforces L0 behavior. Any request can trigger execution. |
| REQ-0006 | L2: write ONLY to sandbox dirs | **UNPROVEN** | `capability/artifact_store.py` | 13-14, 28-29 | Artifact store writes to superpower_artifacts/ but NO enforcement prevents writes elsewhere. `save_upload()` uses user-supplied filename with no path traversal protection. |
| REQ-0007 | L3 requires WRITE_LOCK | **UNPROVEN** | `exocortex/ledgers.py`, `routers/exocortex.py` | ledgers:265-270, exocortex:265-267 | Lock GRANT exists. Lock CHECK before operations does NOT exist. No middleware validates locks. |
| REQ-0008 | L4 requires SHIP_LOCK | **UNPROVEN** | Same as REQ-0007 | Same | SHIP_LOCK can be granted but never checked before any operation. |
| REQ-0009 | Default autonomy is L1 | **PROVEN** | `exocortex/ledgers.py` | 95, 293 | `autonomy_level: str = "L1"` default in record_intent(); `"autonomy": "L1"` in parse_flags(). |
| REQ-0010 | Locks stored in provenance ledger | **PROVEN** | `exocortex/ledgers.py` | 225-260 | `grant_lock()` writes to `provenance/locks.jsonl` with dual-write to Supabase. |
| REQ-0011 | Lock checked before every L3/L4 op | **UNPROVEN** | -- | -- | No middleware or decorator checks lock validity before any operation. |
| REQ-0012 | Autonomy parsed from input text | **PROVEN** | `exocortex/ledgers.py` | 288-320 | `parse_flags(text)` extracts L0-L3 patterns, WRITE_LOCK/SHIP_LOCK, EDGE/CORE/DISABLED. |
| REQ-0013 | Missing lock -> request and wait | **UNPROVEN** | -- | -- | No code returns 403 or prompts for lock when missing. |

---

## Section 3: Capability Graph

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0014 | Capabilities defined by contract schema | **PARTIAL** | `capability/registry.py`, `capability_registry.yaml` | registry:13-44 | Code defines name, keywords, file_extensions, default_agent. YAML has full schema. Code does NOT use YAML contracts -- it has its own simpler dict. |
| REQ-0015 | 10 capabilities registered | **CONTRADICTED** | `capability/registry.py` | 13-44 | Only 3 registered: tableau_to_powerbi, mood_playlist, general. Spec claims 10 (also git_scan, git_write, git_ship, context_build, ollama_generate, tunnel_status, file_ingest). |
| REQ-0016 | Capability chains compose | **UNPROVEN** | -- | -- | No chain resolver or executor exists in code. |
| REQ-0017 | Router priority: file ext > explicit > keyword > chain > fallback | **PARTIAL** | `capability/registry.py` | 56-78 | File extension (priority 1) and keyword scoring (priority 3) implemented. Explicit capability name and chain inference NOT implemented. |
| REQ-0018 | Tiebreaker: offline > minimal risk > latency > enterprise | **UNPROVEN** | -- | -- | No tiebreaker logic exists. Highest keyword score wins. |
| REQ-0019 | Each capability has smoke_tests | **PARTIAL** | `capability_registry.yaml` | Varies | Smoke tests defined in YAML for all capabilities. Code does NOT use them -- no test runner. |
| REQ-0020 | Each capability has undo_strategy | **PARTIAL** | `capability_registry.yaml` | Varies | Undo strategies defined in YAML. No undo executor in code. |
| REQ-0021 | Each capability has default_brain | **PROVEN** | `capability/registry.py` | 23,35,43 | BYTE, ARIA, CJ assigned as default_agent per capability. |

---

## Section 4: Provenance Ledger

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0022 | Every execution produces provenance entry | **UNPROVEN** | `routers/superpowers.py` | 61-98, 103-128 | Superpowers tableau/playlist endpoints do NOT call any provenance recording function. |
| REQ-0023 | Provenance schema has all listed fields | **PARTIAL** | `exocortex/ledgers.py` | 80-130 | Intent record has: intent_id, timestamp, raw_input, parsed_intent, autonomy_level, capability_chain, chosen_brains, definition_of_done. Missing from spec: run_id format, tools_called, files_written, context_pack_hash, undo_recipe, routing_decision. |
| REQ-0024 | Dual storage: Supabase + local JSONL | **PROVEN** | `exocortex/ledgers.py` | 40-75 | `_dual_write()` writes to local file + Supabase. Supabase failure logged, does not block local. |
| REQ-0025 | Five undo types implemented | **UNPROVEN** | -- | -- | delete_artifacts, git_revert, git_delete_branch, noop, service_rollback -- NONE have execution code. Only defined as strings in YAML. |
| REQ-0026 | Local fallback is append-only JSONL | **PROVEN** | `exocortex/ledgers.py` | 44-50 | Files opened with `"a"` (append) mode. No delete/update functions exist. |

---

## Section 5: Context Packs

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0027 | Context pack schema with all sections | **UNPROVEN** | -- | -- | No context pack builder exists. `artifact_store.save_context_pack()` saves arbitrary dict but no builder populates the spec schema. |
| REQ-0028 | Pack hashed SHA-256 in provenance | **UNPROVEN** | -- | -- | No hashing logic for context packs. |
| REQ-0029 | operating_rules from MEMORY.md + CLAUDE.md | **UNPROVEN** | -- | -- | No code reads MEMORY.md or CLAUDE.md to build rules section. |
| REQ-0030 | session_history: last 20 provenance entries | **UNPROVEN** | -- | -- | No code queries ledger for recent entries to populate history. |
| REQ-0031 | landmines: hardcoded + CLAUDE.md bugs | **UNPROVEN** | -- | -- | No landmine collection code. |

---

## Section 6: Tool Dominion (Adapters)

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0032 | Git adapter with autonomy enforcement | **UNPROVEN** | -- | -- | No git adapter module exists in capability/ or services/. |
| REQ-0033 | Supabase adapter with autonomy enforcement | **PARTIAL** | `services/supabase_client.py` | 1-139 | Supabase read/write works. No autonomy checks on operations. |
| REQ-0034 | Telegram adapter with autonomy enforcement | **PARTIAL** | `services/telegram.py` | 1-68 | Telegram send works. No autonomy level check. |
| REQ-0035 | Cloudflared adapter with autonomy enforcement | **UNPROVEN** | -- | -- | No cloudflared adapter module. Hub router shows tunnel info but no adapter abstraction. |
| REQ-0036 | Ollama adapter with autonomy enforcement | **PARTIAL** | `services/brain_manager.py` | 1-224 | Brain manager talks to Ollama (generate, chat, consensus). No autonomy gating. |
| REQ-0037 | File ingest adapter with autonomy enforcement | **PARTIAL** | `routers/ingest.py` | 1-388 | File ingest router accepts uploads, routes to processors. No autonomy checks. |
| REQ-0038 | Dr. Data adapter with autonomy enforcement | **PARTIAL** | `capability/tableau_to_powerbi.py` | 1-427 | Tableau parsing/transpiling works. No autonomy enforcement. |
| REQ-0039 | SUPABASE_SERVICE_ROLE_KEY never logged/in artifacts/in prompts | **PROVEN** | `services/supabase_client.py` | 15-20 | Key read from `os.environ.get("SUPABASE_SERVICE_ROLE_KEY")` only. Grep confirms key value never appears in logs or artifacts. |
| REQ-0040 | Curated playlists work offline; Spotify degrades | **PROVEN** | `capability/mood_playlist.py`, `capability/music.py` | playlist:40-100, music:50-140 | Curated tracks hardcoded. Spotify adapter catches connection errors and falls back. |

---

## Section 7: Execution Fabric

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0041 | 7 brains with roles | **PARTIAL** | `capability/registry.py`, `joao_local_dispatch.py` | registry:23,35,43; dispatch:30-50 | 3 brains referenced in registry (BYTE, ARIA, CJ). Dispatch knows all 16 council agents. Spec's 7-brain model doesn't match the 16-agent reality. |
| REQ-0042 | Full dispatch lifecycle: route -> brain -> job JSON -> tmux -> result -> callback -> provenance | **PARTIAL** | `joao_local_dispatch.py`, `services/dispatch.py` | dispatch:120-200 | Dispatch sends to tmux. No job JSON written to artifacts. No callback endpoint. No provenance on completion. |
| REQ-0043 | Job JSON at superpower_artifacts/{job_id}/job_{BRAIN}.json | **UNPROVEN** | -- | -- | No code writes job_{BRAIN}.json files. |
| REQ-0044 | Brain POSTs callback to /joao/agent_callback | **UNPROVEN** | -- | -- | No `/joao/agent_callback` endpoint exists in any router. |
| REQ-0045 | Multi-brain review mandatory for L3+ | **UNPROVEN** | -- | -- | QA pipeline (`services/qa_pipeline.py`) exists for code review but is NOT wired to L3+ dispatch. |
| REQ-0046 | Ollama drafts first (free, local) | **UNPROVEN** | `services/qa_pipeline.py` | 50-100 | QA pipeline uses Claude Sonnet + GPT-4o + Claude Opus. Ollama NOT in the review chain. |

---

## Section 8: Security and Privacy

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0047 | All 5 secrets from env vars | **PROVEN** | `services/supabase_client.py`, `services/dispatch.py`, `services/telegram.py`, `routers/voice.py`, `mcp_server.py` | Various | All secrets sourced via `os.environ.get()` or `os.getenv()`. Grep confirms zero hardcoded secrets. |
| REQ-0048 | WU data never to external APIs | **UNPROVEN** | -- | -- | No data classification or WU-flag exists. Any data can reach OpenAI/Anthropic via chat/ingest endpoints. |
| REQ-0049 | Provenance flags external API usage | **UNPROVEN** | -- | -- | No egress tracking in provenance. |
| REQ-0050 | No write outside job dir at L2 | **UNPROVEN** | `capability/artifact_store.py` | 28 | `save_upload(job_id, filename, content)` uses filename directly -- path traversal via `../../` not blocked. No L2 sandbox enforcement. |
| REQ-0051 | L3 writes require WRITE_LOCK with scope | **UNPROVEN** | -- | -- | No scope-checked write gating. |
| REQ-0052 | SSH key contents never read | **PROVEN** | `services/dispatch.py` | 60-80 | SSH key used via asyncssh for transport only. Key contents written to temp file with 0600 perms, never logged. |

---

## Section 9: API Surface

| REQ | Claim | Status | Evidence File(s) | Lines | Explanation |
|-----|-------|--------|-------------------|-------|-------------|
| REQ-0053 | Endpoints under /joao/superpowers/ on :7778 | **PROVEN** | `routers/superpowers.py` | 22 | `router = APIRouter(prefix="/joao", tags=["superpowers"])` |
| REQ-0054 | GET /superpowers/capabilities | **PROVEN** | `routers/superpowers.py` | 53-56 | Returns `registry.list_capabilities()` |
| REQ-0055 | POST /superpowers/route | **PROVEN** | `routers/superpowers.py` | 46-50 | Returns `registry.route(text, filename)` |
| REQ-0056 | POST /superpowers/tableau | **PROVEN** | `routers/superpowers.py` | 61-98 | Accepts TWB/TWBX upload, returns 6 artifacts |
| REQ-0057 | POST /superpowers/playlist | **PROVEN** | `routers/superpowers.py` | 103-128 | Accepts feelings, returns playlist JSON |
| REQ-0058 | POST /superpowers/dispatch | **PROVEN** | `routers/superpowers.py` | 133-165 | Routes to tableau or playlist by capability key |
| REQ-0059 | GET /superpowers/artifacts/{id} | **PROVEN** | `routers/superpowers.py` | 170-182 | Lists job artifacts |
| REQ-0060 | GET /superpowers/provenance/{run_id} | **UNPROVEN** | -- | -- | Endpoint does not exist |
| REQ-0061 | POST /superpowers/undo/{run_id} | **UNPROVEN** | -- | -- | Endpoint does not exist |
| REQ-0062 | POST /superpowers/git/scan | **UNPROVEN** | -- | -- | Endpoint does not exist |
| REQ-0063 | POST /superpowers/git/write | **UNPROVEN** | -- | -- | Endpoint does not exist |
| REQ-0064 | POST /superpowers/git/ship | **UNPROVEN** | -- | -- | Endpoint does not exist |
| REQ-0065 | GET /superpowers/tunnel/status | **UNPROVEN** | -- | -- | Endpoint does not exist |
| REQ-0066 | POST /superpowers/context/build | **UNPROVEN** | -- | -- | Endpoint does not exist |

---

## Section 10: What Exists vs What Must Be Built

| REQ | Claim | Status | Evidence |
|-----|-------|--------|----------|
| REQ-0067 | Superpowers router mounted | **PROVEN** | `main.py` includes superpowers router; `routers/superpowers.py` exists |
| REQ-0068 | tableau_to_powerbi: real Dr. Data | **PROVEN** | `capability/tableau_to_powerbi.py` imports `core.enhanced_tableau_parser`, `core.formula_transpiler`, `core.direct_mapper` |
| REQ-0069 | mood_playlist: curated + Spotify | **PROVEN** | `capability/mood_playlist.py` + `capability/music.py` with Spotify and curated adapters |
| REQ-0070 | Registry with intent classification | **PROVEN** | `capability/registry.py:48-78` keyword + file extension classification |
| REQ-0071 | Artifact store with job-based storage | **PROVEN** | `capability/artifact_store.py` stores per job_id |
| REQ-0072 | UI wired at /joao/app | **PROVEN** | `main.py` mounts static files; `/static/` directory serves UI |
| REQ-0073 | Must build: autonomy middleware | **CONFIRMED UNBUILT** | No autonomy middleware in `middleware/` or router decorators |
| REQ-0074 | Must build: lock manager | **CONFIRMED UNBUILT** | Lock grant exists; lock check/enforcement does not |
| REQ-0075 | Must build: provenance ledger integration | **CONFIRMED UNBUILT** | Ledger modules exist; not called by superpowers router |
| REQ-0076 | Must build: context pack builder | **CONFIRMED UNBUILT** | No builder code |
| REQ-0077 | Must build: git adapter | **CONFIRMED UNBUILT** | No git adapter module |
| REQ-0078 | Must build: tunnel status adapter | **CONFIRMED UNBUILT** | No tunnel adapter module |
| REQ-0079 | Must build: Ollama multi-brain review | **CONFIRMED UNBUILT** | QA pipeline uses paid APIs, not Ollama-first |
| REQ-0080 | Must build: undo executor | **CONFIRMED UNBUILT** | No undo executor code |
| REQ-0081 | Must build: capability chaining engine | **CONFIRMED UNBUILT** | No chain engine |

---

## Capability Registry Requirements

| REQ | Claim | Status | Evidence |
|-----|-------|--------|----------|
| REQ-0082 | tableau_to_powerbi: 6 artifacts | **PROVEN** | `capability/tableau_to_powerbi.py` produces: tableau_spec.json, model_mapping.json, dax_translations.json, migration_plan.md, pbix_build_instructions.md, pbip_config.json |
| REQ-0083 | Smoke: parse DigitalAds TWBX | **PARTIAL** | Endpoint exists; no automated smoke test runner |
| REQ-0084 | Smoke: reject non-TWB -> 400 | **PROVEN** | `routers/superpowers.py:76-77` checks `.twb`/`.twbx` extension |
| REQ-0085 | Smoke: stressed->focused playlist | **PARTIAL** | Endpoint exists; no automated smoke test |
| REQ-0086 | Playlist undo: noop | **PROVEN** | Playlist generates data only, no side effects to undo |
| REQ-0087 | Git scan: status per repo | **UNPROVEN** | No git scan endpoint |
| REQ-0088 | Git write: reject without WRITE_LOCK | **UNPROVEN** | No git write endpoint |
| REQ-0089 | Git ship: reject without SHIP_LOCK | **UNPROVEN** | No git ship endpoint |
| REQ-0090 | Tunnel: name + hostnames | **UNPROVEN** | No tunnel status endpoint |

---

## Summary Counts

| Status | Count | % |
|--------|-------|---|
| PROVEN | 28 | 31.1% |
| PARTIAL | 22 | 24.4% |
| UNPROVEN | 38 | 42.2% |
| CONTRADICTED | 1 | 1.1% |
| CONFIRMED UNBUILT | 9 (subset of spec's "Must Be Built") | -- |
| **Total** | **90** (excl. confirmed unbuilt) | 100% |

**Note:** 9 "CONFIRMED UNBUILT" items (REQ-0073 through REQ-0081) are correctly acknowledged in spec Section 10 as "Must Be Built." They are not failures -- they are known scope. The remaining UNPROVEN items (38) are claims the spec makes about existing behavior that cannot be verified from code.
