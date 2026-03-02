# joao-spine

Personal automation server — SSH dispatch, AI processing, idea vault. Deployed on Railway.

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/joao/health` | None | Lightweight liveness check. Always 200 if app is running. |
| GET | `/joao/status` | None | Diagnostic health checks: Supabase, SSH, tmux sessions. |
| POST | `/joao/dispatch` | HMAC | Send a command to a tmux session on the home server. |
| POST | `/joao/audio` | None | Process audio URL through AI pipeline. |
| POST | `/joao/meeting` | None | Process meeting transcript through AI pipeline. |
| POST | `/joao/vision` | None | Process image through AI vision pipeline. |
| POST | `/joao/text` | None | Process text through AI pipeline. |
| GET | `/mcp/sse` | None | MCP server (SSE transport) for tool access. |

### GET /joao/status

Returns diagnostic JSON with real connectivity checks:

```json
{
  "status": "healthy",
  "service": "joao-spine",
  "timestamp": "2026-03-02T18:00:00.000000",
  "version": "abc123",
  "uptime_seconds": 3600.0,
  "checks": {
    "supabase": { "ok": true, "latency_ms": 12.3, "error": null },
    "ssh":      { "ok": true, "latency_ms": 45.0, "error": null, "target": "zamoritacr@192.168.0.55:22" },
    "tmux":     { "ok": true, "latency_ms": 8.1, "error": null, "sessions": ["BYTE", "DEX"] }
  },
  "recent_activity": []
}
```

Status values: `healthy` (all checks pass), `degraded` (partial), `down` (all fail).

### POST /joao/dispatch (HMAC-protected)

**Request body:**
```json
{
  "session_name": "BYTE",
  "command": "echo hello",
  "wait": false
}
```

**Agent allowlist:** BYTE, ARIA, CJ, SOFIA, DEX, GEMMA, MAX (case-insensitive).

**HMAC signing:**
- Header `X-JOAO-SIGNATURE`: `sha256=<hex>`
- Header `X-JOAO-TIMESTAMP`: Unix seconds
- Signature = HMAC-SHA256(secret, `"{timestamp}.{raw_body}"`)
- Timestamp must be within ±300 seconds of server time.

Returns 401 if signature is missing/invalid. Returns 422 if agent name or command fails validation.

## HMAC Signing — curl example

```bash
SECRET="your-hmac-secret"
TIMESTAMP=$(date +%s)
BODY='{"session_name":"BYTE","command":"echo hello","wait":false}'
SIG="sha256=$(printf "%s.%s" "$TIMESTAMP" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"

curl -X POST https://joao-spine-production.up.railway.app/joao/dispatch \
  -H "Content-Type: application/json" \
  -H "X-JOAO-TIMESTAMP: $TIMESTAMP" \
  -H "X-JOAO-SIGNATURE: $SIG" \
  -d "$BODY"
```

## Test commands

```bash
# Health check
curl https://joao-spine-production.up.railway.app/joao/health

# Diagnostic status
curl https://joao-spine-production.up.railway.app/joao/status | python3 -m json.tool

# Unsigned dispatch (should 401 when HMAC secret is configured)
curl -X POST https://joao-spine-production.up.railway.app/joao/dispatch \
  -H "Content-Type: application/json" \
  -d '{"session_name":"BYTE","command":"echo test","wait":false}'
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service role key (falls back to `SUPABASE_KEY`) |
| `COUNCIL_SSH_HOST` | Yes | SSH target host (falls back to `SSH_HOST`, default `192.168.0.55`) |
| `COUNCIL_SSH_PORT` | No | SSH port (falls back to `SSH_PORT`, default `22`) |
| `COUNCIL_SSH_USER` | No | SSH username (falls back to `SSH_USER`, default `joao`) |
| `COUNCIL_SSH_PRIVATE_KEY` | Yes* | PEM key content for Railway (falls back to `*_KEY_PATH` vars) |
| `JOAO_DISPATCH_HMAC_SECRET` | Yes | HMAC signing secret. If unset, dispatch auth is disabled with a warning. |
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `OPENAI_MODEL` | No | OpenAI model (default `gpt-4o`) |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID |

*On Railway, set `COUNCIL_SSH_PRIVATE_KEY` to the full PEM content (Railway supports multiline env vars in the dashboard). Locally, use `COUNCIL_SSH_PRIVATE_KEY_PATH` or `SSH_PRIVATE_KEY_PATH` instead.

## Railway Notes

- All secrets must be set in **Railway Variables** (project dashboard → Variables tab).
- `RAILWAY_PUBLIC_DOMAIN` and `RAILWAY_GIT_COMMIT_SHA` are set automatically by Railway.
- The healthcheck path is `/joao/health` (configured in `railway.toml`).
- Logs are JSON-structured to stdout — visible in Railway's log viewer.
