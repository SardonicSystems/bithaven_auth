#!/usr/bin/env bash
# Install Caddy (pinned, with the DNS-01 module) on proxy01 and render its Caddyfile from env. Re-runnable.
# DIFF_ONLY=1 shows file changes without applying. Run from the repo host; needs ssh to PROXY_IP.
set -euo pipefail
. "$(dirname "$0")/../../lib/common.sh"; load_env
require_vars PROXY_IP APP_IP AUTH_HOST NETBIRD_HOST ACME_EMAIL ACME_CHALLENGE INTERNAL_TLS_MODE CADDY_VERSION
HERE="$(dirname "$0")"; IP="$PROXY_IP"

case "$ACME_CHALLENGE" in
  dns01)  require_vars ACME_DNS_PROVIDER ACME_DNS_TOKEN_FILE
          [ "$ACME_DNS_PROVIDER" = cloudflare ] || die "only the cloudflare DNS module is built in (got $ACME_DNS_PROVIDER)"
          PUBLIC_TLS_BLOCK=$'\ttls {\n\t\tdns cloudflare {env.CF_API_TOKEN}\n\t\tpropagation_delay 30s\n\t\tpropagation_timeout -1\n\t}' ;;
  http01) PUBLIC_TLS_BLOCK="" ;;
  *) die "ACME_CHALLENGE must be dns01 or http01" ;;
esac
case "$INTERNAL_TLS_MODE" in
  caddy-internal) INTERNAL_TLS_BLOCK=$'\ttls internal' ;;
  file) INTERNAL_TLS_BLOCK=$'\ttls /etc/caddy/internal.crt /etc/caddy/internal.key' ;;   # VERIFY: which CA the club uses
  *) die "INTERNAL_TLS_MODE must be caddy-internal or file" ;;
esac
ACME_CA_LINE=""; [ -z "${ACME_CA:-}" ] || ACME_CA_LINE=$'\tacme_ca '"$ACME_CA"
# NetBird behind Caddy (docs.netbird.io/selfhosted/reverse-proxy): gRPC to the server over h2c, relay/API/OAuth paths to the
# server over HTTP, everything else to the dashboard. UDP 3478 (STUN) cannot be proxied: forward it to the NetBird host.
if [ -n "${NETBIRD_SERVER_UPSTREAM:-}" ] || [ -n "${NETBIRD_DASHBOARD_UPSTREAM:-}" ]; then
  require_vars NETBIRD_SERVER_UPSTREAM NETBIRD_DASHBOARD_UPSTREAM
  NETBIRD_BODY=$'\t@grpc header Content-Type application/grpc*\n\t@backend path /relay* /ws-proxy/* /api/* /oauth2/*\n'
  NETBIRD_BODY+=$'\thandle @grpc {\n\t\treverse_proxy h2c://'"$NETBIRD_SERVER_UPSTREAM"$'\n\t}\n'
  NETBIRD_BODY+=$'\thandle @backend {\n\t\treverse_proxy '"$NETBIRD_SERVER_UPSTREAM"$'\n\t}\n'
  NETBIRD_BODY+=$'\thandle {\n\t\treverse_proxy '"$NETBIRD_DASHBOARD_UPSTREAM"$'\n\t}'
else NETBIRD_BODY=$'\trespond "NetBird is not deployed here" 503'; fi
export PUBLIC_TLS_BLOCK INTERNAL_TLS_BLOCK ACME_CA_LINE NETBIRD_BODY

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
render "$HERE/Caddyfile.tmpl" "ACME_EMAIL ACME_CA_LINE PUBLIC_TLS_BLOCK INTERNAL_TLS_BLOCK NETBIRD_BODY AUTH_HOST NETBIRD_HOST APP_IP" > "$TMP/Caddyfile"
cp "$HERE/caddy.service" "$TMP/caddy.service"
if [ -n "${RENDER_ONLY:-}" ]; then   # render the config files only: no ssh, no secrets, no host changes
  mkdir -p "$RENDER_ONLY"; cp "$TMP/Caddyfile" "$TMP/caddy.service" "$RENDER_ONLY/"; log "rendered to $RENDER_ONLY"; exit 0
fi

if [ "${DIFF_ONLY:-0}" != 1 ]; then
  host_ssh "$IP" "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null
    getent group caddy >/dev/null || groupadd --system caddy
    id caddy >/dev/null 2>&1 || useradd --system -g caddy -d /var/lib/caddy -s /usr/sbin/nologin caddy
    mkdir -p /etc/caddy /var/lib/caddy; chown caddy:caddy /var/lib/caddy"
  if ! host_ssh "$IP" "/usr/local/bin/caddy version 2>/dev/null | grep -q 'v$CADDY_VERSION' && /usr/local/bin/caddy list-modules 2>/dev/null | grep -q dns.providers.cloudflare"; then
    log "installing Caddy v$CADDY_VERSION with caddy-dns/cloudflare"
    host_ssh "$IP" "curl -fsSL -o /tmp/caddy 'https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/caddy-dns/cloudflare&version=v$CADDY_VERSION' && install -m 0755 /tmp/caddy /usr/local/bin/caddy && rm -f /tmp/caddy"
  fi
  log "caddy: $(host_ssh "$IP" '/usr/local/bin/caddy version | cut -c1-60') sha256 $(host_ssh "$IP" 'sha256sum /usr/local/bin/caddy | cut -c1-16')..."
fi

if [ "$ACME_CHALLENGE" = dns01 ]; then
  printf 'CF_API_TOKEN=%s\n' "$(secret "$(basename "$ACME_DNS_TOKEN_FILE")")" > "$TMP/caddy.env"
else : > "$TMP/caddy.env"; fi
push_file "$IP" "$TMP/caddy.env" /etc/caddy/caddy.env 0640 root:caddy
push_file "$IP" "$TMP/Caddyfile" /etc/caddy/Caddyfile 0644
push_file "$IP" "$TMP/caddy.service" /etc/systemd/system/caddy.service 0644

[ "${DIFF_ONLY:-0}" = 1 ] && exit 0
host_ssh "$IP" "set -a; . /etc/caddy/caddy.env; set +a; /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | tail -2"
host_ssh "$IP" "systemctl daemon-reload && systemctl enable caddy >/dev/null 2>&1; systemctl is-active caddy >/dev/null && systemctl reload caddy || systemctl start caddy"
sleep 3; host_ssh "$IP" "systemctl is-active caddy"
