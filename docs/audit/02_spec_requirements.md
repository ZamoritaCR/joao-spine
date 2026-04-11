# Phase 2: Spec Requirements (Atomic Testable Statements)

**Source:** `JOAO_OS_DESIGN.md` v2.0 + `capability_registry.yaml` + `go_live_plan.md`
**Extraction date:** 2026-04-11 (v2)
**Total requirements:** 90

---

## Section 1: What JOAO Is

| REQ | Spec Section | Claim (<=25 words) | Acceptance Criteria |
|-----|-------------|---------------------|---------------------|
| REQ-0001 | 1 | Any input enters, gets classified, routed, executed, returns artifacts with provenance | Input -> classify -> route -> execute -> artifacts + provenance entry |
| REQ-0002 | 1 | Every action is auditable | All capability executions produce a queryable audit record |
| REQ-0003 | 1 | Every action is reversible | All capability executions include an undo recipe |

---

## Section 2: Autonomy Dial

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0004 | 2 | Five autonomy levels: L0 (Observe) through L4 (Ship) | Code defines/enforces all 5 levels |
| REQ-0005 | 2 | L0: Capture, log, classify. No output beyond ack | L0 requests produce only acknowledgment |
| REQ-0006 | 2 | L2: Write ONLY to sandbox dirs (superpower_artifacts/, /tmp/joao-*) | L2 file writes constrained to sandbox paths |
| REQ-0007 | 2 | L3 requires WRITE_LOCK | L3 operations rejected without active WRITE_LOCK |
| REQ-0008 | 2 | L4 requires SHIP_LOCK | L4 operations rejected without active SHIP_LOCK |
| REQ-0009 | 2 | Default autonomy is L1 unless explicitly set | Requests without autonomy flag default to L1 |
| REQ-0010 | 2 | Locks stored in provenance ledger | Lock grants/expirations recorded in ledger |
| REQ-0011 | 2 | Lock checked before every L3/L4 operation; refuse if expired or out of scope | Middleware validates lock before execution |
| REQ-0012 | 2 | Autonomy parsed from any position in user input | Parser extracts L0-L4 from arbitrary text positions |
| REQ-0013 | 2 | Missing lock -> request lock and wait | 403 with lock requirement message |

---

## Section 3: Capability Graph

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0014 | 3.1 | Every capability defined by contract schema (name, version, inputs, outputs, constraints...) | YAML or code defines all contract fields per capability |
| REQ-0015 | 3.2 | 10 capabilities registered: tableau_to_powerbi through tunnel_status | Registry contains all 10 capabilities |
| REQ-0016 | 3.3 | Capability chains compose for complex intents | Chain resolver exists and executes sequential capabilities |
| REQ-0017 | 3.4 | Router priority: file extension > explicit name > keyword score > chain inference > fallback | Router implements this priority stack |
| REQ-0018 | 3.4 | Tiebreaker: offline-first > minimal risk > minimal latency > enterprise constraint | Tiebreaker logic implemented |
| REQ-0019 | 3.1 | Each capability has smoke_tests defined | Smoke test commands exist per capability |
| REQ-0020 | 3.1 | Each capability has undo_strategy defined | Undo strategy string exists per capability |
| REQ-0021 | 3.1 | Each capability has default_brain assigned | default_brain field populated per capability |

---

## Section 4: Provenance Ledger

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0022 | 4.1 | Every execution produces exactly one provenance entry | Entry written on each capability run |
| REQ-0023 | 4.1 | Provenance includes: run_id, timestamp, intent, autonomy_level, capability_chain, tools_called, files_written, undo_recipe, outcome | Schema has all listed fields |
| REQ-0024 | 4.2 | Dual storage: Supabase + local JSONL fallback | Both write paths attempted; Supabase failure doesn't block local |
| REQ-0025 | 4.3 | Five undo types: delete_artifacts, git_revert, git_delete_branch, noop, service_rollback | All 5 undo types implemented as executable recipes |
| REQ-0026 | 4.2 | Local fallback is append-only JSONL | ledger.jsonl is append-only (no deletes/updates) |

---

