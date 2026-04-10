#!/usr/bin/env bash
# Smoke test: inspect FocusFlow via JOAO Remote Inspector
set -uo pipefail

BASE="${JOAO_BASE_URL:-http://127.0.0.1:7778}"
PASS=0
FAIL=0

check() {
    local name="$1" ok="$2"
    if [ "$ok" = "true" ]; then
        echo "  [PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== FocusFlow Inspection Smoke Test ==="
echo "Target: $BASE"
echo ""

# 1. Quick inspect
RESP=$(curl -s "$BASE/joao/inspect/focusflow")
STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw']['status'])" 2>/dev/null)
check "FocusFlow returns 200" "$([ "$STATUS" = "200" ] && echo true || echo false)"

# 2. No redirect loops
REDIRECTS=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['raw']['redirect_chain']))" 2>/dev/null)
check "No excessive redirects (<5)" "$([ "${REDIRECTS:-99}" -lt 5 ] && echo true || echo false)"

# 3. TLS valid
TLS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw']['tls']['protocol'])" 2>/dev/null)
check "TLS protocol present" "$([ -n "$TLS" ] && echo true || echo false)"

# 4. Body present
BODY_SIZE=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw']['body_size_bytes'])" 2>/dev/null)
check "Body size > 0" "$([ "${BODY_SIZE:-0}" -gt 0 ] && echo true || echo false)"

# 5. Security: block non-allowlisted
BLOCK=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"url":"https://google.com"}' "$BASE/joao/inspect" | python3 -c "import sys,json; d=json.load(sys.stdin); print('blocked' if 'not in allowlist' in d.get('detail','') else 'open')" 2>/dev/null)
check "Non-allowlisted domain blocked" "$([ "$BLOCK" = "blocked" ] && echo true || echo false)"

# 6. Security: block http
BLOCK_HTTP=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"url":"http://focusflow.theartofthepossible.io"}' "$BASE/joao/inspect" | python3 -c "import sys,json; d=json.load(sys.stdin); print('blocked' if 'https' in d.get('detail','').lower() else 'open')" 2>/dev/null)
check "HTTP (non-HTTPS) blocked" "$([ "$BLOCK_HTTP" = "blocked" ] && echo true || echo false)"

echo ""
echo "=== RESULTS: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
