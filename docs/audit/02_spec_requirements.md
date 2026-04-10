# Phase 2: Spec Requirements (Atomic Testable Statements)

**Source:** `JOAO_OS_DESIGN.md` v2.0 + `capability_registry.yaml` + `go_live_plan.md`
**Extraction date:** 2026-04-10

---

## Section 1: What JOAO Is

| REQ | Spec Section | Claim (<=25 words) | Acceptance Criteria |
|-----|-------------|---------------------|---------------------|
| REQ-0001 | 1 | Any input enters, gets classified, routed through a capability graph, executed by brains | Intent classification endpoint exists; routing returns capability + agent |
| REQ-0002 | 1 | Returns shipped artifacts with full provenance | Capability execution produces artifacts + provenance entry |
| REQ-0003 | 1 | Every action is auditable | Provenance ledger records every operation with timestamps, inputs, outputs |
| REQ-0004 | 1 | Every action is reversible | Undo recipes exist for each provenance entry; undo executor exists |

## Section 2: Autonomy Dial

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0005 | 2 | Five autonomy levels L0-L4 with defined permissions | Autonomy parser recognizes L0-L4; middleware enforces per-level restrictions |
| REQ-0006 | 2 | Default autonomy is L1 unless explicitly set | Parser returns L1 when no flag present in input |
| REQ-0007 | 2 | L3 requires WRITE_LOCK | L3 operations rejected without active WRITE_LOCK |
| REQ-0008 | 2 | L4 requires SHIP_LOCK | L4 operations rejected without active SHIP_LOCK |
| REQ-0009 | 2 | Locks have duration, scope (repo, capability, path, service, env) | Lock schema includes duration + scope fields; expiry is enforced |
| REQ-0010 | 2 | Locks stored in provenance ledger | Locks written to JSONL + Supabase |
| REQ-0011 | 2 | JOAO checks lock validity before every L3/L4 operation | Middleware calls check_lock() before L3/L4 execution |
| REQ-0012 | 2 | Autonomy flags parsed from any position in user input | Parser extracts L-flag from "migrate this TWB L2" |
| REQ-0013 | 2 | Missing lock -> JOAO requests lock and waits | HTTP 403 returned with lock requirement message |

## Section 3: Capability Graph

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0014 | 3.1 | Every capability defined by a contract with required fields | capability_registry.yaml has contracts with all schema fields |
| REQ-0015 | 3.2 | 10 capabilities registered (tableau_to_powerbi through tunnel_status) | Registry lists all 10 capabilities |
| REQ-0016 | 3.3 | Capability chains compose for complex intents | Chain executor runs multi-step capability sequences |
| REQ-0017 | 3.4 | File extension match is strongest routing signal | Router checks file ext before keyword scoring |
| REQ-0018 | 3.4 | Keyword scoring used for intent classification | Router counts keyword matches weighted by length |
| REQ-0019 | 3.4 | Fallback to ollama_generate for unmatched intents | Unmatched intents route to "general" capability |

## Section 4: Provenance Ledger

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0020 | 4.1 | Every execution produces exactly one provenance entry with specified schema | Provenance record has run_id, timestamp, intent, autonomy, tools, artifacts, outcome |
| REQ-0021 | 4.2 | Primary storage: Supabase table joao_provenance | Supabase insert for provenance exists |
| REQ-0022 | 4.2 | Local fallback: ledger.jsonl append-only | Local JSONL write always attempted |
| REQ-0023 | 4.2 | Both write paths always attempted; Supabase failure does not block local | Dual-write logic with try/except on Supabase |
| REQ-0024 | 4.3 | Every provenance entry includes an undo recipe | Outcome records include undo_steps field |
| REQ-0025 | 4.3 | Five undo types: delete_artifacts, git_revert, git_delete_branch, noop, service_rollback | Undo executor handles all five types |

## Section 5: Context Packs

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0026 | 5.1 | Context pack assembled before capability execution with specified schema | Context pack builder produces JSON with required sections |
| REQ-0027 | 5.2 | Pack is SHA-256 hashed and hash stored in provenance | Hash computed on pack; hash stored in intent record |
| REQ-0028 | 5.2 | operating_rules always included from MEMORY.md + CLAUDE.md | Context builder reads memory + CLAUDE.md |
| REQ-0029 | 5.2 | session_history: last 20 provenance entries | Context includes recent provenance entries |
| REQ-0030 | 5.2 | landmines section populated with known dangers | Hardcoded landmines + CLAUDE.md bugs included |

## Section 6: Tool Dominion (Adapters)

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0031 | 6.1 | Git adapter: reads at L0+, writes at L3 with WRITE_LOCK, ships at L4 with SHIP_LOCK | Git endpoints enforce autonomy levels |
| REQ-0032 | 6.2 | Supabase adapter: reads at L0+, writes at L2+, schema at L3 with WRITE_LOCK | Supabase operations check autonomy |
| REQ-0033 | 6.3 | Telegram adapter: notify at L1+, deliver at L2+ | Telegram calls check autonomy level |
| REQ-0034 | 6.4 | Cloudflared adapter: status at L0+, restart at L4 with SHIP_LOCK | Tunnel status read-only; restart behind lock |
| REQ-0035 | 6.5 | Ollama adapter: list at L0+, generate at L1+, review at L2+ | Ollama endpoints check autonomy |
| REQ-0036 | 6.6 | File ingest stores in job dir; metadata extracted | Upload saves to job dir; file type/size reported |
| REQ-0037 | 6.7 | Dr. Data adapter: parse/transpile/map/generate for TWB files | Direct Python import of Dr. Data core modules |
| REQ-0038 | 6.8 | MrDP adapter: curated playlists work offline; Spotify needs API keys | Curated works without SPOTIPY_*; Spotify gracefully fails |

