#!/usr/bin/env bash
# Human-run on the IPA server as root, after `kinit admin`. Additive and prefixed; undo: 10-undo-lab.sh.
# Usage: ENV_FILE=./bithaven.env ./00-lab-setup.sh
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"
load_env
require_vars IPA_REALM IPA_BASEDN IPA_GROUP_MEMBERS IPA_GROUP_ADMINS IPA_SYNC_UID IPA_PROVISION_UID

OUT="${BH_OUT:-/root/bithaven-ipa}"
SOCK="ldapi://%2Frun%2Fslapd-${IPA_REALM//./-}.socket"   # VERIFY path on this IPA version
UBASE="cn=users,cn=accounts,${IPA_BASEDN}"
umask 077; mkdir -p "$OUT"

ipa_try() { local o; if o="$("$@" 2>&1)"; then log "ok: $1 $2 ${3:-}"; else
  case "$o" in *"already exists"*|*"no modifications"*|*"This entry is already a member"*) log "present: $2 ${3:-}";;
    *) die "failed: $* :: $o";; esac; fi; }

set_password() {  # set_password UID : random password via ldapi as root, so it is not expired
  local uid="$1" f="$OUT/${1}_password"
  [ -s "$f" ] || openssl rand -base64 24 | tr -d '\n' > "$f"
  ldappasswd -Y EXTERNAL -H "$SOCK" -T "$f" "uid=${uid},${UBASE}" >/dev/null
  log "password set for ${uid} -> $f"
}

ipa_try ipa group-add "$IPA_GROUP_MEMBERS" --desc="BitHaven NetBird members"
ipa_try ipa group-add "$IPA_GROUP_ADMINS"  --desc="BitHaven NetBird member admins"
ipa_try ipa group-add-member "$IPA_GROUP_MEMBERS" --groups="$IPA_GROUP_ADMINS"

for uid in "$IPA_SYNC_UID" "$IPA_PROVISION_UID"; do
  ipa_try ipa user-add "$uid" --first=BH --last=Service --shell=/sbin/nologin
  ipa_try ipa group-add-member service_accounts --users="$uid"
  set_password "$uid"
done

ipa_try ipa permission-add bh_group_members_write --right=write --attrs=member \
  --subtree="cn=groups,cn=accounts,${IPA_BASEDN}" --filter="(cn=${IPA_GROUP_MEMBERS})"
ipa_try ipa privilege-add bh_user_provision --desc="BitHaven: add users, edit one group's members"
ipa_try ipa privilege-add-permission bh_user_provision \
  --permissions="System: Add Users" --permissions=bh_group_members_write
ipa_try ipa role-add bh_provisioner --desc="BitHaven provisioning"
ipa_try ipa role-add-privilege bh_provisioner --privileges=bh_user_provision
ipa_try ipa role-add-member bh_provisioner --users="$IPA_PROVISION_UID"

PDN="uid=${IPA_PROVISION_UID},${UBASE}"
if ldapsearch -Y EXTERNAL -H "$SOCK" -LLL -b "cn=ipa_pwd_extop,cn=plugins,cn=config" -s base passSyncManagersDNs 2>/dev/null \
   | grep -qiF "passSyncManagersDNs: ${PDN}"; then log "present: passSyncManagersDNs"; else
  ldapmodify -Y EXTERNAL -H "$SOCK" >/dev/null <<LDIF
dn: cn=ipa_pwd_extop,cn=plugins,cn=config
changetype: modify
add: passSyncManagersDNs
passSyncManagersDNs: ${PDN}
LDIF
  log "added passSyncManagersDNs: ${PDN}"
fi

log "IPA global password policy (Authentik's policy must match):"
ipa pwpolicy-show
log "Done. Passwords are in $OUT (mode 600). Copy them to the lab host's secrets/ by hand; do not paste them anywhere."
