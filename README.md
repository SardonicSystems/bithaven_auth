# BitHaven auth

Scripts and templates that deploy a sign-in stack for a club: Caddy (public HTTPS), Authentik (accounts, MFA, invitations) and a link to FreeIPA, with an OIDC provider for NetBird. Every site-specific value lives in one env file (`bithaven.env`), copied from `bithaven.env.example`.

Read in this order:

1. `docs/club-prerequisites.md`: what the club must prepare (network, DNS, Cloudflare token, FreeIPA values, mail, two hosts).
2. `docs/club-deploy.md`: the deploy steps, the handoff to hand administration, and day-two procedures.
3. `docs/netbird-oidc.md`: connect NetBird to Authentik.

Status: built and tested at one site. The deploy guide lists what was not tested (hairpin NAT, real SMTP, the NetBird side, the handoff script on a live stack). The tests in `tests/` need the Authentik bootstrap token and, for the enrollment test, a mailbox they can read (the original site used Mailpit).

License: MIT, see `LICENSE`.
