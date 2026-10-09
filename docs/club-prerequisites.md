# BitHaven club prerequisites

Goal: prepare the club network, DNS, Cloudflare, FreeIPA, mail and two hosts, then collect the values for `bithaven.env`.
Audience: the club members who own the router, DNS, Cloudflare account, FreeIPA server and mail relay.
When done: give the filled-in sheet in Part I to the person who runs `docs/club-deploy.md`.

Menu names differ between routers and DNS servers. Where a step names a menu, use the closest match on your device.

## Who does what

| Part | Owner | Result |
|---|---|---|
| A | All | Names and addresses decided |
| B | Network admin | Static addresses, firewall rules, NAT |
| C | DNS admin | External and internal DNS records |
| D | Cloudflare account owner | A DNS token |
| E | FreeIPA admin | IPA values and a reachable server |
| F | Mail admin | An SMTP relay |
| G | Whoever runs the hosts | Two Debian 12 hosts with SSH |
| H | Network admin | Connectivity checks pass |
| I | All | The sheet of values |

## Part A. Decide the names and addresses

Goal: fix every name and address before anyone changes a device.
Where: a shared document, such as the sheet in Part I.

1. Choose the public name for sign-in. Example: `auth.example.org`. This is `AUTH_HOST`.

Expected: a fully qualified name in a domain the club owns.
If not: buy or choose a domain first.

2. Choose the public name for NetBird. Example: `netbird.example.org`. This is `NETBIRD_HOST`.

Expected: a second name in the same domain.
If not: use another name in the club domain.

3. Choose one static address for proxy01 and one for app01. Both must be on the same LAN, outside the DHCP pool.

Expected: two unused addresses. Example: `192.168.1.20` and `192.168.1.21`.
If not: choose other addresses and check them again in Part B.

4. Find the club's public IP address. Note if it is static or dynamic.

Expected: one public IPv4 address.
If not: ask the internet provider. A dynamic address needs dynamic DNS in Part C.

## Part B. Network admin: addresses, firewall, NAT

Goal: give both hosts fixed addresses and allow the traffic in the table.
Where: the club router or firewall, as an administrator.
Before you start: Part A is done.
Blast radius: a wrong rule can expose the hosts or cut off other services that use the same ports.
Rollback: remove the rules and the reservations you added.

1. On the router, check that the two chosen addresses are unused. Ping each one from a LAN machine.

```bash
ping -c 2 -W 2 <HOST_ADDRESS>
```

`<HOST_ADDRESS>` is each address from Part A, step 3. Run the command once for each address.
Expected: `100% packet loss`.
If not: choose another address.

2. On the router, reserve both addresses so DHCP never hands them out. Remove them from the pool or add reservations. Use the MAC address after Part G, or exclude the range now.

Expected: both addresses are outside the DHCP pool.
If not: shrink the pool in the router's DHCP settings.

3. On the router, add the port forwards from the public IP address to proxy01.

| Public port | Protocol | Forward to | Purpose |
|---|---|---|---|
| 443 | TCP | proxy01, port 443 | Sign-in and NetBird, over HTTPS |
| 80 | TCP | proxy01, port 80 | Redirect to HTTPS. Needed for certificates only if `ACME_CHALLENGE=http01` |
| 3478 | UDP | The NetBird server directly, port 3478 | STUN. A web proxy cannot carry UDP, so do not send it to proxy01 |

Expected: both rules show as enabled.
If not: check that no other service already uses port 80 or 443 on the router.

4. On the router, allow the traffic in this table between internal hosts. Block everything else by default.

