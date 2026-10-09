#!/usr/bin/env bash
# Post-bootstrap settings: Base URL, and the IPA-synced member-admins group as Authentik superusers. Re-runnable.
# Needs the LDAP source to have synced the group first. Uses the Authentik API with the bootstrap token.
set -euo pipefail
. "$(dirname "$0")/../../lib/common.sh"; load_env
require_vars AUTH_HOST IPA_GROUP_ADMINS LDAP_SYNC_CRON
API="https://$AUTH_HOST/api/v3"; T="$(secret authentik_bootstrap_token)"
ak() { curl -fsS -m 20 -H "Authorization: Bearer $T" -H "Content-Type: application/json" "$@"; }

# Re-apply the custom blueprints: files read via !File (e.g. policies/ipa_create.py) are not re-read unless the blueprint re-applies.
if [ "${DIFF_ONLY:-0}" != 1 ]; then
  for pk in $(ak "$API/managed/blueprints/?page_size=100" | jq -r '.results[] | select(.path | startswith("custom/")) | .pk'); do
    ak -X POST "$API/managed/blueprints/$pk/apply/" >/dev/null && log "re-applied blueprint $pk"
  done
fi

# LDAP sync schedule (Authentik default is every 2 h).
SCHED="$(ak "$API/tasks/schedules/?page_size=100" | jq -r '.results[] | select(.actor_name | endswith("ldap_sync")) | .id')"
if [ -n "$SCHED" ] && [ "${DIFF_ONLY:-0}" != 1 ]; then
  ak -X PATCH "$API/tasks/schedules/$SCHED/" -d "{\"crontab\":\"$LDAP_SYNC_CRON\"}" >/dev/null && log "LDAP sync schedule: $LDAP_SYNC_CRON"
fi

# Pin links and the issuer to the public URL (System > Settings > Base URL).
if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would set Base URL to https://$AUTH_HOST"
else ak -X PATCH "$API/admin/settings/" -d "{\"base_url\":\"https://$AUTH_HOST\"}" >/dev/null && log "Base URL: https://$AUTH_HOST"; fi

GROUP_JSON="$(ak "$API/core/groups/?name=$IPA_GROUP_ADMINS")"
GROUP="$(jq -r '.results[0].pk // empty' <<<"$GROUP_JSON")"
[ -n "$GROUP" ] || die "group $IPA_GROUP_ADMINS not found; run the LDAP sync first"

if [ "${DIFF_ONLY:-0}" = 1 ]; then log "would set is_superuser on group $GROUP ($IPA_GROUP_ADMINS)"; exit 0; fi
ak -X PATCH "$API/core/groups/$GROUP/" -d '{"is_superuser":true}' >/dev/null
log "group $IPA_GROUP_ADMINS: is_superuser (member admins are full Authentik administrators)"
