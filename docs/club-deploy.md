# Deploy BitHaven auth at the club

Goal: deploy Caddy, Authentik and the FreeIPA link at the club from one env file.
Where: the control host (repo checkout), the IPA server (as root), proxy01 and app01 (reached by SSH as root), and a browser.

Before you start: complete `docs/club-prerequisites.md` (network, DNS, Cloudflare token, IPA values, mail, hosts) and have its sheet of values. The list below is a summary.
- Two Debian 12 hosts exist: proxy01 and app01. Root SSH works with your key.
- FreeIPA runs at the club. You can run `kinit admin` on the IPA server.
- You have a Cloudflare (or other DNS-01) token limited to the club zone. Without DNS-01, set `ACME_CHALLENGE=http01` and open port 80.
- You have SMTP relay credentials and the club Acceptable Use Policy URL.
- The router sends ports 80 and 443 for `AUTH_HOST` and `NETBIRD_HOST` to proxy01. It supports NAT reflection (hairpin). The lab did not test this.
- app01 can reach the IPA server on ports 443 and 636. The control host can reach it on port 80.
- DNS A records for `AUTH_HOST` and `NETBIRD_HOST` point to the club public IP.
- No other network uses the club's 192.168.1.0/24 range. If NetBird routes the whole /24, a member on a home 192.168.1.x LAN loses access. Publish /32 resources or renumber. The lab did not test this.

Blast radius: steps 8 to 28 change proxy01, app01 and the IPA server. A failed step leaves the auth stack down or members unable to enroll. Other club services are not touched.
Rollback: stop the stacks on app01 and proxy01. Run `freeipa/10-undo-lab.sh` on the IPA server to remove the BitHaven objects. Delete the two hosts.

## How the scripts work

The scripts get the stack running. They read one env file and write the config for Caddy and Authentik. After step 12, administrators manage Authentik by hand in its web UI. Do not run `install.sh` or `post-sync.sh` for app01 after step 12. They would take control back.

## Prepare

1. On the control host, copy the example env file.

```bash
cp bithaven.env.example bithaven.env
```

Expected: `bithaven.env` exists.
If not: run the command from the repo root.

2. On the control host, open `bithaven.env` and set every value.
- Keep `ACME_CA` set to the Let's Encrypt staging URL until certificates issue.
- Set `INTERNAL_TLS_MODE=caddy-internal`. The setting is required but unused.
- Leave `NETBIRD_SERVER_UPSTREAM` and `NETBIRD_DASHBOARD_UPSTREAM` empty until NetBird runs. Then set both to `host:port`.
- Put group names in `IPA_GROUP_MEMBERS` and `IPA_GROUP_ADMINS`. The example uses `netbird_members` and `netbird_member_admins`.
- Leave `IPA_CA_SHA256` empty for now.

Expected: no `example.org` or `example.lan` value remains.
If not: set the remaining values.

3. On the control host, put the DNS token in `secrets/`. The file name is the last part of `ACME_DNS_TOKEN_FILE`.

```bash
install -m 600 /dev/null secrets/cloudflare_dns_token
```

Open the file and paste the token. Expected: the file has one line.
If not: remove the file and repeat this step.

4. On the control host, put the SMTP password in `secrets/smtp_password`. Skip this step if `SMTP_USER` is empty.

```bash
install -m 600 /dev/null secrets/smtp_password
```

Open the file and paste the password. Expected: the file has one line.
If not: remove the file and repeat this step.

5. On the control host, render the app01 files from your env file. This changes no host.

```bash
ENV_FILE=bithaven.env RENDER_ONLY=/tmp/bh-render hosts/app01/install.sh
```

Expected: `rendered to /tmp/bh-render`.
If not: send the error to the agent. A `missing in bithaven.env` error names the value to set.

## Create the IPA objects

6. Copy the `freeipa/` and `lib/` directories and `bithaven.env` to the IPA server. Keep the same relative layout.

Expected: `freeipa/00-lab-setup.sh`, `lib/common.sh` and `bithaven.env` exist on the IPA server.
If not: copy the missing files.