| From | To | Port | Protocol | Purpose |
|---|---|---|---|---|
| proxy01 | app01 | 9000 | TCP | Reverse proxy to Authentik |
| app01 | IPA server | 443 | TCP | IPA API, used at enrollment |
| app01 | IPA server | 636 | TCP | LDAPS user and group sync |
| control host | IPA server | 80 | TCP | One-time download of the IPA CA certificate |
| control host | proxy01, app01 | 22 | TCP | SSH for the install scripts |
| control host | `AUTH_HOST` public name | 443 | TCP | The scripts call the Authentik API by public name |
| proxy01, app01 | internet | 80, 443 | TCP | Package downloads, container images, certificate issuing |
| proxy01, app01 | DNS resolvers | 53 | UDP and TCP | Name resolution |
| app01 | SMTP relay | `SMTP_PORT` | TCP | Invitation and verification emails |
| proxy01 | NetBird server and dashboard | their listen ports (default 8081 and 8080) | TCP | Only if the two NetBird upstream values are set |

Expected: each row is a rule or is allowed by default.
If not: add the missing rule.

5. On the router, decide how LAN clients reach the public names. Use one of two options.
- Option 1: enable NAT reflection (hairpin) for the two forwards in step 3.
- Option 2: create internal DNS records in Part C, step 4. Option 2 is simpler and more reliable.

Expected: a recorded choice.
If not: choose Option 2.

NetBird signal, management and relay traffic rides on TCP 443 (gRPC and WebSocket). UDP 3478 (STUN) is the only other port, for NetBird 0.29 and later. Clients older than 0.29 also need TCP 33073, 10000 and 33080 and UDP 49152-65535. This repository does not deploy the NetBird server, so choose its host and forward UDP 3478 to that host.

## Part C. DNS admin: external and internal records

Goal: make both public names resolve from the internet and from the LAN.
Where: the Cloudflare dashboard for the club domain, and the club internal DNS server.
Before you start: Part A is done.
Rollback: delete the records you added.

1. In Cloudflare, open the club domain. Open DNS > Records.

Expected: the record list for the domain.
If not: confirm the domain uses Cloudflare name servers.

2. Add an A record for `AUTH_HOST` that points to the club public IP address. Set Proxy status to DNS only (grey cloud).

Expected: the record shows DNS only.
If not: click the orange cloud to turn it grey.

3. Add an A record for `NETBIRD_HOST` that points to the same address. Set Proxy status to DNS only.

Expected: the record shows DNS only. The Cloudflare proxy does not carry NetBird's non-HTTP traffic.
If not: click the orange cloud to turn it grey.

If the public IP address is dynamic, run a dynamic DNS client that updates both records. Cloudflare supports API updates.

4. On the club internal DNS server, add A records for `AUTH_HOST` and `NETBIRD_HOST` that point to the proxy01 address. Skip this step if you chose NAT reflection in Part B.

Expected: LAN clients resolve both names to the proxy01 address.
If not: confirm the LAN clients use this DNS server.

5. On the internal DNS server, make sure `IPA_HOST` resolves to the IPA server address from proxy01, app01 and the control host.

Expected: the FQDN resolves to the IPA address.
If not: add the record.

## Part D. Cloudflare account owner: the DNS token

Goal: create a token that lets Caddy issue certificates by DNS-01, and nothing else.
Where: the Cloudflare dashboard, as the account owner.
Before you start: Part C is done. If the club does not use Cloudflare, skip this part, set `ACME_CHALLENGE=http01` and keep port 80 open.
Blast radius: the token can edit DNS records in one zone.
Rollback: delete the token in the dashboard.

1. In Cloudflare, open My Profile > API Tokens. Click Create Token.

Expected: a list of token templates.
If not: sign in as a user who can manage tokens.

2. Choose the Edit zone DNS template. Click Use template.

Expected: a form with the permission Zone - DNS - Edit.
If not: choose Create Custom Token and add Zone - DNS - Edit.

3. Under Zone Resources, set Include to Specific zone. Choose the club domain.

Expected: the token covers exactly one zone.
If not: do not use All zones.

4. Under TTL, set an end date if the club wants the token to expire. Click Continue to summary. Click Create Token.

Expected: Cloudflare shows the token once.
If not: create the token again.

5. Copy the token to a file named `secrets/cloudflare_dns_token` on the control host. Set the file mode to 600. Do not paste the token into chat, email or a ticket.

