#!/usr/bin/env bash
# Human-run on the IPA server as root, after `kinit admin`. Reverses 00-lab-setup.sh and deletes bhtest-* users.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"
load_env
require_vars IPA_REALM IPA_BASEDN IPA_GROUP_MEMBERS IPA_GROUP_ADMINS IPA_SYNC_UID IPA_PROVISION_UID

SOCK="ldapi://%2Frun%2Fslapd-${IPA_REALM//./-}.socket"
PDN="uid=${IPA_PROVISION_UID},cn=users,cn=accounts,${IPA_BASEDN}"
try() { "$@" >/dev/null 2>&1 && log "removed: $*" || log "skip: $*"; }

ldapmodify -Y EXTERNAL -H "$SOCK" >/dev/null 2>&1 <<LDIF || log "skip: passSyncManagersDNs value"
dn: cn=ipa_pwd_extop,cn=plugins,cn=config
changetype: modify
delete: passSyncManagersDNs
passSyncManagersDNs: ${PDN}
LDIF

try ipa role-del bh_provisioner
try ipa privilege-del bh_user_provision
try ipa permission-del bh_group_members_write
for u in $(ipa user-find bhtest- --pkey-only --raw 2>/dev/null | sed -n 's/^ *uid: //p'); do try ipa user-del "$u"; done
try ipa user-del "$IPA_PROVISION_UID"
try ipa user-del "$IPA_SYNC_UID"
try ipa group-del "$IPA_GROUP_ADMINS"
try ipa group-del "$IPA_GROUP_MEMBERS"
log "Undo complete."