7. On the IPA server, get an admin ticket.

```bash
kinit admin
```

Expected: no output after the password prompt.
If not: check the admin password.

8. On the IPA server, from the directory that holds `freeipa/`, run the setup script.

```bash
ENV_FILE=./bithaven.env freeipa/00-lab-setup.sh
```

Expected: `Done. Passwords are in /root/bithaven-ipa`.
If not: send the last 20 lines to the agent.

The script creates the two groups, the sync and provisioning accounts, and the provisioning role. It adds the provisioning account to the IPA password-sync list. The script is safe to run again.

9. Copy the two password files from `/root/bithaven-ipa` on the IPA server to `secrets/ipa_sync_password` and `secrets/ipa_provision_password` on the control host. Use `scp`. Do not paste the passwords into chat or a ticket.

Expected: both files exist in `secrets/`, mode 600.
If not: set the mode with `chmod 600`.

## Deploy

10. On the control host, preview the proxy01 install.

```bash
DIFF_ONLY=1 ENV_FILE=bithaven.env hosts/proxy01/install.sh
```

Expected: a list of files to create, with no error.
If not: send the error to the agent.

11. On the control host, install Caddy on proxy01.

```bash
ENV_FILE=bithaven.env hosts/proxy01/install.sh
```

Expected: `Valid configuration` and `active`.
If not: send the last 20 lines to the agent.

12. On the control host, install Authentik on app01. The first run prints the IPA CA sha256.

```bash
ENV_FILE=bithaven.env hosts/app01/install.sh
```

Expected: a line `IPA_CA_SHA256 not set; fetched CA sha256 is` followed by a hash.
If not: send the last 20 lines to the agent.

13. On the IPA server, print the real CA fingerprint.

```bash
openssl x509 -in /etc/ipa/ca.crt -noout -fingerprint -sha256
```

Expected: the same value as the hash from step 12, ignoring colons and case.
If not: stop. Do not continue until the values match.

14. On the control host, set `IPA_CA_SHA256` in `bithaven.env` to the hash from step 12. Run step 12 again.

Expected: no sha256 warning. `postgresql`, `server` and `worker` show `running`.
If not: send the last 20 lines to the agent.

15. Wait about 5 minutes. Authentik finishes its first start and the first LDAP sync runs. Then check that the groups exist.

Expected: in the Authentik admin UI, Directory > Groups lists the two IPA groups.
If not: wait for the next sync, up to `LDAP_SYNC_CRON`. If the groups are still missing, send the app01 worker log to the agent.

16. On the control host, run the post-install settings.

```bash
ENV_FILE=bithaven.env hosts/app01/post-sync.sh
```

Expected: lines for the sync schedule, `Base URL` and `is_superuser`.
If not: send the last 20 lines to the agent.

17. Follow `docs/netbird-oidc.md` to connect NetBird to Authentik.

Expected: a NetBird sign-in redirects to Authentik.
If not: send the NetBird error to the agent.

## Verify

18. In a browser, open `https://<AUTH_HOST>/if/user/`. Log in as a member who is in the IPA members group. Use a private window.

Expected: after the password, Authentik shows a TOTP setup page.
If not: wait one sync interval and try again.

19. In a browser, log in as a member of the IPA member-admins group. Open `https://<AUTH_HOST>/if/admin/`.

Expected: the Authentik admin interface loads.
If not: send what the page shows to the agent.

20. Send one invitation by hand. Follow "Invite a member" below.

Expected: the invitee completes the enrollment and logs in.
If not: send the step that failed to the agent.

## Hand over to hand administration

21. On the control host, preview the handoff.

```bash
DIFF_ONLY=1 ENV_FILE=bithaven.env hosts/app01/handoff.sh
```

Expected: a line that lists the blueprint instances and the `.env` change.
If not: send the error to the agent.

22. On the control host, run the handoff.

```bash
CONFIRM_HANDOFF=1 ENV_FILE=bithaven.env hosts/app01/handoff.sh
```

