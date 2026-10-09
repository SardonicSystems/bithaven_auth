# Authentik expression policy body: create the enrolling member in FreeIPA, then add them to the members group.
# Runs after email verification (cleanup of a half-made user is in the bh-ipa-failed policy). Never logs, echoes or returns the password; any failure denies with a generic message.
import os
import re
import requests

ctx = request.context
plan = ctx.get("flow_plan")
if plan is not None and plan.context.get("bh_ipa_created") is not None:
    return True   # already ran in this flow; never create twice

data = ctx.get("prompt_data") or {}
pending = (plan.context.get("pending_user") if plan is not None else None) or ctx.get("pending_user")
uid = (data.get("username") or "").strip()
mail = (data.get("email") or "").strip()
full = (data.get("name") or uid).strip()
pw = data.get("password") or ""


def deny():
    # Record the failure; the Deny stage and the bh-ipa-failed policy handle cleanup after this stage's own save.
    if plan is not None:
        plan.context["bh_ipa_created"] = False
    return True   # a failing binding policy would only skip this stage, so always let the flow reach the Deny stage


if not (re.fullmatch(r"[a-z][a-z0-9_.-]{2,31}", uid) and "@" in mail and pw):
    return deny()

try:
    host = os.environ["IPA_HOST"]
    base = "https://%s/ipa" % host
    ca = "/certs/ipa-ca.crt"
    members = os.environ["IPA_GROUP_MEMBERS"]
    s = requests.Session()
    s.verify = ca
    login = s.post(
        base + "/session/login_password",
        data={"user": os.environ["IPA_PROVISION_UID"], "password": os.environ["IPA_PROVISION_PASSWORD"]},
        headers={"Referer": base, "Accept": "text/plain"},
        timeout=15,
    )
    if login.status_code != 200:
        return deny()

    def rpc(method, args, opts):
        opts = dict(opts, version="2.251")
        r = s.post(
            base + "/session/json",
            json={"method": method, "params": [args, opts], "id": 0},
            headers={"Referer": base, "Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        return None if j.get("error") is not None else (j.get("result") or {})

    parts = full.split()
    first = parts[0] if parts else uid
    last = " ".join(parts[1:]) if len(parts) > 1 else "-"
    created = rpc("user_add", [uid], {"givenname": first, "sn": last, "cn": full, "mail": mail, "userpassword": pw})
    if created is None:
        return deny()
    if rpc("group_add_member", [members], {"user": [uid]}) is None:
        return deny()
    uniq = ((created.get("result") or {}).get("ipauniqueid") or [""])[0]
    if not uniq or pending is None:
        return deny()

    # Link the Authentik user to the LDAP source now: the sync refuses to create a user that already exists,
    # so without this the member would never be synced or get group membership from IPA.
    from authentik.core.models import Group
    from authentik.sources.ldap.models import LDAPSource, UserLDAPSourceConnection

    src = LDAPSource.objects.get(slug="bithaven-ipa")   # slug defined in blueprints/10-ipa-source.yaml
    UserLDAPSourceConnection.objects.update_or_create(source=src, user=pending, defaults={"identifier": uniq})
    group = Group.objects.filter(name=members).first()
    if group is not None:
        group.users.add(pending)
except Exception:
    return deny()

if plan is not None:
    plan.context["bh_ipa_created"] = True
return True
