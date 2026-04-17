#!/usr/bin/env bash
# ============================================================================
# JOAO memory consolidation
# ============================================================================
# Unifies the 5 divergent memory files behind one canonical location.
#
# Canonical (chosen because it matches routers/joao.py _MEMORY_DIR default):
#   /home/zamoritacr/joao-interface/memory/JOAO_SESSION_LOG.md
#   /home/zamoritacr/joao-interface/memory/JOAO_MASTER_CONTEXT.md
#
# Others (archived, then symlinked to canonical):
#   /home/zamoritacr/joao-spine/JOAO_SESSION_LOG.md
#   /home/zamoritacr/joao-spine/JOAO_MASTER_CONTEXT.md
#   /home/zamoritacr/JOAO_MASTER_CONTEXT.md
#
# IDEMPOTENT, prompts before every destructive op.
# Ref: gap-closure-20260416 / JOAO_TRUE_REAL_GAP.md section 5
# ============================================================================

set -euo pipefail

CANONICAL_DIR="/home/zamoritacr/joao-interface/memory"
ARCHIVE_DIR="/home/zamoritacr/joao-memory-archive/$(date -u +%Y%m%d_%H%M%S)"
CANONICAL_SESSION="$CANONICAL_DIR/JOAO_SESSION_LOG.md"
CANONICAL_MASTER="$CANONICAL_DIR/JOAO_MASTER_CONTEXT.md"

OTHER_FILES=(
    "/home/zamoritacr/joao-spine/JOAO_SESSION_LOG.md"
    "/home/zamoritacr/joao-spine/JOAO_MASTER_CONTEXT.md"
    "/home/zamoritacr/JOAO_MASTER_CONTEXT.md"
)

confirm() { read -rp "${1} [y/N] " r ; [[ "$r" =~ ^[Yy] ]] ; }
log()  { printf "[%s] %s\n" "$(date -u +%H:%M:%S)" "$*" ; }
warn() { printf "[%s] WARN: %s\n" "$(date -u +%H:%M:%S)" "$*" >&2 ; }

log "verify canonical files exist and are non-empty"
for f in "$CANONICAL_SESSION" "$CANONICAL_MASTER" ; do
    if [[ ! -s "$f" ]]; then
        warn "canonical file missing or empty: $f"
        warn "ABORTING — not safe to proceed without a known-good canonical."
        exit 1
    fi
    log "  ok: $f ($(stat -c '%s' "$f") bytes)"
done

log "create archive dir $ARCHIVE_DIR"
mkdir -p "$ARCHIVE_DIR"

log "archive divergent copies (full content preserved)"
for f in "${OTHER_FILES[@]}" ; do
    if [[ -f "$f" && ! -L "$f" ]]; then
        dest="$ARCHIVE_DIR/$(echo "$f" | tr / _)"
        cp -a "$f" "$dest"
        log "  archived $f -> $dest ($(stat -c '%s' "$dest") bytes)"
    fi
done

log "replace divergent copies with symlinks to canonical"
if confirm "Proceed to replace the non-canonical files with symlinks?"; then
    for f in "${OTHER_FILES[@]}"; do
        if [[ -f "$f" && ! -L "$f" ]]; then
            case "$(basename "$f")" in
                JOAO_SESSION_LOG.md)    target="$CANONICAL_SESSION" ;;
                JOAO_MASTER_CONTEXT.md) target="$CANONICAL_MASTER"  ;;
                *) warn "unknown file $f, skipping"; continue ;;
            esac
            rm -f "$f"
            ln -s "$target" "$f"
            log "  linked $f -> $target"
        fi
    done
else
    warn "skipped symlinking; divergent copies archived only"
fi

log "ensure JOAO_MEMORY_DIR is explicit in spine env"
ENV_FILE="/home/zamoritacr/joao-spine/.env"
if ! grep -q '^JOAO_MEMORY_DIR=' "$ENV_FILE" ; then
    if confirm "Append JOAO_MEMORY_DIR=$CANONICAL_DIR to $ENV_FILE?"; then
        printf '\n# Canonical memory path (added by memory_consolidate.sh %s)\nJOAO_MEMORY_DIR=%s\n' \
            "$(date -u +%Y-%m-%d)" "$CANONICAL_DIR" >> "$ENV_FILE"
        log "  appended JOAO_MEMORY_DIR to $ENV_FILE"
        warn "  restart the spine for this to take effect"
    fi
else
    log "  JOAO_MEMORY_DIR already set in $ENV_FILE — no change"
fi

log "write sentinel canary for later verification"
CANARY="MEMORY_CONSOLIDATED_$(date -u +%s)_$(openssl rand -hex 4)"
cat >> "$CANONICAL_SESSION" <<EOF

---
### [$(date -u +%Y-%m-%dT%H:%M:%SZ)] memory_consolidate.sh
Canary: $CANARY
Archived copies live at: $ARCHIVE_DIR
Canonical from here forward: $CANONICAL_DIR
EOF

log "done. canary written: $CANARY"
log "verify with:  grep -r '$CANARY' /home/zamoritacr/joao-* 2>/dev/null | wc -l"
log "(expected: 1 match per symlink pointing at canonical = 3 or more)"
