#!/usr/bin/env python3
"""End-to-end check of the v5 Mini App API against a running dev harness.

    tests/devctl.sh start owner 8080
    tests/devctl.sh start locked 8081        # optional, for the locked-user flow
    python tests/test_api_flow.py 8080 [8081]

Runs as the dev owner (MINIAPP_DEV_USER) and exercises the admin endpoints:
approve / extend / reject / revoke / ban / unban / promote / demote plus
the user-facing access endpoints on a second, locked harness (second argument,
default port+1) if it is up — otherwise that part is skipped.
"""
import json
import sys
import urllib.request
import urllib.error

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
LOCKED_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else PORT + 1
BASE = f"http://localhost:{PORT}"
fails = 0


def call(path, body=None, base=BASE):
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:       # harness not running / refused
        return 0, {"error": str(e)}


def check(name, cond, info=""):
    global fails
    print(("✅" if cond else "❌"), name, ("· " + str(info)) if info else "")
    if not cond:
        fails += 1


s, me = call("/api/me")
check("owner /api/me 200", s == 200 and me["me"]["owner"], me.get("me", {}).get("role"))
check("config has plans", len(me["config"]["plans"]) >= 5)

s, ov = call("/api/admin/overview")
check("overview ok", s == 200 and ov["ok"], f"users={len(ov['users'])} pending={len(ov['pending'])}")
check("overview counts", ov["counts"]["pending"] >= 1, ov["counts"])
check("overview audit (owner)", isinstance(ov.get("audit"), list) and len(ov["audit"]) > 0)
pending_ids = [u["id"] for u in ov["pending"]]
check("pending list has users", pending_ids, pending_ids)

# approve the first pending request with 1 month
uid = pending_ids[0]
s, r = call("/api/admin/users", {"action": "approve", "id": uid, "duration": "1m"})
check("approve 1m", s == 200 and r["user"]["access"]["status"] == "approved", r.get("user", {}).get("access", {}).get("expiry_label") or r.get("error"))
exp1 = r["user"]["access"]["expires"]

s, r = call("/api/admin/users", {"action": "extend", "id": uid, "duration": "1w"})
check("extend +1w", s == 200 and r["user"]["access"]["expires"] > exp1, r.get("user", {}).get("access", {}).get("expiry_label") or r.get("error"))

s, r = call("/api/admin/users", {"action": "approve", "id": uid, "duration": "banana"})
check("bad duration rejected", s == 400, r.get("error"))

s, r = call("/api/admin/user/%d" % uid)
check("user detail", s == 200 and r["user"]["id"] == uid and "history" in r)

s, r = call("/api/admin/users", {"action": "revoke", "id": uid, "reason": "test revoke"})
check("revoke", s == 200 and r["user"]["access"]["status"] == "none", r.get("user", {}).get("access", {}).get("reason") or r.get("error"))

# reject the second pending user (if any)
if len(pending_ids) > 1:
    uid2 = pending_ids[1]
    s, r = call("/api/admin/users", {"action": "reject", "id": uid2, "reason": "not now"})
    check("reject", s == 200 and r["user"]["access"]["status"] == "rejected", r.get("error"))

# ban / unban a fresh id
s, r = call("/api/admin/users", {"action": "ban", "id": 424242, "reason": "spam"})
check("ban", s == 200 and r["user"]["access"]["status"] == "banned", r.get("error"))
s, r = call("/api/admin/users", {"action": "approve", "id": 424242, "duration": "1m"})
check("approve banned → 409", s == 409, r.get("error"))
s, r = call("/api/admin/users", {"action": "unban", "id": 424242})
check("unban", s == 200 and r["user"]["access"]["status"] == "none", r.get("error"))

# promote / demote
s, r = call("/api/admin/users", {"action": "promote", "id": 1003})
check("promote", s == 200 and r["user"]["role"] == "admin", r.get("error"))
s, r = call("/api/admin/users", {"action": "demote", "id": 1003})
check("demote", s == 200 and r["user"]["role"] == "user", r.get("error"))

# owner is immutable
s, r = call("/api/admin/users", {"action": "ban", "id": me["me"]["id"]})
check("owner immutable → 403", s == 403, r.get("error"))

# legacy v4 names still work
s, r = call("/api/admin/users", {"action": "add", "id": 555001})
check("legacy add → approve", s == 200 and r["user"]["access"]["status"] == "approved", r.get("user", {}).get("access", {}).get("plan_label"))
s, r = call("/api/admin/users", {"action": "remove", "id": 555001})
check("legacy remove → revoke", s == 200 and r["user"]["access"]["status"] == "none")

# ── locked user flow on the neighbour harness (DEV_ROLE=locked) ────────
LOCKED = f"http://localhost:{LOCKED_PORT}"
s, r = call("/api/me", base=LOCKED)
if s == 0 or s >= 500 or (s == 200 and r.get("me", {}).get("owner")):
    print("ℹ️  locked harness not running on", LOCKED, "— skipping user flow (tests/devctl.sh start locked", LOCKED_PORT, ")")
else:
    check("locked /api/me → 403 code=locked", s == 403 and r.get("code") == "locked" and r.get("me"), r.get("status"))
    check("locked payload has access", r["me"]["access"]["can_request"] is True)
    s, r = call("/api/access/request", {"note": "hello from test"}, base=LOCKED)
    check("request access", s == 200 and r["sent"] and r["me"]["access"]["status"] == "pending", r.get("why"))
    s, r = call("/api/access/request", {"note": "second note"}, base=LOCKED)
    check("re-request while pending → noted", s == 200 and not r["sent"] and r["why"] in ("noted", "pending"), r.get("why"))
    s, r = call("/api/jobs", base=LOCKED)
    check("jobs still locked", s == 403 and r.get("code") == "locked")
    s, r = call("/api/access/withdraw", {}, base=LOCKED)
    check("withdraw", s == 200 and r["me"]["access"]["status"] == "none")
    s, r = call("/api/access", base=LOCKED)
    check("/api/access works while locked", s == 200 and r["ok"])

print("\n" + ("ALL GOOD ✅" if not fails else f"{fails} check(s) FAILED ❌"))
sys.exit(1 if fails else 0)
