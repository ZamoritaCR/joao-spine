# Phase 1: Static Analysis Summary

**Date:** 2026-04-10
**Codebase:** `/home/zamoritacr/joao-spine` (HEAD: `0934ee8`)

---

## Tool Results Overview

| Tool | Target | Findings | Raw Output |
|------|--------|----------|------------|
| ruff | All .py files | 16 issues (F401 unused imports, F841 unused vars, F541 f-string) | `raw/ruff.txt` |
| bandit | All .py files | 204 total: 3 High, 7 Medium, 194 Low | `raw/bandit.txt` |
| shellcheck | Council .sh scripts | 12 issues (warnings + info) | `raw/shellcheck_council.txt` |
| shellcheck | Spine scripts/ | 0 issues (clean) | `raw/shellcheck_spine.txt` |
| pip-audit | requirements.txt | 0 known vulnerabilities | `raw/pip_audit.txt` |
| secrets grep | All source files | 50 lines flagged (see below) | `raw/secrets_grep.txt` |

---

## 1. Ruff (Python Linter)

**16 issues total** -- all LOW severity (code quality, not security):

- **F401 (unused imports):** 11 instances across `artifact_store.py`, `mood_playlist.py`, `music.py`, `registry.py`, `tableau_to_powerbi.py`, `docs/taop-agents/cli.py`, `docs/taop-agents/dashboard.py`
- **F841 (unused variables):** 3 instances -- `language` in `mood_playlist.py:70`, `tables` in `tableau_to_powerbi.py:245`, `path` in `tableau_to_powerbi.py:406`
- **F541 (f-string without placeholders):** 1 instance in `docs/taop-agents/dashboard.py:200`
- **Impact:** None operational. Code cleanliness only.

---

## 2. Bandit (Security Scanner)

### HIGH severity (3 issues)

All 3 are `B113: request_without_timeout` -- HTTP requests made without explicit timeout. These are in:
- Various httpx/requests calls throughout the codebase
- **Risk:** Potential for hung connections under network failure. Not exploitable but affects reliability.

### MEDIUM severity (7 issues)

- **B608 (SQL injection):** 1 instance in `docs/taop-agents/tasks.py:68` -- f-string SQL construction. This is in a separate TAOP agents demo app, NOT in the live JOAO spine. Low real-world risk.
- **B108 (hardcoded tmp):** 2 instances in `joao_local_dispatch.py:84-85` -- `/tmp/council/tasks` and `/tmp/council/outputs`. Known design choice for inter-process communication. Mitigated by single-user system.
- **B110 (try-except-pass):** 4 instances -- silent exception swallowing. Risk: masks errors during debugging.

### LOW severity (194 issues)

Dominated by:
- **B404/B607/B603 (subprocess usage):** ~180 instances -- all tmux/pgrep subprocess calls in `joao_local_dispatch.py`, `exocortex/digest.py`. These are legitimate system management calls, NOT user-input-driven. All use list-form arguments (no shell=True), which is the safe pattern.
- **B110 (try-except-pass):** scattered silent exception handlers

**Assessment:** No critical or exploitable vulnerabilities found in bandit scan. The subprocess usage pattern (list args, no shell=True) is correct. The SQL injection flag is in a non-production demo app.

---

## 3. ShellCheck (Shell Scripts)

**12 issues** in council scripts, all LOW:

| File | Issue | Severity |
|------|-------|----------|
| `council_health.sh:40` | SC2034: `gpid` unused variable | Warning |
| `council_watchdog.sh:29` | SC1091: Not following sourced .env | Info |
| `council_watchdog.sh:33` | SC2155: Declare/assign separately | Warning |
| `launch_agent.sh:45` | SC1091: Not following sourced activate | Info |
| `context_watcher.sh:27` | SC2162: read without -r | Info |
| `context_watcher.sh:33` | SC2129: Consider grouped redirects | Style |
| `restart_agents.sh:9` | SC1091: Not following sourced .env | Info |
| `setup_agents.sh:26` | SC2034: `session` unused | Warning |
| `setup_agents.sh:43,50` | SC2015: A&&B||C is not if-then-else | Info |
| `council_launch.sh:12` | SC1091: Not following sourced .env | Info |

**Assessment:** No functional bugs. Mostly style and info-level notices.

---

## 4. pip-audit (Dependency Vulnerabilities)

**Result: No known vulnerabilities found.**

All dependencies in `requirements.txt` are at versions without known CVEs.

---

## 5. Secrets Scan (grep-based)

**Methodology:** Searched for `password|secret|token|api_key|apikey|private_key` in all source files, excluding .env files and environment variable reads.

**Findings:**

- **No hardcoded secrets found in Python source.** All credentials use `os.getenv()` or `os.environ.get()` patterns.
- **Dispatch secret reference:** `joao_local_dispatch.py:24` has a comment referencing "shared secret" but the actual value comes from env var.
- **API key references** in `capability/music.py`, `tools/chat.py`, `middleware/auth.py` -- all read from environment, never hardcoded.
- **JOAO_DISPATCH_SECRET** found hardcoded in systemd service file `/home/zamoritacr/.config/systemd/user/joao-dispatch.service` -- this is expected (systemd environment injection). Value REDACTED in this report.
- **Telegram bot token** reference in `exocortex/digest.py:261` -- reads from env var, not hardcoded.

**Assessment:** Secret management follows proper patterns. No credentials committed to source code.

---

## Overall Static Analysis Verdict

| Category | Status |
|----------|--------|
| Critical vulnerabilities | NONE |
| Hardcoded secrets | NONE |
| Dependency CVEs | NONE |
| SQL injection risk | LOW (demo app only, not prod) |
| Code quality | GOOD (minor unused imports) |
| Shell scripts | CLEAN (style-only issues) |
| Subprocess safety | GOOD (list args, no shell=True) |
