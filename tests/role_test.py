#!/usr/bin/env python3
"""Check that the member-admins group makes its members Authentik administrators. Creates a throwaway Authentik user in the admin group and removes it.
Run from the repo root: tests/role_test.py [bithaven.env]"""
import os, json, secrets, sys, time
import requests
ENV = sys.argv[1] if len(sys.argv) > 1 else "bithaven.env"
env = {}
for line in open(ENV):
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        k, v = line.split("=", 1); env[k] = v.strip().strip('"')
SD = env.get("BH_SECRETS_DIR") or "secrets"
AUTH = f"https://{env['AUTH_HOST']}/api/v3"
ADM = {"Authorization": "Bearer " + open(f"{SD}/authentik_bootstrap_token").read().strip()}
results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
def call(method, path, hdr, **kw): return requests.request(method, AUTH + path, headers=hdr, timeout=30, **kw)

grp = call("GET", f"/core/groups/?name={env['IPA_GROUP_ADMINS']}", ADM).json()["results"][0]["pk"]
name = f"bhtest-role-{secrets.token_hex(3)}"
u = call("POST", "/core/users/", ADM, json={"username": name, "name": name, "email": f"{name}@{env['INTERNAL_DOMAIN']}", "is_active": True, "groups": [grp], "path": "bithaven"}).json()
try:
    tok = call("POST", "/core/tokens/", ADM, json={"identifier": f"{name}-tok", "intent": "api", "user": u["pk"], "expiring": False}).json()
    key = call("GET", f"/core/tokens/{name}-tok/view_key/", ADM).json()["key"]
    H = {"Authorization": "Bearer " + key}
    flow = call("GET", "/flows/instances/bithaven-enrollment/", ADM).json()["pk"]
    r = call("POST", "/stages/invitation/invitations/", H, json={"name": f"{name}-inv", "flow": flow, "single_use": True, "fixed_data": {"email": f"{name}@x.lan"}})
    check("member admin can create an invitation", r.status_code == 201, str(r.status_code))
    inv = r.json().get("pk") if r.status_code == 201 else None
    check("member admin can list invitations", call("GET", "/stages/invitation/invitations/", H).status_code == 200)
    if inv:
        r = call("POST", f"/stages/invitation/invitations/{inv}/send_email/", H, json={"email_addresses": [f"{name}@{env['INTERNAL_DOMAIN']}"]})
        check("member admin can email an invitation", r.status_code in (200, 204), str(r.status_code))
    me = call("GET", "/core/users/me/", H).json()["user"]
    check("member admin is a superuser", me.get("is_superuser") is True)
    check("member admin can read flows", call("GET", "/flows/instances/", H).status_code == 200)
    check("member admin can list users", call("GET", "/core/users/", H).status_code == 200)
    if inv: call("DELETE", f"/stages/invitation/invitations/{inv}/", ADM)
finally:
    call("DELETE", f"/core/tokens/{name}-tok/", ADM); call("DELETE", f"/core/users/{u['pk']}/", ADM)
failed = [n for n, ok, _ in results if not ok]
out = f"results/role-{time.strftime('%Y%m%d-%H%M%S')}.md"
os.makedirs("results", exist_ok=True)
with open(out, "w") as fh:
    fh.write(f"# Member-admin group test {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n| Result | Check | Detail |\n|---|---|---|\n")
    for n, ok, d in results: fh.write(f"| {'PASS' if ok else 'FAIL'} | {n} | {d} |\n")
print(f"\n{len(results)-len(failed)}/{len(results)} passed -> {out}"); sys.exit(1 if failed else 0)
