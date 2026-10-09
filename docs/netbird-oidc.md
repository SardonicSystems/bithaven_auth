# NetBird ↔ Authentik OIDC — what to enter in NetBird

Authentik side is built by `hosts/app01/blueprints/30-netbird-oidc.yaml` (provider `bh-netbird`, application `netbird`). Tested by `tests/oidc_test.py`.

In the NetBird dashboard: Settings → Identity Providers → Add Identity Provider → Generic OIDC.

| Field | Value |
|---|---|
| Issuer | `https://<AUTH_HOST>/application/o/netbird/` |
| Client ID | `netbird` |
| Client secret | contents of `secrets/netbird_oidc_client_secret` |

Copy the Redirect URL NetBird shows and confirm it equals `https://<NETBIRD_HOST>/oauth2/callback` (registered in Authentik, strict match; the logout callback is also registered).

Settings → Groups: enable JWT group sync, claim `groups`.

Only groups matching `NETBIRD_GROUPS_REGEX` are sent (lab `^bh_netbird_`, club `^netbird_`), and only users holding such a group can sign in to the application.
