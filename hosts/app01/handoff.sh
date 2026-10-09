#!/usr/bin/env bash
# End of bootstrap: stop the repo from managing Authentik. After this, admins change Authentik by hand in its UI.
# Deletes the custom blueprint instances (the objects they made stay), moves the blueprint files out of the mounted
# directory, and removes the bootstrap token and password from the stack .env. Needs CONFIRM_HANDOFF=1. Honors DIFF_ONLY=1.
# Do not run install.sh or post-sync.sh for app01 afterwards: they re-push the blueprints and re-take control.
set -euo pipefail
. "$(dirname "$0")/../../lib/common.sh"; load_env
require_vars AUTH_HOST APP_IP
API="https://$AUTH_HOST/api/v3"; T="$(secret authentik_bootstrap_token)"; STACK=/opt/stacks/authentik
ak() { curl -fsS -m 20 -H "Authorization: Bearer $T" "$@"; }
PKS="$(ak "$API/managed/blueprints/?page_size=100" | jq -r '.results[] | select(.path | startswith("custom/")) | .pk')"
if [ "${DIFF_ONLY:-0}" = 1 ]; then
  log "would delete $(wc -w <<<"$PKS") custom blueprint instances, move $STACK/blueprints to $STACK/blueprints-archive, and remove AUTHENTIK_BOOTSTRAP_* from $STACK/.env"; exit 0
fi
[ "${CONFIRM_HANDOFF:-0}" = 1 ] || die "this ends repo management of Authentik; set CONFIRM_HANDOFF=1 to continue"
for pk in $PKS; do ak -X DELETE "$API/managed/blueprints/$pk/" >/dev/null && log "deleted blueprint instance $pk"; done
host_ssh "$APP_IP" "cd $STACK && rm -rf blueprints-archive && mv blueprints blueprints-archive && mkdir blueprints \
  && sed -i '/^AUTHENTIK_BOOTSTRAP_/d' .env && docker compose up -d 2>&1 | tail -3"
log "handoff done: Authentik is now managed by hand. Rotate the Authentik admin password. Blueprints are archived in $STACK/blueprints-archive."
