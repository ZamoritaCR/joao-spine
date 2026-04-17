# Gap Closure Runbook — branch `gap-closure-20260416`

Sequence to apply what's on this branch. Branch does NOT push itself; merge
and deploy are explicit manual steps.

## What this branch changes

| Commit | What | Risk |
|---|---|---|
| `61b9e7f` | Add migration SQL (file only, not applied) | zero |
| `0765755` | Route `SessionLogRecord` writes to `session_log` table (1-line fix) | zero until migration runs |
| `494b4b3` | Add `services/dispatch_receipt.py` (new module, no callers yet) | zero |
| `2b738be` | Wire `/joao/dispatch` endpoint to `dispatch_with_receipt` when `wait=True` | affects live endpoint on next spine restart |
| `be69b0b` | Add `scripts/memory_consolidate.sh` (not executed by commit) | zero |
| `e1aae93` | Add `.taop-manifest.yaml` | zero |
| `b8390d4` | Add `scripts/taop_manifest_validate.py` (tested live, returned OK) | zero |

## Apply order

### Step 1 — Supabase migration (user-run)
Open https://supabase.com/dashboard/project/wkfewpynskakgbetscsa/sql/new
Paste contents of `migrations/20260416_gap_closure_receipts.sql`, click Run.
Verify:
```bash
KEY=$(grep '^SUPABASE_SERVICE_ROLE_KEY=' .env | cut -d= -f2-)
curl -s -H "apikey: $KEY" -H "Authorization: Bearer $KEY" \
  "https://wkfewpynskakgbetscsa.supabase.co/rest/v1/session_log?limit=1"
# Expect: []
curl -s -H "apikey: $KEY" -H "Authorization: Bearer $KEY" \
  "https://wkfewpynskakgbetscsa.supabase.co/rest/v1/agent_outputs?limit=1"
# Expect: []
```

### Step 2 — Merge branch, restart spine
```bash
cd ~/joao-spine
git checkout main
git merge --no-ff gap-closure-20260416
# (optional) git push origin main
sudo systemctl restart joao-spine-local   # or your deploy process
sleep 5
curl -s http://localhost:7778/health | head -1
# Tail for 60s, confirm no "insert_session_log failed" warnings
tail -f logs/spine.log &
TAIL_PID=$!
sleep 60
kill $TAIL_PID
tail -200 logs/spine.log | grep -c "insert_session_log failed"
# Expect: 0
```

### Step 3 — Live verify dispatch receipts
```bash
# Send a wait=true dispatch, confirm receipt in agent_outputs
curl -sX POST http://localhost:7778/joao/dispatch \
  -H "Authorization: Bearer $JOAO_DISPATCH_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"session_name":"CORE","command":"echo HELLO_FROM_VERIFIED","wait":true}'

# Then query agent_outputs for the most recent row
KEY=$(grep '^SUPABASE_SERVICE_ROLE_KEY=' .env | cut -d= -f2-)
curl -s -H "apikey: $KEY" -H "Authorization: Bearer $KEY" \
  "https://wkfewpynskakgbetscsa.supabase.co/rest/v1/agent_outputs?order=created_at.desc&limit=1" \
  | python3 -m json.tool
# Expect: row with session_name=CORE, metadata.verified=true, RECEIPT_ token in output
```

### Step 4 — Memory consolidation (user-run, interactive)
```bash
bash scripts/memory_consolidate.sh
# Says y to each prompt. Script archives divergent copies, symlinks them
# to canonical, appends JOAO_MEMORY_DIR to .env, writes a canary.
# Restart spine after for JOAO_MEMORY_DIR to take effect.
sudo systemctl restart joao-spine-local
```

### Step 5 — Install manifest validator cron
```bash
# Install pyyaml if missing
pip install --user pyyaml

# Symlink manifest to canonical location
ln -sf ~/joao-spine/.taop-manifest.yaml ~/.taop-manifest.yaml

# Add cron line
(crontab -l; echo '*/5 * * * * /usr/bin/flock -n /tmp/taop-manifest-validate.lock /home/zamoritacr/joao-spine/scripts/taop_manifest_validate.py >> /home/zamoritacr/logs/taop-manifest.log 2>&1') | crontab -

# Verify
crontab -l | grep taop_manifest
python3 scripts/taop_manifest_validate.py
```

### Step 6 — Tunnel + triage (user-run, sudo)
These aren't in the branch; do them when ready:

```bash
# Kill 2 rogue QuickTunnels (unauthenticated public *.trycloudflare.com URLs)
pgrep -f 'cloudflared tunnel --url http://localhost' | xargs -r kill

# Fix FocusFlow tunnel misconfig (7775 -> 8001)
sudo cp /etc/cloudflared/config.yml /etc/cloudflared/config.yml.bak.$(date -u +%s)
sudo sed -i 's|service: http://localhost:7775|service: http://localhost:8001|' /etc/cloudflared/config.yml
sudo systemctl restart cloudflared

# Verify
curl -s -o /dev/null -w "%{http_code}\n" https://focusflow.theartofthepossible.io/
# Expect: 2xx/3xx/401/403 (not 502)
```

### Step 7 — GitHub PAT rotation (user-only)
1. Visit https://github.com/settings/tokens
2. Revoke `<REDACTED_GITHUB_PAT>` (or the current version)
3. Generate replacement with minimum scopes
4. Update `~/joao-spine/.env` `GITHUB_TOKEN=...`
5. Delete the leaking directory:
   `rm -rf /home/zamoritacr/taop-repos/<LEAKED_PAT_DIR>`

## Rollback

```bash
# Unmerge the branch (if step 2 already happened)
git reset --hard 910c49b   # previous main HEAD
sudo systemctl restart joao-spine-local

# Or just don't merge — branch sits unmerged indefinitely, zero impact.
```

## Ground truth as of branch close

- Branch: `gap-closure-20260416`
- HEAD: `b8390d4`
- 7 commits, +789 lines, -11 lines
- All files pass syntax / import / YAML validation
- Validator ran live and returned `OK products_checked=8`
- Migration NOT applied to Supabase (user-gated)
- Spine NOT restarted (still on main, commit 910c49b)
- Nothing pushed to origin

## Out of scope (Dr. Data)
Per owner directive, nothing in this branch touches Dr. Data repos, ports,
tunnels, or services. The 5-copy streamlit_app.py sprawl, the
drdata-v2/drdata-workbench duplication, and the evidence-logger spec all
remain for a separate sprint.
