"""凭证交互自测：插入凭证（凭证号顺移）、批量审核、批量删除、列表带分录。

跑在独立库上，不影响演示数据。
用法：python test_voucher_ops.py [base_url]
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8092"
TOKEN = None
FAIL = []


def call(method, path, body=None, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def chk(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}{'  — ' + str(detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


TOKEN = call("POST", "/api/login", {"username": "admin", "password": "admin123"})[1]["token"]
print(f"目标 {BASE}")
accs = {a["code"]: a["id"] for a in call("GET", "/api/accounts")[1]}
P = "2026-05"


def mk(date, amount, summary):
    return call("POST", "/api/vouchers", {
        "date": date, "word": "记",
        "lines": [
            {"summary": summary, "account_id": accs["1002.01"], "debit": amount, "credit": 0},
            {"summary": summary, "account_id": accs["3001"], "debit": 0, "credit": amount},
        ],
    })


print("\n[准备基础凭证]")
ids = []
for i, d in enumerate(["2026-05-01", "2026-05-02", "2026-05-03"]):
    code, v = mk(d, 1000 * (i + 1), f"第{i + 1}张")
    ids.append(v["id"])
    print("   ", v["no"], v["summary"])
chk("三张凭证编号连续", [x for x in ["记-0001", "记-0002", "记-0003"]] ==
    [v["no"] for v in [call("GET", f"/api/vouchers/{i}")[1] for i in ids]], "")

print("\n[插入凭证到第 1 张之后]")
code, nv = call("POST", "/api/vouchers", {
    "date": "2026-05-01", "word": "记",
    "lines": [
        {"summary": "插入的凭证", "account_id": accs["1001"], "debit": 66, "credit": 0},
        {"summary": "插入的凭证", "account_id": accs["3001"], "debit": 0, "credit": 66},
    ],
}, params={"insert_after": ids[0]})
chk("插入成功", code == 200, nv.get("no") if isinstance(nv, dict) else nv)
chk("插入后编号为 记-0002", isinstance(nv, dict) and nv["no"] == "记-0002", nv.get("no") if isinstance(nv, dict) else "")

_, lst = call("GET", "/api/vouchers", params={"period": P, "size": 50})
nos = sorted([r["no"] for r in lst["rows"]])
chk("编号顺移后仍连续", nos == ["记-0001", "记-0002", "记-0003", "记-0004"], nos)
order = [r["summary"] for r in sorted(lst["rows"], key=lambda x: x["seq"])]
chk("插入位置正确", order[1] == "插入的凭证", order)

print("\n[列表带分录]")
_, wl = call("GET", "/api/vouchers", params={"period": P, "size": 50, "with_lines": True})
chk("with_lines 每张都有分录", all(len(r.get("lines") or []) >= 2 for r in wl["rows"]), len(wl["rows"]))
chk("分录含科目名称", wl["rows"][0]["lines"][0]["account_name"] != "", wl["rows"][0]["lines"][0]["account_name"])

print("\n[批量审核]")
code, r = call("POST", "/api/vouchers/batch", {"ids": ids[:2] + [ids[2]], "action": "audit"})
chk("批量审核返回完成数", code == 200 and r["done"] == 3, r)
_, after = call("GET", "/api/vouchers", params={"period": P, "status": "audited"})
chk("三张已变为已审核", after["total"] == 3, after["total"])

print("\n[批量删除：已审核的应被跳过]")
_, all_rows = call("GET", "/api/vouchers", params={"period": P, "size": 50})
code, r2 = call("POST", "/api/vouchers/batch", {"ids": [x["id"] for x in all_rows["rows"]], "action": "delete"})
chk("只删掉草稿", code == 200 and r2["done"] == 1, f"done={r2['done']} skipped={len(r2['skipped'])}")
chk("跳过的有原因说明", all(s["reason"] for s in r2["skipped"]), r2["skipped"][:2])

print("\n[整理凭证号]")
rows = call("GET", "/api/vouchers", params={"period": P, "size": 50})[1]["rows"]
rows.sort(key=lambda x: x["seq"])
mid = rows[1]
call("POST", f"/api/vouchers/{mid['id']}/unaudit")
call("DELETE", f"/api/vouchers/{mid['id']}")
left = call("GET", "/api/vouchers", params={"period": P, "size": 50})[1]["rows"]
nos_before = sorted(r["no"] for r in left)
print("    整理前:", nos_before)
chk("删除后出现断号", nos_before != [f"记-{i:04d}" for i in range(1, len(nos_before) + 1)], nos_before)

code, rr = call("POST", "/api/vouchers/renumber", params={"period": P, "word": "记"})
chk("整理接口返回", code == 200, rr)
after = call("GET", "/api/vouchers", params={"period": P, "size": 50})[1]["rows"]
nos_after = sorted(r["no"] for r in after)
chk("整理后编号连续", nos_after == [f"记-{i:04d}" for i in range(1, len(nos_after) + 1)], nos_after)

code, again = call("POST", "/api/vouchers/renumber", params={"period": P, "word": "记"})
chk("已连续时不再改动", code == 200 and again.get("unchanged") is True, again)

print("\n[批量复制到另一期间]")
ids = [r["id"] for r in after]
TP = "2026-06"
code, cp = call("POST", "/api/vouchers/batch", {"ids": ids, "action": "copy", "target_period": TP})
chk("复制成功", code == 200 and cp["done"] == len(ids), cp)
d2 = call("GET", "/api/vouchers", params={"period": TP, "size": 50})[1]
chk("目标期间张数一致", d2["total"] == len(ids), d2["total"])
chk("复制件均为未审核", all(x["status"] == "draft" for x in d2["rows"]), "")
chk("复制件编号连续", sorted(x["no"] for x in d2["rows"]) ==
    [f"记-{i:04d}" for i in range(1, len(ids) + 1)], sorted(x["no"] for x in d2["rows"]))

code, bad = call("POST", "/api/vouchers/batch", {"ids": ids, "action": "copy", "target_period": "2099-01"})
chk("复制到不存在的期间被拦", code == 400, bad)

code, bad2 = call("POST", "/api/vouchers/batch", {"ids": ids, "action": "copy"})
chk("没指定期间被拦", code == 400, bad2)

print("\n[清理]")
_, left = call("GET", "/api/vouchers", params={"period": P, "size": 50})
for x in left["rows"]:
    call("POST", f"/api/vouchers/{x['id']}/unaudit")
    call("DELETE", f"/api/vouchers/{x['id']}")
d3 = call("GET", "/api/vouchers", params={"period": TP, "size": 50})[1]
for x in d3["rows"]:
    call("DELETE", f"/api/vouchers/{x['id']}")
_, left2 = call("GET", "/api/vouchers", params={"period": P})
_, left3 = call("GET", "/api/vouchers", params={"period": TP})
chk("清理完毕", left2["total"] == 0 and left3["total"] == 0, f"{left2['total']}/{left3['total']}")

print("\n" + ("全部通过 ✓" if not FAIL else f"失败：{FAIL}"))
sys.exit(0 if not FAIL else 1)
