#!/usr/bin/env bash
# Install Docker and the Authentik stack on app01 from env. Re-runnable. DIFF_ONLY=1 shows file changes only.
set -euo pipefail
. "$(dirname "$0")/../../lib/common.sh"; load_env
require_vars APP_IP AUTHENTIK_VERSION POSTGRES_VERSION ACME_EMAIL SMTP_HOST SMTP_PORT SMTP_FROM SMTP_TLS \
  IPA_HOST IPA_BASEDN IPA_GROUP_MEMBERS IPA_GROUP_ADMINS IPA_SYNC_UID IPA_PROVISION_UID AUP_URL NETBIRD_HOST NETBIRD_GROUPS_REGEX DEBIAN_RELEASE
HERE="$(dirname "$0")"; IP="$APP_IP"; STACK=/opt/stacks/authentik
SD="${BH_SECRETS_DIR:-$REPO_ROOT/secrets}"
PLACEHOLDER=0; { [ "${DIFF_ONLY:-0}" = 1 ] || [ -n "${RENDER_ONLY:-}" ]; } && PLACEHOLDER=1   # no real secrets, no host changes

gen() {  # gen NAME CMD... : create secrets/NAME once, never printed
  [ -s "$SD/$1" ] && return 0
  [ "$PLACEHOLDER" = 1 ] && { log "would generate secrets/$1"; return 0; }
  shift_name="$1"; shift; (umask 077; "$@" | tr -d '\n' > "$SD/$shift_name"); log "generated secrets/$shift_name"
}
gen authentik_secret_key        openssl rand -base64 48
gen authentik_pg_pass           openssl rand -hex 24
gen authentik_bootstrap_password openssl rand -hex 16
gen authentik_bootstrap_token   openssl rand -hex 32
gen netbird_oidc_client_secret  openssl rand -hex 32

if [ "$PLACEHOLDER" != 1 ]; then
  AK_SECRET_KEY="$(secret authentik_secret_key)"; AK_PG_PASS="$(secret authentik_pg_pass)"
  AK_BOOTSTRAP_PASSWORD="$(secret authentik_bootstrap_password)"; AK_BOOTSTRAP_TOKEN="$(secret authentik_bootstrap_token)"
  AK_IPA_SYNC_PASSWORD="$(secret ipa_sync_password)"; AK_IPA_PROVISION_PASSWORD="$(secret ipa_provision_password)"; AK_NETBIRD_OIDC_SECRET="$(secret netbird_oidc_client_secret)"
else AK_SECRET_KEY=x; AK_PG_PASS=x; AK_BOOTSTRAP_PASSWORD=x; AK_BOOTSTRAP_TOKEN=x; AK_IPA_SYNC_PASSWORD=x; AK_IPA_PROVISION_PASSWORD=x; AK_NETBIRD_OIDC_SECRET=x; fi
AK_SMTP_PASSWORD=""; [ -z "${SMTP_USER:-}" ] || { [ "$PLACEHOLDER" = 1 ] && AK_SMTP_PASSWORD=x || AK_SMTP_PASSWORD="$(secret smtp_password)"; }
case "$SMTP_TLS" in starttls) AK_SMTP_USE_TLS=true;  AK_SMTP_USE_SSL=false;; tls) AK_SMTP_USE_TLS=false; AK_SMTP_USE_SSL=true;;
  none) AK_SMTP_USE_TLS=false; AK_SMTP_USE_SSL=false;; *) die "SMTP_TLS must be starttls|tls|none";; esac
export AK_NETBIRD_OIDC_SECRET AK_IPA_PROVISION_PASSWORD AK_SECRET_KEY AK_PG_PASS AK_BOOTSTRAP_PASSWORD AK_BOOTSTRAP_TOKEN AK_IPA_SYNC_PASSWORD AK_SMTP_PASSWORD AK_SMTP_USE_TLS AK_SMTP_USE_SSL

# IPA CA: fetch once, pin by sha256 when IPA_CA_SHA256 is set.
CA="$SD/ipa-ca.crt"
if [ ! -s "$CA" ]; then
  [ "$PLACEHOLDER" = 1 ] || curl -fsS -m 15 "http://$IPA_HOST/ipa/config/ca.crt" -o "$CA"
fi
if [ -s "$CA" ]; then
  FP="$(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')"
  if [ -n "${IPA_CA_SHA256:-}" ]; then [ "$FP" = "${IPA_CA_SHA256,,}" ] || die "IPA CA sha256 mismatch: got $FP"
  else warn "IPA_CA_SHA256 not set; fetched CA sha256 is $FP -- verify it, then set it in $ENV_FILE"; fi
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
render "$HERE/docker-compose.yml.tmpl" "POSTGRES_VERSION AUTHENTIK_VERSION APP_IP" > "$TMP/compose.yml"
render "$HERE/env.tmpl" "AK_PG_PASS AK_SECRET_KEY ACME_EMAIL AK_BOOTSTRAP_PASSWORD AK_BOOTSTRAP_TOKEN SMTP_HOST SMTP_PORT SMTP_USER AK_SMTP_PASSWORD AK_SMTP_USE_TLS AK_SMTP_USE_SSL SMTP_FROM IPA_HOST IPA_BASEDN IPA_GROUP_MEMBERS IPA_GROUP_ADMINS IPA_SYNC_UID AK_IPA_SYNC_PASSWORD IPA_PROVISION_UID AK_IPA_PROVISION_PASSWORD AUP_URL NETBIRD_HOST NETBIRD_GROUPS_REGEX AK_NETBIRD_OIDC_SECRET" > "$TMP/env"

if [ -n "${RENDER_ONLY:-}" ]; then   # render the stack files only
  mkdir -p "$RENDER_ONLY/blueprints"; cp "$TMP/compose.yml" "$TMP/env" "$RENDER_ONLY/"
  cp -r "$HERE/blueprints/." "$RENDER_ONLY/blueprints/"; log "rendered to $RENDER_ONLY"; exit 0
fi
if [ "${DIFF_ONLY:-0}" != 1 ]; then
  host_ssh "$IP" "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq curl ca-certificates gnupg >/dev/null
    if ! command -v docker >/dev/null; then
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
      echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $DEBIAN_RELEASE stable\" > /etc/apt/sources.list.d/docker.list
      apt-get update -qq && apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
    fi
    systemctl enable --now docker >/dev/null 2>&1; docker --version; docker compose version
    mkdir -p $STACK/data $STACK/custom-templates $STACK/certs $STACK/blueprints"
fi
push_file "$IP" "$TMP/compose.yml" "$STACK/compose.yml" 0644
push_file "$IP" "$TMP/env" "$STACK/.env" 0600
[ ! -s "$CA" ] || push_file "$IP" "$CA" "$STACK/certs/ipa-ca.crt" 0644
mapfile -t BP < <(find "$HERE/blueprints" -type f \( -name '*.yaml' -o -name '*.py' \) | sort)
for b in "${BP[@]}"; do push_file "$IP" "$b" "$STACK/blueprints/${b#"$HERE"/blueprints/}" 0644; done

[ "${DIFF_ONLY:-0}" = 1 ] && exit 0
host_ssh "$IP" "cd $STACK && docker compose pull -q 2>&1 | tail -2; docker compose up -d 2>&1 | tail -6; docker compose ps --format '{{.Service}} {{.State}}'"
