#!/usr/bin/env python3
"""Measure how long a member keeps NetBird access after removal from the IPA members group.
Enrolls a fresh bhtest member, prints the one IPA command a human must run, then watches IPA and Authentik and
reports the delay. Run from the repo root: tests/revocation_test.py [bithaven.env]. Status is written to results/revocation.status."""
import os, json, re, secrets, sys, time, urllib.parse
import requests
sys.argv = [sys.argv[0], sys.argv[1] if len(sys.argv) > 1 else "bithaven.env"]
sys.path.insert(0, "tests")
import enroll_test as t
import importlib; oidc_src = open("tests/oidc_test.py").read().split("stamp = secrets.token_hex(3)")[0]
ns = {"__name__": "oidc_helpers"}; sys.argv = ["x", t.ENV]; exec(compile(oidc_src, "oidc_helpers", "exec"), ns)
env = t.env; STATUS = "results/revocation.status"
def say(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"; print(line, flush=True); open(STATUS, "a").write(line + "\n")
os.makedirs("results", exist_ok=True); open(STATUS, "w").close()

stamp = time.strftime("%m%d%H%M%S"); user = f"bhtest-rev{stamp}"; email = f"{user}@{env['INTERNAL_DOMAIN']}"; pw = secrets.token_urlsafe(14)
inv = t.make_invite(email, "rev" + stamp); r, since = t.send_invite(inv, email)
itoken = urllib.parse.parse_qs(urllib.parse.urlparse(t.link_in(t.mail_for(email, "", since), "itoken")).query)["itoken"][0]
f, ch, info = t.run_enrollment(itoken, user, email, pw)
assert info and info["done"], "enrollment failed: " + " > ".join(f.trace)
totp_secret = info["totp"]
say(f"ENROLLED {user}")

def ak_groups():
    u = requests.get(f"{t.AUTH}/api/v3/core/users/", params={"username": user}, headers=t.API, timeout=20).json()["results"]
    return [g["name"] for g in u[0]["groups_obj"]] if u else None
def ipa_members():
    s = requests.Session(); s.verify = t.CA
    s.post(t.IPA + "/session/login_password", data={"user": env["IPA_PROVISION_UID"], "password": open(f"{t.SD}/ipa_provision_password").read().strip()},
           headers={"Referer": t.IPA, "Accept": "text/plain"}, timeout=20)
    j = s.post(t.IPA + "/session/json", json={"method": "group_show", "params": [[env["IPA_GROUP_MEMBERS"]], {"version": "2.251"}], "id": 0},
               headers={"Referer": t.IPA, "Accept": "application/json", "Content-Type": "application/json"}, timeout=20).json()
    return (j.get("result") or {}).get("result", {}).get("member_user", [])
mg = env["IPA_GROUP_MEMBERS"]
assert mg in (ak_groups() or []) and user in ipa_members(), "member not present in both IPA and Authentik before the test"
say(f"READY: {user} is in {mg} in both IPA and Authentik")
say(f"ACTION: on ipa01 (after kinit admin) run:  ipa group-remove-member {mg} --users={user}")
t0 = time.time(); t_ipa = None; t_ak = None
while time.time() - t0 < 2400 and t_ak is None:
    if t_ipa is None and user not in ipa_members():
        t_ipa = time.time(); say("IPA no longer lists the user (removal seen)")
    if t_ipa is not None and mg not in (ak_groups() or []):
        t_ak = time.time(); gone = ak_groups() is None
        say(f"Authentik {'deleted the user' if gone else 'removed the group'} {t_ak - t_ipa:.0f}s after IPA")
    time.sleep(5 if t_ipa else 3)
if t_ak is None: say("TIMEOUT: removal not observed within 40 minutes"); sys.exit(2)
class Denied(Exception): pass
def login_totp():
    s = requests.Session(); url = f"{ns['API']}/flows/executor/default-authentication-flow/"; ch = s.get(url, timeout=30).json()
    for _ in range(8):
        c = ch.get("component")
        if c == "ak-stage-identification": ch = s.post(url, json={"component": c, "uid_field": user}, timeout=30).json()
        elif c == "ak-stage-password": ch = s.post(url, json={"component": c, "password": pw}, timeout=30).json()
        elif c == "ak-stage-authenticator-validate":
            dev = ch["device_challenges"][0]; ch = s.post(url, json={"component": c, "code": t.totp(totp_secret)}, timeout=30).json()
        elif c in ("xak-flow-redirect", "xak-flow-shell"): return s
        elif c in ("ak-stage-access-denied", "ak-stage-flow-error"): raise Denied(c)
        else: raise SystemExit(f"unexpected login stage {c}")
    return s
try: s = login_totp(); st, loc, _ = ns["authorize"](s); denied = st == 403 or "code=" not in loc
except Denied: denied = True
say(f"NetBird sign-in after removal: {'DENIED' if denied else 'STILL ALLOWED'}")
say(f"RESULT revocation delay IPA->Authentik {t_ak - t_ipa:.0f}s; NetBird access {'revoked' if denied else 'NOT revoked'}")