## Section 5: Context Packs

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0027 | 5.1 | Context pack schema: operating_rules, session_history, project_context, landmines, relevant_files, definition_of_done | Builder produces all sections |
| REQ-0028 | 5.2 | Pack hashed (SHA-256) and hash stored in provenance | Hash computed and recorded per execution |
| REQ-0029 | 5.2 | operating_rules always included, sourced from MEMORY.md + CLAUDE.md | Rules section populated from these files |
| REQ-0030 | 5.2 | session_history: last 20 provenance entries filtered by relevance | History section contains recent relevant entries |
| REQ-0031 | 5.2 | landmines: hardcoded known dangers + CLAUDE.md Active Bugs | Landmines section populated |

---

## Section 6: Tool Dominion (Adapters)

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0032 | 6.1 | Git adapter: reads at L0+, writes at L3+WRITE_LOCK, ships at L4+SHIP_LOCK | Adapter enforces autonomy per operation type |
| REQ-0033 | 6.2 | Supabase adapter: reads at L0+, writes at L2+, schema at L3+WRITE_LOCK | Adapter enforces autonomy |
| REQ-0034 | 6.3 | Telegram adapter: notify at L1+, deliver at L2+ | Adapter enforces autonomy |
| REQ-0035 | 6.4 | Cloudflared adapter: status at L0+, restart at L4+SHIP_LOCK | Adapter enforces autonomy |
| REQ-0036 | 6.5 | Ollama adapter: list at L0+, generate/chat at L1+, review at L2+ | Adapter enforces autonomy |
| REQ-0037 | 6.6 | File ingest: upload/metadata at L1+, artifacts at L2+, bundle at L2+ | Adapter enforces autonomy |
| REQ-0038 | 6.7 | Dr. Data adapter: parse at L1+, transpile at L1+, map at L2+, generate at L2+ | Adapter enforces autonomy |
| REQ-0039 | 6.2 | Supabase credential (SERVICE_ROLE_KEY) never logged, never in artifacts, never in prompts | Key not present in any log/artifact/prompt output |
| REQ-0040 | 6.8 | MrDP adapter: curated playlists work offline; Spotify needs SPOTIPY_CLIENT_ID | Curated works without network; Spotify gracefully degrades |

---

## Section 7: Execution Fabric

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0041 | 7.1 | 7 brains with distinct roles: BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX | All 7 defined with roles and default models |
| REQ-0042 | 7.2 | Job dispatch: router -> brain select -> job JSON -> tmux -> result -> callback -> provenance | Full dispatch lifecycle implemented |
| REQ-0043 | 7.2 | Job JSON written to superpower_artifacts/{job_id}/job_{BRAIN}.json | Job files created per dispatch |
| REQ-0044 | 7.2 | Brain POSTs callback to /joao/agent_callback | Callback endpoint exists and processes results |
| REQ-0045 | 7.3 | Multi-brain review mandatory for L3+ operations | L3+ dispatches to 3+ brains before shipping |
| REQ-0046 | 7.3 | Ollama drafts first (free, local), then all brains review | Ollama is first in review chain |

---

## Section 8: Security and Privacy

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0047 | 8.1 | All secrets from env vars; 5 specific secrets listed with handling rules | All 5 secrets sourced from env only |
| REQ-0048 | 8.2 | WU data never sent to external APIs; Ollama is safe for any data | No code path sends WU-flagged data externally |
| REQ-0049 | 8.2 | Provenance flags if external API was used | Provenance entry records egress (internal/external) |
| REQ-0050 | 8.3 | No capability may write outside job dir at L2 | Sandbox enforcement prevents writes beyond job dir |
| REQ-0051 | 8.3 | L3 writes require WRITE_LOCK with explicit scope | Scope-check before L3 file writes |
| REQ-0052 | 8.1 | SSH key contents never read, only existence checked | No code reads SSH private key content beyond transport use |

---