```bash
install -m 600 /dev/null secrets/cloudflare_dns_token
```

Open the file and paste the token. Expected: a file with one line.
If not: remove the file and repeat.

6. On the control host, test the token.

```bash
curl -s \
  -H "Authorization: Bearer $(cat secrets/cloudflare_dns_token)" \
  https://api.cloudflare.com/client/v4/user/tokens/verify
```

Expected: `"status":"active"`.
If not: create a new token.

The lab token covered one zone. The lab did not record the exact permission list. If Caddy later logs a zone lookup error, add Zone - Zone - Read to the token.

## Part E. FreeIPA admin: values and reachability

Goal: collect the IPA values and make sure LDAPS works by name.
Where: the IPA server, as root.
Before you start: Part C, step 5 is done.

1. On the IPA server, print the realm and base DN.

```bash
cat /etc/ipa/default.conf
```

Expected: lines `realm = <REALM>` and `basedn = <BASE_DN>`, and `host = <IPA_HOST>`.
If not: send the output to the person running the deploy.

2. On the IPA server, print the IPA CA fingerprint. Write it on the sheet. The deploy compares it against the downloaded certificate.

```bash
openssl x509 -in /etc/ipa/ca.crt -noout -fingerprint -sha256
```

Expected: a line `sha256 Fingerprint=<HEX>`. Remove the colons when you write it on the sheet.
If not: locate the CA file in `/etc/ipa/`.

3. Make sure the IPA server certificate names `IPA_HOST`. The sync checks the name.

Expected: `IPA_HOST` is the server's FQDN, not an alias or IP address.
If not: use the FQDN that the certificate lists.

4. Choose the names for the two IPA groups. Example: `netbird_members` and `netbird_member_admins`. The deploy creates them. Do not create them now.

Expected: two names that do not exist yet.
If not: choose other names.

Anyone you later add to the admins group becomes a full Authentik administrator.

## Part F. Mail admin: SMTP relay

Goal: let Authentik send invitation and verification emails.
Where: the club mail relay.

1. Get the relay host name, port and encryption type (`starttls`, `tls` or `none`).

Expected: three values.
If not: ask the mail provider.

2. Choose a From address on a domain that the relay is allowed to send for. Example: `noreply@example.org`.

Expected: an address the relay accepts.
If not: add SPF and DKIM records for the domain, or messages go to spam.

3. If the relay needs a login, get the user name and password. Put the password in `secrets/smtp_password` on the control host, mode 600.

Expected: a one-line file.
If not: skip this step when the relay needs no login.

## Part G. Two hosts

Goal: create proxy01 and app01 and allow SSH from the control host.
Where: the club virtualization host, or physical machines.
Before you start: Part B, step 3 is done.

1. Create the two hosts with Debian 12. The lab used these sizes.

| Host | CPU | Memory | Disk |
|---|---|---|---|
| proxy01 | 1 | 512 MB | 4 GB |
| app01 | 2 | 4 GB | 16 GB |

Expected: both hosts boot. Authentik takes about 5 minutes on its first start with 2 CPUs.
If not: add CPU or memory.

2. If a host is a container, enable nesting for app01 and keyctl for Docker. Skip this for a full VM.

Expected: Docker runs inside app01.
If not: examine the container features.

3. Give each host its static address, the gateway and the DNS resolvers from Part I.

Expected: each host answers on its address.
If not: examine the network settings.

4. Add the control host's public SSH key to `/root/.ssh/authorized_keys` on both hosts.

Expected: the control host connects as root without a password.
If not: examine the key and the SSH settings.

5. On the control host, install the tools the scripts use.

```bash
apt-get install -y curl jq gettext-base openssh-client openssl python3-requests
```

Where: the control host, as root. Expected: no error.
If not: install the missing package.

## Part H. Connectivity checks

Goal: prove every row of the Part B table before the deploy.
Where: as named in each step.
Before you start: Parts B to G are done.

1. On the control host, resolve the public name.

```bash
dig +short <AUTH_HOST>
```

