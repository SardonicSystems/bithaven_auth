#!/usr/bin/env bash
# Delete bhtest-* users and invitations from Authentik. IPA-side test users: freeipa/10-undo-lab.sh (human).
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"; load_env
require_vars AUTH_HOST
API="https://$AUTH_HOST/api/v3"; T="$(secret authentik_bootstrap_token)"
ak() { curl -fsS -m 30 -H "Authorization: Bearer $T" "$@"; }
n=0
for pk in $(ak "$API/core/users/?search=bhtest-&page_size=200" | jq -r '.results[] | select(.username|startswith("bhtest-")) | .pk'); do
  if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would delete user $pk"; else ak -X DELETE "$API/core/users/$pk/" >/dev/null; fi; n=$((n+1))
done
for pk in $(ak "$API/stages/invitation/invitations/?page_size=200" | jq -r '.results[] | select(.name|startswith("bhtest-")) | .pk'); do
  if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would delete invitation $pk"; else ak -X DELETE "$API/stages/invitation/invitations/$pk/" >/dev/null; fi; n=$((n+1))
done
log "bhtest cleanup: $n objects"