## Section 7: Execution Fabric (7 Brains)

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0039 | 7.1 | 7 brains with defined roles, models, and capabilities | All 7 agents exist as tmux sessions with assigned models |
| REQ-0040 | 7.2 | Job dispatch writes job JSON to agent's superpower_artifacts dir | Job file created in artifacts dir for dispatched brain |
| REQ-0041 | 7.2 | Agent posts callback to /joao/agent_callback | Callback endpoint exists and processes results |
| REQ-0042 | 7.3 | Multi-brain review: Ollama drafts first, 3+ brains in parallel, consensus | QA pipeline dispatches to 3+ brains and synthesizes |
| REQ-0043 | 7.3 | Multi-brain mandatory for L3+ operations | L3+ enforcement requires multi-brain validation |

## Section 8: Security and Privacy

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0044 | 8.1 | Secrets from env only; never logged, never in artifacts, never in prompts | All secret access via os.getenv(); no logging of values |
| REQ-0045 | 8.2 | WU data never sent to external APIs; Ollama safe for any data | Privacy flag on capabilities; external API usage tracked in egress_summary |
| REQ-0046 | 8.3 | All outputs go to superpower_artifacts/{job_id}/ | Artifact store enforces job directory isolation |
| REQ-0047 | 8.3 | No capability may write outside job dir at L2 | L2 writes constrained to sandbox dirs |
| REQ-0048 | 8.3 | L3 writes require WRITE_LOCK with explicit scope | WRITE_LOCK checked before L3 file operations |

## Section 9: API Surface

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0049 | 9 | GET /superpowers/capabilities exists at L0 | Endpoint returns registered capabilities |
| REQ-0050 | 9 | POST /superpowers/route exists at L0 | Endpoint classifies intent |
| REQ-0051 | 9 | POST /superpowers/tableau exists at L2 | Endpoint accepts TWB upload, returns migration bundle |
| REQ-0052 | 9 | POST /superpowers/playlist exists at L2 | Endpoint generates mood playlist |
| REQ-0053 | 9 | POST /superpowers/dispatch exists | Generic dispatch endpoint exists |
| REQ-0054 | 9 | GET /superpowers/artifacts/{id} exists | Artifact listing endpoint exists |
| REQ-0055 | 9 | GET /superpowers/artifacts/{id}/{file} exists | Artifact download endpoint exists |
| REQ-0056 | 9 | GET /superpowers/artifacts/{id}/bundle exists | Zip bundle endpoint exists |
| REQ-0057 | 9 | GET /superpowers/provenance/{run_id} exists | Provenance query endpoint exists |
| REQ-0058 | 9 | GET /superpowers/provenance?last=N exists | Recent provenance endpoint exists |
| REQ-0059 | 9 | POST /superpowers/undo/{run_id} exists | Undo executor endpoint exists |
| REQ-0060 | 9 | POST /superpowers/git/scan exists at L1 | Git scan endpoint exists |
| REQ-0061 | 9 | POST /superpowers/git/write exists at L3 | Git write endpoint exists |
| REQ-0062 | 9 | POST /superpowers/git/ship exists at L4 | Git ship endpoint exists |
| REQ-0063 | 9 | GET /superpowers/tunnel/status exists at L0 | Tunnel status endpoint exists |
| REQ-0064 | 9 | POST /superpowers/context/build exists at L1 | Context build endpoint exists |

## Section 10: What Exists vs Must Be Built

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0065 | 10 | Superpowers router mounted in joao-spine | Router imported and included in app |
| REQ-0066 | 10 | tableau_to_powerbi working with real Dr. Data integration | Tableau endpoint calls real parser + transpiler |
| REQ-0067 | 10 | mood_playlist working (curated + Spotify) | Playlist endpoint generates tracks with streaming links |
| REQ-0068 | 10 | Intent classifier with keyword + file extension routing | Registry.classify_intent() uses both signals |
| REQ-0069 | 10 | Artifact store with job-based storage | artifact_store.py manages job dirs |
| REQ-0070 | 10 | UI wired in /joao/app with buttons and result cards | /joao/app serves PWA with superpower buttons |

## Capability Registry YAML Claims

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0071 | registry | tableau_to_powerbi produces 6 artifact files | Execute returns 6 named artifacts |
| REQ-0072 | registry | mood_playlist constraint: min_autonomy L1, timeout 15s | Playlist accessible at L1; timeout configured |
| REQ-0073 | registry | git_scan is L0, offline_capable, timeout 30s | Git scan has no auth requirement; works locally |
| REQ-0074 | registry | git_write rejects without WRITE_LOCK (HTTP 403) | Smoke test: 403 on missing lock |
| REQ-0075 | registry | git_ship rejects without SHIP_LOCK (HTTP 403) | Smoke test: 403 on missing lock |
| REQ-0076 | registry | context_build returns operating_rules + project_context | Context pack has both sections |
| REQ-0077 | registry | Each capability has smoke_tests defined | YAML has smoke_tests for each capability |

## Go-Live Plan Claims

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0078 | go_live | Phase 0 complete: 12/12 smoke tests passing | Smoke test evidence available |
| REQ-0079 | go_live | JOAO Spine live at :7778 serving 70+ routes | Process running on 7778; route count verified |
| REQ-0080 | go_live | 15 tmux agent sessions live | tmux ls shows 15 agent sessions |
| REQ-0081 | go_live | Ollama running with phi4, deepseek-coder-v2, llama3.1:8b | Ollama process alive; models present |
| REQ-0082 | go_live | Supabase configured (URL + key in env) | SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY set |
| REQ-0083 | go_live | Cloudflared tunnel active at joao.theartofthepossible.io | Tunnel process running; hostname resolves |

---

**Total requirements extracted: 83**
