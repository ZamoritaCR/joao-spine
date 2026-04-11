# Phase 1: Static Analysis Summary

**Date:** 2026-04-11 (v2)
**Codebase:** `/home/zamoritacr/joao-spine` (HEAD: `a3e72ea`)
**Tools run:** ruff, bandit, pip-audit, shellcheck, secrets grep
**Tools unavailable:** mypy (not installed), gitleaks (not installed), openapi-spec-validator (not installed)

---

## Tool Results Summary

| Tool | Issues Found | Severity Breakdown |
|------|-------------|-------------------|
| ruff (Python lint) | 166 errors | 90 auto-fixable (F401), rest F841/E741/E402 |
| bandit (security) | 100 issues | 3 High, 7 Medium, 90 Low |
| pip-audit (deps) | 0 vulnerabilities | Clean |
| shellcheck (shell) | 3 info-level | SC1091 (sourced files), SC2034 (unused var) |
| secrets grep | 0 findings | No hardcoded keys/passwords in .py files |

---

## Ruff Analysis (166 errors)

**Breakdown by rule:**

| Rule | Count | Description | Severity |
|------|-------|-------------|----------|
| F401 | ~90 | Unused imports | Low (cosmetic) |
| F841 | ~15 | Assigned but unused variables | Low (dead code) |
| F541 | ~5 | f-strings without placeholders | Low (cosmetic) |
| E741 | ~3 | Ambiguous variable names (`l`) | Low (readability) |
| E402 | ~5 | Module imports not at top of file | Low (style) |

**Assessment:** No functional issues. All are cosmetic/style. 90 are auto-fixable with `ruff --fix`.

**Key files with issues:**
- `capability/mood_playlist.py` -- unused imports (json, MOOD_SEEDS), unused variable `language`
- `capability/tableau_to_powerbi.py` -- unused imports (json, Path), unused vars (`tables`, `path`)
- `services/qa_pipeline.py` -- unused imports (io, datetime), ambiguous var name `l`
- `terminal_manager.py` -- unused imports (struct, termios)

---

## Bandit Security Analysis (100 issues)

### High Severity (3)

| ID | File | Issue | Assessment |
|----|------|-------|------------|
| B608 | `docs/taop-agents/tasks.py:68` | Possible SQL injection (f-string in UPDATE) | **REAL RISK** -- but in docs/ subproject, not live spine |
| B603 | Various | subprocess calls without shell=True | **FALSE POSITIVE** -- intentional for tmux/pgrep |
| B607 | Various | Partial executable path in subprocess | **ACCEPTABLE** -- tmux/pgrep are standard system tools |

### Medium Severity (7)

| ID | Files | Issue | Assessment |
|----|-------|-------|------------|
| B108 | `joao_local_dispatch.py:84-85` | Hardcoded /tmp paths | **ACCEPTABLE** -- `/tmp/council/tasks/` is intentional temp staging |
| B113 | Various | Requests without timeout | **POTENTIAL ISSUE** -- network calls could hang |
| B110 | `tools/chat.py:61` | try/except/pass | **MINOR** -- bare except swallows errors silently |

### Low Severity (90)

Primarily B404 (import subprocess), B603/B607 (subprocess calls). All are expected patterns for a system that orchestrates tmux sessions and local processes.

---

## Dependency Audit (pip-audit)

```
No known vulnerabilities found
```

All Python dependencies are at versions with no known CVEs as of 2026-04-11.

---

## Shell Script Analysis (shellcheck)

**Files checked:**
- `/home/zamoritacr/joao-spine/start_local_dispatch.sh` -- **CLEAN**
- `/home/zamoritacr/joao-spine/scripts/inspect_focusflow.sh` -- **CLEAN**
- `/home/zamoritacr/taop-repos/joao-spine/scripts/boom.sh` -- 3 info-level findings:
  - SC1091: `source .env` not followed (expected -- dynamic path)
  - SC1091: `source activate` not followed (expected -- venv)
  - SC2034: Loop variable `i` unused (cosmetic -- used for delay counting)

---

## Secrets Scan

Grep for `sk-`, `password\s*=\s*['"]`, API keys in .py files (excluding .venv):

**Result: 0 hardcoded secrets found.**

All credentials sourced from environment variables via `os.environ.get()` or `os.getenv()`.

---

## Missing Tool Coverage

| Tool | Status | Impact |
|------|--------|--------|
| mypy | Not installed | No type-checking coverage; Python is untyped |
| gitleaks | Not installed | No git history secret scanning |
| openapi-spec-validator | Not installed | `openapi_council.yaml` not validated |
| npm audit | N/A | No Node.js dependencies |

---

## Raw Outputs

Raw tool outputs are available at:
- `docs/audit/raw/ruff.txt`
- `docs/audit/raw/bandit.txt`
- `docs/audit/raw/pip_audit.txt`
- `docs/audit/raw/shellcheck.txt`
- `docs/audit/raw/secrets_grep.txt`
