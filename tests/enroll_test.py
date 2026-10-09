#!/usr/bin/env python3
"""Drive the BitHaven enrollment flow end to end over HTTP. Run from the repo root: tests/enroll_test.py [bithaven.env].
Needs secrets/authentik_bootstrap_token and Mailpit reachable (lab). Creates bhtest-* users; remove with freeipa/10-undo-lab.sh.
Never prints the test password."""
import base64, hashlib, hmac, json, os, re, secrets, struct, sys, time, urllib.parse
import requests

ENV = sys.argv[1] if len(sys.argv) > 1 else "bithaven.env"
env = {}
for line in open(ENV):
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1); env[k] = v.strip().strip('"')
SD = env.get("BH_SECRETS_DIR") or "secrets"
TOKEN = open(f"{SD}/authentik_bootstrap_token").read().strip()
AUTH = f"https://{env['AUTH_HOST']}"
MAILPIT = f"http://{env['APP_IP']}:8025"
IPA = f"https://{env['IPA_HOST']}/ipa"
CA = env.get("IPA_CA_CERT_PATH", "secrets/ipa-ca.crt")
API = {"Authorization": f"Bearer {TOKEN}"}
results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

def totp(secret_b32, t=None):
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    c = struct.pack(">Q", int((t or time.time()) // 30))
    h = hmac.new(key, c, hashlib.sha1).digest(); o = h[-1] & 0xF
    return f"{(struct.unpack('>I', h[o:o+4])[0] & 0x7FFFFFFF) % 10**6:06d}"

def mail_for(addr, subject_part, since, wait=120):
    for _ in range(wait):
        r = requests.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{addr}"}, timeout=10).json()
        for m in r.get("messages", []):
            if subject_part.lower() in m["Subject"].lower() and m["Created"] >= since:
                body = requests.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=10).json()
                return (body.get("Text") or "") + "\n" + (body.get("HTML") or "")
        time.sleep(1)
    return None

def link_in(body, key):
    for u in re.findall(r"https?://[^\s\"'<>]+", body):
        u = u.replace("&amp;", "&")
        if key in u: return u
    return None

def flow_id():
    return requests.get(f"{AUTH}/api/v3/flows/instances/bithaven-enrollment/", headers=API, timeout=20).json()["pk"]

def make_invite(email, name_suffix, **kw):
    body = {"name": f"bhtest-{name_suffix}", "flow": flow_id(), "single_use": True, "fixed_data": {"email": email}}
    body.update(kw)
    r = requests.post(f"{AUTH}/api/v3/stages/invitation/invitations/", headers=API, json=body, timeout=20)
    r.raise_for_status(); return r.json()["pk"]

def send_invite(pk, email):
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 5))
    r = requests.post(f"{AUTH}/api/v3/stages/invitation/invitations/{pk}/send_email/", headers=API, json={"email_addresses": [email]}, timeout=20)
    return r, since

class Flow:
    def __init__(self, query):
        self.s = requests.Session(); self.q = query; self.url = f"{AUTH}/api/v3/flows/executor/bithaven-enrollment/"
        self.trace = []
    def get(self, query=None):
        r = self.s.get(self.url, params={"query": query or self.q}, timeout=30); return r.json()
    def post(self, data):
        r = self.s.post(self.url, params={"query": self.q}, json=data, timeout=60); return r.json()

