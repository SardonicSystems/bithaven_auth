#!/usr/bin/env bash
# Shared helpers. Source from scripts: . "$(dirname "$0")/../lib/common.sh"
# ENV_FILE selects the env (default bithaven.env). DIFF_ONLY=1 shows changes without applying them.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/bithaven.env}"

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { log "WARN: $*" >&2; }
die()  { log "ERROR: $*" >&2; exit 1; }

load_env() {
  [ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE"
  set -a; . "$ENV_FILE"; set +a
}

# require_vars VAR... : fail if any is unset or empty.
require_vars() {
  local v missing=()
  for v in "$@"; do [ -n "${!v:-}" ] || missing+=("$v"); done
  [ "${#missing[@]}" -eq 0 ] || die "missing in $ENV_FILE: ${missing[*]}"
}

# secret NAME : print the contents of secrets/NAME; never logs it.
secret() {
  local f="${BH_SECRETS_DIR:-$REPO_ROOT/secrets}/$1"
  [ -s "$f" ] || die "secrets/$1 missing or empty"
  local v; v="$(cat "$f")"; printf '%s' "$v"   # strips trailing newlines
}

# render SRC VARS : envsubst only the listed vars ("A B C"), so other $ stay intact.
render() {
  local src="$1" vars="" v
  for v in $2; do vars+="\${$v} "; done
  envsubst "$vars" < "$src"
}

# install_rendered SRC DEST "VARS" [MODE] : idempotent; with DIFF_ONLY=1 prints the diff only.
install_rendered() {
  local src="$1" dest="$2" vars="$3" mode="${4:-0644}" tmp
  tmp="$(mktemp)"; render "$src" "$vars" > "$tmp"
  if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then rm -f "$tmp"; return 0; fi
  if [ "${DIFF_ONLY:-0}" = 1 ]; then
    diff -u "$dest" "$tmp" 2>/dev/null || cat "$tmp"; rm -f "$tmp"; return 0
  fi
  install -D -m "$mode" "$tmp" "$dest"; rm -f "$tmp"; log "wrote $dest"
}

# apply CMD... : run, or just print under DIFF_ONLY=1.
apply() {
  if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would run: $*"; else "$@"; fi
}

# host_ssh IP CMD... : ssh to a target host (SSH_USER default root; key from SSH_KEY or BH_SSH_KEY).
host_ssh() {
  local ip="$1"; shift; local k="${SSH_KEY:-${BH_SSH_KEY:-}}"
  ssh ${k:+-i "${k/#\~/$HOME}"} -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "${SSH_USER:-root}@$ip" "$@"
}

# push_file IP SRC DEST [MODE] [OWNER:GROUP] : copy SRC to DEST on the host if different; DIFF_ONLY=1 shows the diff.
push_file() {
  local ip="$1" src="$2" dest="$3" mode="${4:-0644}" own="${5:-root:root}" cur
  cur="$(mktemp)"; host_ssh "$ip" "cat '$dest' 2>/dev/null" </dev/null > "$cur" || true
  if cmp -s "$src" "$cur"; then rm -f "$cur"; return 0; fi
  case "$mode" in 0600|0640|0400|0440) if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would update $dest (secret, contents hidden)"; rm -f "$cur"; return 0; fi ;; esac
  if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would update $dest"; diff -u "$cur" "$src" | sed 's/^\(.*\)$/    \1/' | head -40 || true; rm -f "$cur"; return 0; fi
  host_ssh "$ip" "install -D -m $mode -o ${own%%:*} -g ${own##*:} /dev/stdin '$dest'" < "$src"; rm -f "$cur"; log "wrote $ip:$dest"
}