## Section 9: API Surface

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0053 | 9 | All endpoints under /joao/superpowers/ on port 7778 | Superpowers router mounted at correct prefix |
| REQ-0054 | 9 | GET /superpowers/capabilities returns registered list | Endpoint returns all capabilities |
| REQ-0055 | 9 | POST /superpowers/route classifies intent | Endpoint returns routing decision |
| REQ-0056 | 9 | POST /superpowers/tableau uploads TWB/TWBX, returns migration bundle | Endpoint processes Tableau files |
| REQ-0057 | 9 | POST /superpowers/playlist generates mood playlist | Endpoint returns playlist JSON |
| REQ-0058 | 9 | POST /superpowers/dispatch dispatches to any capability | Generic dispatch endpoint exists |
| REQ-0059 | 9 | GET /superpowers/artifacts/{id} lists job artifacts | Endpoint returns artifact listing |
| REQ-0060 | 9 | GET /superpowers/provenance/{run_id} returns provenance entry | Provenance query endpoint exists |
| REQ-0061 | 9 | POST /superpowers/undo/{run_id} executes undo recipe | Undo execution endpoint exists |
| REQ-0062 | 9 | POST /superpowers/git/scan scans repos | Git scan endpoint exists |
| REQ-0063 | 9 | POST /superpowers/git/write branches/commits | Git write endpoint exists |
| REQ-0064 | 9 | POST /superpowers/git/ship pushes/deploys | Git ship endpoint exists |
| REQ-0065 | 9 | GET /superpowers/tunnel/status returns health | Tunnel status endpoint exists |
| REQ-0066 | 9 | POST /superpowers/context/build builds pack | Context build endpoint exists |

---

## Section 10: What Exists vs What Must Be Built

| REQ | Spec Section | Claim | Acceptance Criteria |
|-----|-------------|-------|---------------------|
| REQ-0067 | 10 | Superpowers router mounted in joao-spine | Router registered and serving requests |
| REQ-0068 | 10 | tableau_to_powerbi: real Dr. Data integration | Imports and calls Dr. Data parser/transpiler |
| REQ-0069 | 10 | mood_playlist: curated + Spotify | Both adapters functional |
| REQ-0070 | 10 | Capability registry with intent classification | Registry classifies intents to capabilities |
| REQ-0071 | 10 | Artifact store with job-based storage | Artifacts stored per job_id |
| REQ-0072 | 10 | UI wired in /joao/app | Web UI serves at /joao/app |
| REQ-0073 | 10 | Must build: autonomy dial parsing + enforcement middleware | Middleware not yet in production |
| REQ-0074 | 10 | Must build: lock manager | Lock manager not yet integrated |
| REQ-0075 | 10 | Must build: provenance ledger (Supabase + local) | Ledger not yet auto-recording on capability execution |
| REQ-0076 | 10 | Must build: context pack builder | Builder not yet integrated |
| REQ-0077 | 10 | Must build: git adapter (scan, write, ship) | Git adapter not yet implemented |
| REQ-0078 | 10 | Must build: tunnel status adapter | Tunnel adapter not yet implemented |
| REQ-0079 | 10 | Must build: Ollama multi-brain review | Multi-brain not yet wired |
| REQ-0080 | 10 | Must build: undo executor | Undo executor not yet implemented |
| REQ-0081 | 10 | Must build: capability chaining engine | Chain engine not yet implemented |

---

## Capability Registry Requirements (from capability_registry.yaml)

| REQ | Capability | Claim | Acceptance Criteria |
|-----|-----------|-------|---------------------|
| REQ-0082 | tableau_to_powerbi | 6 artifacts produced (spec, DAX, model, plan, build instructions, PBIP) | All 6 files in output |
| REQ-0083 | tableau_to_powerbi | Smoke test: parse DigitalAds TWBX -> success + 6 files | Smoke test passes |
| REQ-0084 | tableau_to_powerbi | Smoke test: reject non-TWB file -> HTTP 400 | Validation rejects bad input |
| REQ-0085 | mood_playlist | Smoke test: stressed->focused -> playlist with tracks | Generates playlist |
| REQ-0086 | mood_playlist | Undo strategy: noop (read-only) | Playlist is informational only |
| REQ-0087 | git_scan | Smoke test: scan all repos -> status per repo | Returns multi-repo status |
| REQ-0088 | git_write | Smoke test: reject without WRITE_LOCK -> HTTP 403 | Lock enforcement works |
| REQ-0089 | git_ship | Smoke test: reject without SHIP_LOCK -> HTTP 403 | Lock enforcement works |
| REQ-0090 | tunnel_status | Smoke test: check tunnel -> name + hostnames | Returns tunnel health |
