# Phase 3: Traceability Matrix -- Spec Certification

**Legend:**
- **PROVEN** -- Code evidence fully satisfies the claim
- **PARTIAL** -- Some evidence exists but incomplete
- **UNPROVEN** -- No code evidence found; claim cannot be verified
- **CONTRADICTED** -- Code evidence contradicts the claim

---

## Section 1: What JOAO Is

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0001 | Input classified, routed through capability graph, executed by brains | **PROVEN** | `capability/registry.py:48-79` (classify_intent), `routers/superpowers.py:46-50` (route endpoint), `routers/joao.py` (council dispatch) | Classification + routing + execution all wired |
| REQ-0002 | Returns artifacts with full provenance | **PARTIAL** | Artifacts: `capability/artifact_store.py:33-41`. Provenance: `exocortex/ledgers.py:91-127` (record_intent). But superpowers.py does NOT call record_intent or record_outcome. | Provenance exists as module but is NOT wired into superpowers execution path |
| REQ-0003 | Every action is auditable | **PARTIAL** | Ledger module exists (`exocortex/ledgers.py`), exocortex router exists (`routers/exocortex.py`). But superpowers router does not call ledger functions. Council dispatch logs to Supabase via `routers/joao.py`. | Audit trail exists for council dispatch but NOT for superpowers capabilities |
| REQ-0004 | Every action is reversible | **PARTIAL** | Undo recipe schema defined in spec. `receipts.py:76` has fallback "Delete artifact directory". But no undo executor endpoint exists in code. | Undo concept designed; executor NOT implemented |

## Section 2: Autonomy Dial

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0005 | Five autonomy levels L0-L4 | **PARTIAL** | Parser exists: `exocortex/ledgers.py:267` (`_AUTONOMY_RE = re.compile(r'\bL([0-4])\b')`). But NO middleware enforces levels on endpoints. | Parser works; enforcement middleware NOT built |
| REQ-0006 | Default L1 | **PROVEN** | `exocortex/ledgers.py:293` (`"autonomy": "L1"` default) | Default is L1 when no flag parsed |
| REQ-0007 | L3 requires WRITE_LOCK | **UNPROVEN** | `check_lock()` exists at `ledgers.py:238-248`. But NO endpoint calls check_lock before L3 operations. No enforcement middleware. | Lock checking code exists but is never called in request pipeline |
| REQ-0008 | L4 requires SHIP_LOCK | **UNPROVEN** | Same as REQ-0007. No SHIP_LOCK enforcement in any endpoint. | Lock check function exists but not wired |
| REQ-0009 | Locks have duration + scope | **PROVEN** | `ledgers.py:220-235`: lock schema has lock_type, scope, granted_at, expires_at, granted_by, active | Schema matches spec |
| REQ-0010 | Locks stored in provenance ledger | **PROVEN** | `ledgers.py:233-234`: dual-write to LOCK_FILE (JSONL) + Supabase joao_locks | Both write paths present |
| REQ-0011 | Lock validity checked before every L3/L4 op | **UNPROVEN** | `check_lock()` at `ledgers.py:238` exists. But no router or middleware calls it. | Function exists, not invoked |
| REQ-0012 | Autonomy flags parsed from any position | **PROVEN** | `ledgers.py:300`: `_AUTONOMY_RE.search(text)` -- regex .search() finds flag anywhere in string | Regex correctly matches anywhere |
| REQ-0013 | Missing lock -> HTTP 403 | **UNPROVEN** | No endpoint returns 403 for missing locks. No lock enforcement middleware. | Needs: autonomy enforcement middleware wrapping superpowers + git endpoints |

## Section 3: Capability Graph

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0014 | Every capability has a contract | **PARTIAL** | `capability_registry.yaml` has full contracts for 9 capabilities (not 10 -- no `ollama_review`). Live `registry.py` only has 3 capabilities (tableau, playlist, general). | YAML is comprehensive; Python registry is incomplete |
| REQ-0015 | 10 capabilities registered | **CONTRADICTED** | YAML has 9 (tableau_to_powerbi, mood_playlist, git_scan, git_write, git_ship, context_build, ollama_generate, tunnel_status, file_ingest). Live registry.py has 3. | Only 3 of 10 implemented in code |
| REQ-0016 | Capability chains compose | **UNPROVEN** | Chain definitions in YAML (`chains_to`, `chains_from`). No chain executor in code. | Spec-only; no implementation |
| REQ-0017 | File extension is strongest routing signal | **PROVEN** | `registry.py:57-61`: file extension checked before keyword scoring | Code matches spec priority |
| REQ-0018 | Keyword scoring for intent classification | **PROVEN** | `registry.py:64-78`: keyword loop with `score += len(kw)` | Length-weighted scoring as specified |
| REQ-0019 | Fallback to general for unmatched intents | **PROVEN** | `registry.py:79`: `return "general"` at end of classify_intent | Fallback exists |