def run_enrollment(itoken, user, email, password, fields_override=None, stop_at=None):
    f = Flow(f"itoken={itoken}")
    ch = f.get(); verify_link = None; totp_secret = None; done = False
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 5))
    for _ in range(14):
        comp = ch.get("component", "?"); f.trace.append(comp)
        if stop_at and comp == stop_at: return f, ch, None
        if comp == "ak-stage-prompt":
            data = {"component": comp, "name": f"Test {user}", "username": user, "email": email,
                    "password": password, "password_repeat": password, "aup": True}
            data.update(fields_override or {})
            ch = f.post(data)
            if ch.get("response_errors"): return f, ch, None
        elif comp == "ak-stage-email":
            body = mail_for(email, "confirm", since)
            if not body: f.trace.append("NO-VERIFY-MAIL"); return f, ch, None
            link = link_in(body, "flow_token")
            if not link: f.trace.append("NO-VERIFY-LINK"); return f, ch, None
            verify_link = link
            tok = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)["flow_token"][0]
            ch = f.get(f"flow_token={tok}")
        elif comp == "ak-stage-authenticator-totp":
            cfg = ch.get("config_url", "")
            sec = urllib.parse.parse_qs(urllib.parse.urlparse(cfg).query).get("secret", [""])[0]
            totp_secret = sec
            ch = f.post({"component": comp, "code": totp(sec)})
        elif comp in ("xak-flow-redirect", "xak-flow-shell"):
            done = True; break
        elif comp in ("ak-stage-access-denied", "ak-stage-flow-error"):
            break
        else:
            body = {"component": comp}
            if "token" in ch: body["token"] = ch["token"]   # consent / confirmation steps
            ch = f.post(body)
    return f, ch, {"verify_link": verify_link, "totp": totp_secret, "done": done}

def ipa_login(user, password):
    r = requests.post(IPA + "/session/login_password", data={"user": user, "password": password},
                      headers={"Referer": IPA, "Accept": "text/plain"}, verify=CA, timeout=20)
    return r.status_code, r.headers.get("X-IPA-Rejection-Reason", "")