Expected: `handoff done`.
If not: send the last 20 lines to the agent.

23. In the Authentik admin UI, open Directory > Users. Set a new password for the `akadmin` user.

Expected: the new password works.
If not: send the error to the agent.

24. When certificates issue, clear `ACME_CA` in `bithaven.env` on the control host.

Expected: the line reads `ACME_CA=`.
If not: edit the line.

25. On the control host, install the Caddyfile on proxy01 again.

```bash
ENV_FILE=bithaven.env hosts/proxy01/install.sh
```

Expected: `Valid configuration` and `active`.
If not: send the last 20 lines to the agent.

26. On proxy01, list the certificate directories.

```bash
ls /var/lib/caddy/.local/share/caddy/certificates
```

Expected: one directory for the staging CA and, after step 27, one for the production CA.
If not: send the output to the agent.

27. On proxy01, delete the staging certificate directory. Replace `<STAGING_DIRECTORY>` with the staging name from step 26.

```bash
rm -rf /var/lib/caddy/.local/share/caddy/certificates/<STAGING_DIRECTORY>
```

Expected: no output.
If not: examine the path from step 26.

28. On proxy01, restart Caddy. Caddy then issues production certificates.

```bash
systemctl restart caddy
```

Expected: no output. Browsers trust `https://<AUTH_HOST>` within a few minutes.
If not: examine `journalctl --unit caddy --lines 50`.

## Operate by hand

### Invite a member

Goal: let a new person enroll.
Where: the Authentik admin UI at `https://<AUTH_HOST>/if/admin/`, as an administrator. `<AUTH_HOST>` is the `AUTH_HOST` value.

1. Open Directory > Invitations. Click Create.

Expected: the invitation form.
If not: send what the page shows to the agent.

2. Set Name to a short label for the person. Set Flow to `bithaven-enrollment`. Turn on Single use.

Expected: the form shows all three values.
If not: choose the flow again.

3. In the custom attributes field, enter the person's email address.

`<INVITEE_EMAIL>` is the person's email address.

```json
{"email": "<INVITEE_EMAIL>"}
```

Expected: the form saves. The invitee must type exactly this address, or enrollment is refused.
If not: send the error text to the agent.

4. Click Create. Choose the email option, or copy the link and send it yourself.

Expected: a link that starts with `https://<AUTH_HOST>/if/flow/bithaven-enrollment/`.
If not: send what the page shows to the agent.

The invitee then fills the form, verifies the email, sets up TOTP and logs in. Their account is created in IPA.

### Make a member an administrator

1. On the IPA server, add the user to the member-admins group. `<MEMBER_ADMINS_GROUP>` is the `IPA_GROUP_ADMINS` value. `<USERNAME>` is the IPA login name.

```bash
ipa group-add-member <MEMBER_ADMINS_GROUP> --users=<USERNAME>
```

Expected: `Number of members added 1`.
If not: send the output to the agent.

After the next sync, the user is an Authentik superuser. Administrators can change flows, the LDAP source and the OIDC provider.

### Remove a member

1. On the IPA server, remove the user from the members group. `<MEMBERS_GROUP>` is the `IPA_GROUP_MEMBERS` value. `<USERNAME>` is the IPA login name. Repeat for the member-admins group if the user is in it.

```bash
ipa group-remove-member <MEMBERS_GROUP> --users=<USERNAME>
```

Expected: `Number of members removed 1`.
If not: send the output to the agent.

Authentik removes the user at the next sync, within `LDAP_SYNC_CRON` (lab: 110 s and 241 s measured). A NetBird session that is already open stays valid until it expires. The lab did not measure that.

## Not tested in the lab

- Hairpin NAT and the 192.168.1.x overlap.
- Real SMTP delivery. The lab used Mailpit.
- The NetBird dashboard side and JWT group sync.
- Lifetime of a NetBird session after removal.
- The handoff script on a live stack.
- Real DNS for `AUTH_HOST` and `NETBIRD_HOST` at the club.