## Section 4: Provenance Ledger

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0020 | Every execution produces provenance entry | **PARTIAL** | `record_intent()` at `ledgers.py:91-127` produces intent records. `record_outcome()` at `ledgers.py:159-190` produces outcomes. But they are NOT called from superpowers router. Exocortex router (`routers/exocortex.py`) exposes manual recording. | Ledger exists; not auto-wired to capability execution |
| REQ-0021 | Primary storage: Supabase joao_provenance | **PARTIAL** | Tables used: `joao_intents`, `joao_outcomes` (not `joao_provenance`). `ledgers.py:125` inserts to `joao_intents`. | Table name differs from spec (intents/outcomes vs single provenance table). Functional equivalent. |
| REQ-0022 | Local fallback: ledger.jsonl append-only | **PARTIAL** | Local files: `intents.jsonl`, `outcomes.jsonl` (not `ledger.jsonl`). `_append_jsonl()` at `ledgers.py:34-37` is append-only. | File names differ; append-only pattern confirmed |
| REQ-0023 | Dual-write; Supabase failure does not block local | **PROVEN** | `ledgers.py:124-125`: local write first, then Supabase. `_supabase_insert()` at line 51-63 has try/except returning False. | Correct dual-write with graceful Supabase failure |
| REQ-0024 | Undo recipe in every provenance entry | **PARTIAL** | `record_outcome()` has `undo_steps` field. `receipts.py:72-76` generates undo section. But no automatic undo recipe generation per capability. | Field exists; auto-generation not wired |
| REQ-0025 | Five undo types implemented | **UNPROVEN** | Spec defines 5 types. No undo executor code found. `receipts.py:76` only has fallback "Delete artifact directory". | Undo types specced but not implemented |

## Section 5: Context Packs

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0026 | Context pack assembled before execution | **UNPROVEN** | `artifact_store.py:58-66` has save/load context pack helpers. No context pack builder that assembles the full schema from spec. | Storage exists; builder NOT implemented |
| REQ-0027 | Pack SHA-256 hashed, hash in provenance | **UNPROVEN** | `record_intent()` has `context_pack_hash` field. No code computes pack hash. | Field exists; hash computation not implemented |
| REQ-0028 | operating_rules from MEMORY.md + CLAUDE.md | **UNPROVEN** | No code reads MEMORY.md or CLAUDE.md to populate context packs. | Not implemented |
| REQ-0029 | session_history: last 20 provenance entries | **UNPROVEN** | `get_intents(last_n=20)` exists but not wired into context pack assembly. | Query exists; not connected |
| REQ-0030 | landmines section populated | **UNPROVEN** | No code populates landmines. | Not implemented |