if __name__ == "__main__":
    stamp = time.strftime("%m%d%H%M%S"); user = f"bhtest-{stamp}"; email = f"{user}@{env['INTERNAL_DOMAIN']}"
    pw = secrets.token_urlsafe(14)
    # 1. invitation email
    inv = make_invite(email, stamp)
    r, since = send_invite(inv, email)
    check("invitation send_email accepted", r.status_code in (200, 204), str(r.status_code))
    body = mail_for(email, "", since)
    check("invitation email arrived in Mailpit", body is not None)
    link = link_in(body or "", "itoken")
    check("invitation link present", bool(link), urllib.parse.urlparse(link).netloc if link else "")
    if link:
        check("invitation link uses the PUBLIC auth host", urllib.parse.urlparse(link).netloc == env["AUTH_HOST"], urllib.parse.urlparse(link).netloc)
    itoken = urllib.parse.parse_qs(urllib.parse.urlparse(link or "").query).get("itoken", [""])[0]
    # 2. full happy path
    f, ch, info = run_enrollment(itoken, user, email, pw)
    check("flow completed (redirect to login)", bool(info and info["done"]), " > ".join(f.trace))
    check("verification email link followed", bool(info and info["verify_link"]))
    check("TOTP enrolled", bool(info and info["totp"]))
    st, why = ipa_login(user, pw)
    check("IPA accepts the new password immediately (no forced change)", st == 200, f"http {st} {why}")
    # 2b. the enrolled user must be linked to the LDAP source and in the members group immediately
    au = requests.get(f"{AUTH}/api/v3/core/users/", params={"username": user}, headers=API, timeout=20).json()["results"]
    check("enrolled user exists and is active in Authentik", bool(au) and au[0]["is_active"])
    if au:
        groups = [g["name"] for g in au[0].get("groups_obj", [])]
        check("enrolled user is in the members group right away", env["IPA_GROUP_MEMBERS"] in groups, ",".join(groups))
        conn = requests.get(f"{AUTH}/api/v3/sources/user_connections/ldap/", params={"user": au[0]["pk"]}, headers=API, timeout=20).json().get("results", [])
        check("enrolled user is linked to the LDAP source", len(conn) == 1, f"{len(conn)} connection(s)")
    # 3. negative cases (each with a fresh single-use invite)
    def neg(label, ov, expect_in_trace=None):
        e2 = f"bhtest-n{secrets.token_hex(3)}@{env['INTERNAL_DOMAIN']}"; i2 = make_invite(e2, secrets.token_hex(3))
        ff, cc, _ = run_enrollment(i2, f"bhtest-n{secrets.token_hex(3)}", e2, pw, ov)
        errs = cc.get("response_errors") or {}
        check(label, bool(errs) or "NO-VERIFY-MAIL" in ff.trace or cc.get("component") == "ak-stage-access-denied", json.dumps(errs)[:120])
    neg("short password rejected", {"password": "abc", "password_repeat": "abc"})
    neg("password mismatch rejected", {"password_repeat": pw + "x"})
    neg("AUP unchecked rejected", {"aup": False})
    neg("bad username rejected", {"username": "Bad Name!"})
    # email must match the invitation
    ie = f"bhtest-i{secrets.token_hex(3)}@{env['INTERNAL_DOMAIN']}"; i3 = make_invite(ie, secrets.token_hex(3))
    ff, cc, _ = run_enrollment(i3, f"bhtest-m{secrets.token_hex(3)}", f"bhtest-other{secrets.token_hex(2)}@{env['INTERNAL_DOMAIN']}", pw)
    check("email different from the invitation is rejected", cc.get("component") == "ak-stage-access-denied", " > ".join(ff.trace[-3:]))
    # IPA refuses (uid already exists in IPA): flow must abort, no Authentik-only user left behind
    dup = env["IPA_SYNC_UID"]; de = f"bhtest-d{secrets.token_hex(3)}@{env['INTERNAL_DOMAIN']}"; i4 = make_invite(de, secrets.token_hex(3))
    ff, cc, _ = run_enrollment(i4, dup, de, pw)
    left = requests.get(f"{AUTH}/api/v3/core/users/", params={"username": dup}, headers=API, timeout=20).json()["results"]
    check("IPA failure aborts the flow", cc.get("component") == "ak-stage-access-denied", " > ".join(ff.trace[-3:]))
    check("IPA failure leaves no Authentik-only user", len(left) == 0, f"{len(left)} found")
    # reuse of the single-use invitation
    f2 = Flow(f"itoken={itoken}"); c2 = f2.get()
    check("used single-use invitation is refused", c2.get("component") != "ak-stage-prompt", c2.get("component", "?"))
    # 2c. the password must not appear in Authentik's logs or in any email
    import subprocess
    key = os.path.expanduser(env.get("BH_SSH_KEY", "~/.ssh/id_ed25519"))
    logs = subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", f"root@{env['APP_IP']}", "cd /opt/stacks/authentik && docker compose logs --since 20m 2>&1"],
                          capture_output=True, text=True, timeout=120).stdout
    mails = requests.get(f"{MAILPIT}/api/v1/messages", params={"limit": 200}, timeout=20).json().get("messages", [])
    in_mail = any(pw in (requests.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=20).text) for m in mails[:60])
    check("password does not appear in Authentik logs", len(logs) > 1000 and pw not in logs, f"scanned {len(logs)//1024} KiB")
    check("password does not appear in any email", not in_mail)
    # 4. a sync after enrollment must not complain about the enrolled users
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    sched = [x["id"] for x in requests.get(f"{AUTH}/api/v3/tasks/schedules/", params={"page_size": 100}, headers=API, timeout=20).json()["results"] if x["actor_name"].endswith("ldap_sync")]
    if sched:
        requests.post(f"{AUTH}/api/v3/tasks/schedules/{sched[0]}/send/", headers=API, timeout=20); time.sleep(45)
        ev = requests.get(f"{AUTH}/api/v3/events/events/", params={"action": "configuration_error", "ordering": "-created", "page_size": 20}, headers=API, timeout=20).json()["results"]
        bad = [e for e in ev if e["created"][:19] >= t0 and "Failed to create user" in str(e.get("context"))]
        check("LDAP sync after enrollment raises no user errors", not bad, f"{len(bad)} error event(s)")
    failed = [n for n, ok, _ in results if not ok]
    out = f"results/enroll-{time.strftime('%Y%m%d-%H%M%S')}.md"
    os.makedirs("results", exist_ok=True)
    with open(out, "w") as fh:
        fh.write(f"# Enrollment flow test {time.strftime('%Y-%m-%d %H:%M:%S')}\n\nTest user: {user}\n\n| Result | Check | Detail |\n|---|---|---|\n")
        for n, ok, d in results: fh.write(f"| {'PASS' if ok else 'FAIL'} | {n} | {d.replace('|','/')} |\n")
    print(f"\n{len(results)-len(failed)}/{len(results)} passed -> {out}"); sys.exit(1 if failed else 0)
