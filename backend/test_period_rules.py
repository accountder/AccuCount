"""凭证期间规则自测：已结账期间锁定、复制凭证、期间落点。

前置：服务已启动，账套里 2026-11 已结账、2026-12 未结账。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"
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


def step(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}{'  — ' + str(detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


TOKEN = call("POST", "/api/login", {"username": "admin", "password": "admin123"})[1]["token"]
print(f"目标 {BASE}")

# ---- 1 期间元数据
_, m = call("GET", "/api/meta")
step("meta 返回 open_period", m.get("open_period") == "2026-12", m.get("open_period"))
step("meta 返回 last_closed_period", m.get("last_closed_period") == "2026-11", m.get("last_closed_period"))

# ---- 2 11 月凭证不能改 / 不能删 / 不能新增
_, novs = call("GET", "/api/vouchers", params={"period": "2026-11", "size": 100})
nov_id = novs["rows"][0]["id"]
nov_no = novs["rows"][0]["no"]
step("11 月有凭证", novs["total"] > 0, f"{novs['total']} 张")

code, _ = call("POST", "/api/vouchers", {
    "date": "2026-11-15", "word": "记",
    "lines": [
        {"summary": "测试", "account_id": 1, "debit": 100, "credit": 0},
        {"summary": "测试", "account_id": 2, "debit": 0, "credit": 100},
    ],
})
step("往已结账期间新增被拦", code == 400, code)

code, d = call("DELETE", f"/api/vouchers/{nov_id}")
step("已结账期间删凭证被拦", code == 400, d.get("detail", "")[:40] if isinstance(d, dict) else d)

# 修改：11 月凭证是 posted，先看是不是被状态拦
_, one = call("GET", f"/api/vouchers/{nov_id}")
step("11 月凭证状态不是草稿", one["status"] != "draft", one["status"])

# ---- 3 复制
code, cp = call("POST", f"/api/vouchers/{nov_id}/copy", {"date": "2026-12-01"})
step("复制已结账凭证成功", code == 200, f"{cp.get('no')} {cp.get('period')}" if isinstance(cp, dict) else cp)
if code == 200:
    step("复制件落在 2026-12", cp["period"] == "2026-12", cp["period"])
    step("复制件是未审核", cp["status"] == "draft", cp["status"])
    step("借贷金额一致",
         abs(cp["debit_total"] - one["debit_total"]) < 0.01 and abs(cp["credit_total"] - one["credit_total"]) < 0.01,
         f"{cp['debit_total']} vs {one['debit_total']}")
    step("分录行数一致", len(cp["lines"]) == len(one["lines"]), f"{len(cp['lines'])} vs {len(one['lines'])}")
    step("往来单位与税率带过来", all(
        a["aux_name"] == b["aux_name"] and a["tax_rate"] == b["tax_rate"]
        for a, b in zip(cp["lines"], one["lines"])
    ))
    # 清理：删掉复制件（草稿可删）
    code, _ = call("DELETE", f"/api/vouchers/{cp['id']}")
    step("复制件可删除（草稿）", code == 200, code)

# ---- 4 复制到已结账期间应被拦
code, _ = call("POST", f"/api/vouchers/{nov_id}/copy", {"date": "2026-11-20"})
step("复制到已结账期间被拦", code == 400, code)

# ---- 5 12 月新增正常
_, accs = call("GET", "/api/accounts")
leaf = [a for a in accs if a["is_leaf"]][:2]
code, nv = call("POST", "/api/vouchers", {
    "date": "2026-12-28", "word": "记",
    "lines": [
        {"summary": "期间规则测试", "account_id": leaf[0]["id"], "debit": 1, "credit": 0},
        {"summary": "期间规则测试", "account_id": leaf[1]["id"], "debit": 0, "credit": 1},
    ],
})
step("12 月新增成功", code == 200, nv.get("no") if isinstance(nv, dict) else nv)
if code == 200:
    call("DELETE", f"/api/vouchers/{nv['id']}")

print("\n" + ("全部通过 ✓" if not FAIL else f"失败：{FAIL}"))
sys.exit(0 if not FAIL else 1)