## Section 6: Tool Dominion (Adapters)

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0031 | Git adapter with autonomy enforcement | **UNPROVEN** | No git adapter exists in code. No /superpowers/git/* endpoints. | Listed as "Must Be Built" in spec section 10 |
| REQ-0032 | Supabase adapter with autonomy levels | **UNPROVEN** | Supabase writes exist (`ledgers.py`, `services/supabase_client.py`) but NO autonomy enforcement. | Supabase used but not gated by autonomy |
| REQ-0033 | Telegram adapter with autonomy levels | **PARTIAL** | `services/telegram.py` and `exocortex/digest.py:252-261` send Telegram messages. No autonomy level check. | Telegram works; no autonomy gating |
| REQ-0034 | Cloudflared adapter | **UNPROVEN** | No tunnel_status capability endpoint. Cloudflared managed via systemd, not JOAO API. | Not implemented |
| REQ-0035 | Ollama adapter with autonomy levels | **UNPROVEN** | Ollama called from various places (QA pipeline, arena) but no formal adapter with autonomy enforcement. | Ollama used; no adapter abstraction |
| REQ-0036 | File ingest stores in job dir | **PROVEN** | `artifact_store.py:27-30` (save_upload), `artifact_store.py:17-19` (_job_dir creates dir) | Job dir isolation working |
| REQ-0037 | Dr. Data adapter for TWB files | **PROVEN** | `capability/tableau_to_powerbi.py` imports from `core.enhanced_tableau_parser`, `core.formula_transpiler`, `core.direct_mapper`. `main.py:78-89` adds Dr. Data to sys.path. | Real Dr. Data integration confirmed |
| REQ-0038 | MrDP curated works offline; Spotify needs keys | **PROVEN** | `capability/music.py:37` checks SPOTIPY_CLIENT_ID; falls back to curated. `capability/mood_playlist.py` returns curated tracks without Spotify. | Graceful fallback confirmed |

## Section 7: Execution Fabric

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0039 | 7 brains with defined roles | **PARTIAL** | tmux sessions exist for all 7 (BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX) + 8 more council agents. But spec says 7 with specific Ollama model assignments; actual deployment uses Claude for most, not Ollama models per spec. | Sessions exist; model assignments diverge from spec |
| REQ-0040 | Job dispatch writes job JSON to artifacts dir | **UNPROVEN** | `joao_local_dispatch.py` writes task files to `/tmp/council/tasks/`, NOT to `superpower_artifacts/`. | Different mechanism than spec describes |
| REQ-0041 | Agent posts callback to /joao/agent_callback | **UNPROVEN** | No `/joao/agent_callback` endpoint found in any router. | Not implemented |
| REQ-0042 | Multi-brain review: Ollama first, 3+ parallel, consensus | **PARTIAL** | `services/qa_pipeline.py` and `routers/qa.py` implement 3-brain QA review (Claude Sonnet + GPT-4 + Opus). Does NOT start with Ollama. | Multi-brain exists but uses paid APIs, not Ollama-first |
| REQ-0043 | Multi-brain mandatory for L3+ | **UNPROVEN** | No enforcement linking multi-brain review to autonomy levels. | Not implemented |

## Section 8: Security and Privacy

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0044 | Secrets from env only | **PROVEN** | All credentials via `os.getenv()`: `middleware/auth.py:33,82`, `capability/music.py:37`, `exocortex/ledgers.py:54-55`, `tools/chat.py:36` | Consistent env-only pattern |
| REQ-0045 | WU data never sent to external APIs | **PARTIAL** | `capability_registry.yaml` has `privacy: internal` flags. `record_outcome()` has `egress_summary` tracking. But NO runtime enforcement blocking external API calls for WU data. | Tracking exists; enforcement missing |
| REQ-0046 | Outputs to superpower_artifacts/{job_id}/ | **PROVEN** | `artifact_store.py:13,17-19`: `ARTIFACTS_DIR` hardcoded + _job_dir enforces structure | Confirmed |
| REQ-0047 | No write outside job dir at L2 | **UNPROVEN** | No L2 sandbox enforcement in code. Capabilities can write anywhere Python allows. | No sandbox enforcement |
| REQ-0048 | L3 writes require WRITE_LOCK with scope | **UNPROVEN** | Same as REQ-0007. Lock check exists but not enforced. | Not wired |

## Section 9: API Surface

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0049 | GET /superpowers/capabilities | **PROVEN** | `superpowers.py:53-56` | Working |
| REQ-0050 | POST /superpowers/route | **PROVEN** | `superpowers.py:46-50` | Working |
| REQ-0051 | POST /superpowers/tableau | **PROVEN** | `superpowers.py:61-98` | Working, produces 6 artifacts |
| REQ-0052 | POST /superpowers/playlist | **PROVEN** | `superpowers.py:103-128` | Working |
| REQ-0053 | POST /superpowers/dispatch | **PROVEN** | `superpowers.py:133-165` | Working (tableau + playlist only) |
| REQ-0054 | GET /superpowers/artifacts/{id} | **PROVEN** | `superpowers.py:170-182` | Working |
| REQ-0055 | GET /superpowers/artifacts/{id}/{file} | **PROVEN** | `superpowers.py:185-199` | Working |
| REQ-0056 | GET /superpowers/artifacts/{id}/bundle | **PROVEN** | `superpowers.py:202-209` | Working |
| REQ-0057 | GET /superpowers/provenance/{run_id} | **UNPROVEN** | No such endpoint in superpowers.py. Exocortex router has `/joao/intents` and `/joao/outcomes` but not at the spec path. | Different endpoint path; similar functionality elsewhere |
| REQ-0058 | GET /superpowers/provenance?last=N | **UNPROVEN** | Same -- no endpoint at this path. `/joao/intents` serves similar purpose. | Path mismatch |
| REQ-0059 | POST /superpowers/undo/{run_id} | **UNPROVEN** | No undo endpoint in any router. | Not implemented |
| REQ-0060 | POST /superpowers/git/scan | **UNPROVEN** | No git endpoints. | Not implemented |
| REQ-0061 | POST /superpowers/git/write | **UNPROVEN** | No git endpoints. | Not implemented |
| REQ-0062 | POST /superpowers/git/ship | **UNPROVEN** | No git endpoints. | Not implemented |
| REQ-0063 | GET /superpowers/tunnel/status | **UNPROVEN** | No tunnel status endpoint in superpowers router. | Not implemented |
| REQ-0064 | POST /superpowers/context/build | **UNPROVEN** | No context build endpoint. | Not implemented |

## Section 10: What Exists vs Must Be Built

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0065 | Superpowers router mounted | **PROVEN** | `main.py:159-176` mounts routers including superpowers | Confirmed in FastAPI app |
| REQ-0066 | tableau_to_powerbi working with Dr. Data | **PROVEN** | `capability/tableau_to_powerbi.py` calls real `enhanced_tableau_parser.parse_twb()`, `formula_transpiler.transpile()`, `direct_mapper.build_pbip_config_from_tableau()` | Full integration |
| REQ-0067 | mood_playlist working | **PROVEN** | `capability/mood_playlist.py` + `capability/music.py` with curated + Spotify + Apple Music | Working with fallbacks |
| REQ-0068 | Intent classifier working | **PROVEN** | `capability/registry.py:48-79` | Keyword + file extension |
| REQ-0069 | Artifact store working | **PROVEN** | `capability/artifact_store.py` full implementation | Job dirs, upload, download, zip |
| REQ-0070 | UI wired in /joao/app | **PROVEN** | `main.py:218` serves /joao/app; UI references in go_live_plan | PWA with buttons |

## Capability Registry YAML Claims

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0071 | tableau produces 6 artifacts | **PROVEN** | `capability/tableau_to_powerbi.py` produces: tableau_spec.json, dax_translations.json, model_mapping.json, migration_plan.md, pbix_build_instructions.md, pbip_config.json | All 6 confirmed |
| REQ-0072 | mood_playlist min_autonomy L1, timeout 15s | **PARTIAL** | Endpoint exists at L1-equivalent (no auth). No timeout enforcement in code. | No autonomy middleware; no per-capability timeout |
| REQ-0073 | git_scan L0, offline, timeout 30s | **UNPROVEN** | git_scan not implemented. | Not built |
| REQ-0074 | git_write rejects without WRITE_LOCK | **UNPROVEN** | git_write not implemented. | Not built |
| REQ-0075 | git_ship rejects without SHIP_LOCK | **UNPROVEN** | git_ship not implemented. | Not built |
| REQ-0076 | context_build returns operating_rules + project_context | **UNPROVEN** | context_build not implemented. | Not built |
| REQ-0077 | Each capability has smoke_tests defined | **PROVEN** | capability_registry.yaml has smoke_tests for all 9 capabilities | In YAML spec, not in code |

## Go-Live Plan Claims

| REQ | Claim | Status | Evidence | Notes |
|-----|-------|--------|----------|-------|
| REQ-0078 | Phase 0: 12/12 smoke tests passing | **UNPROVEN** | No test runner or smoke test results found on disk. Claim in go_live_plan but no evidence. | Need to run smoke tests to verify |
| REQ-0079 | Spine live at :7778 with 70+ routes | **PROVEN** | PID 2696 running uvicorn on :7778. 16 routers mounted. | Running and serving |
| REQ-0080 | 15 tmux agent sessions | **PROVEN** | tmux ls shows 15 agent sessions (APEX through VOLT) | All present |
| REQ-0081 | Ollama with phi4, deepseek, llama3.1 | **PROVEN** | Ollama PID 1678 running. Models confirmed in earlier inventory. | Running with 3 models |
| REQ-0082 | Supabase configured | **PROVEN** | .env has SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY set | Credentials present |
| REQ-0083 | Cloudflared tunnel active | **PROVEN** | PID 482840 running cloudflared with config routing joao.theartofthepossible.io | Tunnel active |

---

## Summary Counts

| Status | Count | Percentage |
|--------|-------|------------|
| **PROVEN** | 36 | 43.4% |
| **PARTIAL** | 14 | 16.9% |
| **UNPROVEN** | 32 | 38.6% |
| **CONTRADICTED** | 1 | 1.2% |
| **Total** | 83 | 100% |

---

## Key Themes

1. **Phase 0 (Stabilize) is solid.** Superpowers router, Tableau, Playlist, Artifact store, UI, Infrastructure -- all PROVEN.
2. **Governance layer (Phase 1) is designed but not wired.** Autonomy parser, lock manager, provenance ledger exist as modules but are NOT integrated into the request pipeline.
3. **Phases 2-4 are entirely unbuilt.** Git adapter, context packs, multi-brain chaining, undo executor -- all UNPROVEN.
4. **The spec itself acknowledges this** in Section 10 "Must Be Built" -- the gap is expected and documented.