`<AUTH_HOST>` is the value from Part A. Expected: the proxy01 address on the LAN (Option 2), or the public address (Option 1).
If not: examine Part C.

2. From a machine outside the club network, resolve the public name.

```bash
dig +short <AUTH_HOST>
```

Expected: the club public IP address.
If not: wait for DNS to propagate or examine Part C, step 2.

3. On the control host, connect to proxy01 and app01 by SSH.

```bash
ssh root@<HOST_ADDRESS> true
```

Run it once for each host address. Expected: no output and no prompt.
If not: examine Part G, step 4.

4. On app01, test the IPA LDAPS port.

```bash
timeout 5 bash -c 'echo > /dev/tcp/<IPA_HOST>/636'
```

`<IPA_HOST>` is the IPA server FQDN. Expected: no output.
If not: examine the firewall row for port 636.

5. On app01, test the IPA HTTPS port.

```bash
timeout 5 bash -c 'echo > /dev/tcp/<IPA_HOST>/443'
```

Expected: no output.
If not: examine the firewall row for port 443.

6. On app01, test the mail relay port.

```bash
timeout 5 bash -c 'echo > /dev/tcp/<SMTP_HOST>/<SMTP_PORT>'
```

`<SMTP_HOST>` and `<SMTP_PORT>` come from Part F. Expected: no output.
If not: examine the firewall row for the relay.

7. On proxy01, test the route to app01.

```bash
timeout 5 bash -c 'echo > /dev/tcp/<APP_IP>/9000'
```

Expected: a connection refused error is fine before the deploy. A timeout means the firewall blocks it.
If not: examine the firewall row for port 9000.

8. On the control host, test that the CA download path is open.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://<IPA_HOST>/ipa/config/ca.crt
```

Expected: `200`.
If not: examine the firewall row for port 80 to the IPA server.

## Part I. The sheet of values for `bithaven.env`

Give this table, filled in, to the person who runs `docs/club-deploy.md`. Never write a password or token in it.

| Key | Meaning | Source |
|---|---|---|
| `AUTH_HOST` | Public sign-in name | Part A |
| `NETBIRD_HOST` | Public NetBird name | Part A |
| `INTERNAL_DOMAIN` | Club internal DNS domain, for example `example.lan` | Club DNS admin |
| `ACME_EMAIL` | Contact address for Let's Encrypt | Club |
| `ACME_CHALLENGE` | `dns01` with Cloudflare, otherwise `http01` | Part D |
| `ACME_CA` | Leave the Let's Encrypt staging URL until certificates issue | Deploy guide |
| `PROXY_IP`, `APP_IP` | Static addresses | Part A |
| `NET_PREFIX` | Prefix length, for example `24` | Router |
| `NET_GATEWAY` | Router address on the LAN | Router |
| `DNS_RESOLVERS` | Internal DNS server addresses | Part C |
| `IPA_HOST`, `IPA_IP` | IPA server FQDN and address | Part E |
| `IPA_REALM`, `IPA_BASEDN` | From `/etc/ipa/default.conf` | Part E |
| `IPA_CA_SHA256` | CA fingerprint, hex without colons | Part E |
| `IPA_GROUP_MEMBERS`, `IPA_GROUP_ADMINS` | The two group names | Part E |
| `NETBIRD_GROUPS_REGEX` | A pattern that matches both group names, for example `^netbird_` | Part E |
| `AUP_URL` | Link to the club Acceptable Use Policy | Club |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_USER` | Mail relay | Part F |
| `NETBIRD_SERVER_UPSTREAM` | `host:port` of netbird-server, for example `192.168.1.30:8081`. Leave empty until NetBird runs | NetBird admin |
| `NETBIRD_DASHBOARD_UPSTREAM` | `host:port` of the NetBird dashboard, for example `192.168.1.30:8080` | NetBird admin |

The secrets go in files, not in the sheet: the Cloudflare token (Part D), the SMTP password (Part F). The IPA service passwords are created by the deploy.
