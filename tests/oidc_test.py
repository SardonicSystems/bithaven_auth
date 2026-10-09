#!/usr/bin/env python3
"""Run a real OIDC authorization-code flow against the NetBird provider and inspect the tokens.
Creates throwaway users/groups (bhtest-*) and removes them. Run from the repo root: tests/oidc_test.py [bithaven.env]"""
import os, base64, json, re, secrets, sys, time, urllib.parse
import requests
ENV = sys.argv[1] if len(sys.argv) > 1 else "bithaven.env"
env = {}
for line in open(ENV):
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1); env[k] = v.strip().strip('"')
SD = env.get("BH_SECRETS_DIR") or "secrets"
HOST = f"https://{env['AUTH_HOST']}"; API = HOST + "/api/v3"
ADM = {"Authorization": "Bearer " + open(f"{SD}/authentik_bootstrap_token").read().strip()}
CLIENT_SECRET = open(f"{SD}/netbird_oidc_client_secret").read().strip()
REDIRECT = f"https://{env['NETBIRD_HOST']}/oauth2/callback"
RX = env["NETBIRD_GROUPS_REGEX"]
results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
def adm(method, path, **kw): return requests.request(method, API + path, headers=ADM, timeout=30, **kw)

import base64, hashlib, hmac, struct, time
def totp(b32):
    key = base64.b32decode(b32.upper() + '=' * (-len(b32) % 8)); h = hmac.new(key, struct.pack('>Q', int(time.time() // 30)), hashlib.sha1).digest(); o = h[-1] & 0xF
    return f"{(struct.unpack('>I', h[o:o+4])[0] & 0x7FFFFFFF) % 10**6:06d}"
TOTP = {}   # username -> secret, captured when the login flow asks the user to set up TOTP
def login(username, password):
    s = requests.Session(); url = f"{API}/flows/executor/default-authentication-flow/"
    ch = s.get(url, timeout=30).json()
    for _ in range(8):
        c = ch.get("component")
        if c == "ak-stage-identification": ch = s.post(url, json={"component": c, "uid_field": username}, timeout=30).json()
        elif c == "ak-stage-password": ch = s.post(url, json={"component": c, "password": password}, timeout=30).json()
        elif c in ("xak-flow-redirect", "xak-flow-shell"): return s
        elif c == "ak-stage-authenticator-totp":
            sec = urllib.parse.parse_qs(urllib.parse.urlparse(ch.get("config_url", "")).query).get("secret", [""])[0]
            TOTP[username] = sec; ch = s.post(url, json={"component": c, "code": totp(sec)}, timeout=30).json()
        elif c == "ak-stage-authenticator-validate" and username in TOTP:
            ch = s.post(url, json={"component": c, "code": totp(TOTP[username])}, timeout=30).json()
        else: return None
    return s

def authorize(s, redirect=REDIRECT, scope="openid profile email groups"):
    q = urllib.parse.urlencode({"client_id": "netbird", "redirect_uri": redirect, "response_type": "code", "scope": scope, "state": "s1", "nonce": "n1"})
    r = s.get(f"{HOST}/application/o/authorize/?{q}", allow_redirects=False, timeout=30)
    loc = r.headers.get("Location", "")
    m = re.search(r"/if/flow/([^/]+)/", loc)
    if not m: return r.status_code, loc, None
    ex = f"{API}/flows/executor/{m.group(1)}/"
    ch = s.get(ex, params={"query": q}, timeout=30).json()
    for _ in range(6):
        c = ch.get("component")
        if c in ("xak-flow-redirect",): return 302, ch.get("to", ""), ch
        if c in ("ak-stage-access-denied", "ak-stage-flow-error"): return 403, c, ch
        body = {"component": c}
        if "token" in ch: body["token"] = ch["token"]
        ch = s.post(ex, params={"query": q}, json=body, timeout=30).json()
    return 0, "", ch

def jwt_payload(tok):
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4); return json.loads(base64.urlsafe_b64decode(p))

stamp = secrets.token_hex(3); pw = secrets.token_urlsafe(16)
def mkuser(name, groups):
    u = adm("POST", "/core/users/", json={"username": name, "name": name, "email": f"{name}@{env['INTERNAL_DOMAIN']}", "is_active": True, "groups": groups, "path": "bithaven"}).json()
    adm("POST", f"/core/users/{u['pk']}/set_password/", json={"password": pw}); return u
def group(name):
    g = adm("GET", f"/core/groups/?name={name}").json()["results"]
    return g[0]["pk"] if g else adm("POST", "/core/groups/", json={"name": name}).json()["pk"]
created = []
try:
    members = group(env["IPA_GROUP_MEMBERS"]); admins = group(env["IPA_GROUP_ADMINS"]); other = group(f"bhtest-other-{stamp}")
    ua = mkuser(f"bhtest-oidc-a-{stamp}", [members, admins, other]); created.append(ua["pk"])
    ub = mkuser(f"bhtest-oidc-b-{stamp}", [other]); created.append(ub["pk"])
    # discovery
    d = requests.get(f"{HOST}/application/o/netbird/.well-known/openid-configuration", timeout=20).json()
    check("discovery issuer is the public URL", d.get("issuer") == f"{HOST}/application/o/netbird/", d.get("issuer", ""))
    check("discovery endpoints use the public host", all(d.get(k, "").startswith(HOST) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri")))
    # member with netbird groups plus an unrelated group
    s = login(ua["username"], pw); check("test member can sign in", s is not None)
    code_status, loc, _ = authorize(s) if s else (0, "", None)
    check("authorize returns a code redirect to NetBird's callback", code_status == 302 and loc.startswith(REDIRECT + "?") and "code=" in loc, loc.split("?")[0])
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("code", [""])[0]
    tr = requests.post(f"{HOST}/application/o/token/", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                       "client_id": "netbird", "client_secret": CLIENT_SECRET}, timeout=30)
    check("token exchange with the client secret works", tr.status_code == 200, str(tr.status_code))
    tok = tr.json() if tr.status_code == 200 else {}
    idt = jwt_payload(tok["id_token"]) if tok.get("id_token") else {}
    groups = sorted(idt.get("groups", []))
    want = sorted(g for g in (env["IPA_GROUP_MEMBERS"], env["IPA_GROUP_ADMINS"]) if re.search(RX, g))
    check("id_token groups are exactly the NetBird groups", groups == want, ",".join(groups))
    check("unrelated group is not in the id_token", not any("bhtest-other" in g for g in groups))
    check("id_token issuer, audience and email present", idt.get("iss") == f"{HOST}/application/o/netbird/" and idt.get("aud") == "netbird" and bool(idt.get("email")), f"aud={idt.get('aud')}")
    ui = requests.get(f"{HOST}/application/o/userinfo/", headers={"Authorization": "Bearer " + tok.get("access_token", "")}, timeout=20).json()
    check("userinfo groups also limited to NetBird groups", sorted(ui.get("groups", [])) == want, ",".join(sorted(ui.get("groups", []))))
    check("wrong client secret is refused", requests.post(f"{HOST}/application/o/token/", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
          "client_id": "netbird", "client_secret": "x"}, timeout=30).status_code in (400, 401))
    st, loc2, _ = authorize(s, redirect="https://evil.example/cb")
    check("unregistered redirect URI is refused", not loc2.startswith("https://evil.example"), f"{st}")
    # user with only an unrelated group: no access
    s2 = login(ub["username"], pw); st2, loc3, _ = authorize(s2) if s2 else (0, "", None)
    check("user without a NetBird group is denied", st2 == 403 or "code=" not in loc3, f"{st2} {loc3[:40]}")
finally:
    for pk in created: adm("DELETE", f"/core/users/{pk}/")
    g = adm("GET", f"/core/groups/?name=bhtest-other-{stamp}").json().get("results", [])
    if g: adm("DELETE", f"/core/groups/{g[0]['pk']}/")
failed = [n for n, ok, _ in results if not ok]
out = f"results/oidc-{time.strftime('%Y%m%d-%H%M%S')}.md"
os.makedirs("results", exist_ok=True)
with open(out, "w") as fh:
    fh.write(f"# NetBird OIDC provider test {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n| Result | Check | Detail |\n|---|---|---|\n")
    for n, ok, d in results: fh.write(f"| {'PASS' if ok else 'FAIL'} | {n} | {d.replace('|','/')} |\n")
print(f"\n{len(results)-len(failed)}/{len(results)} passed -> {out}"); sys.exit(1 if failed else 0)
