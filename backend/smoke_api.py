"""HTTP 冒烟测试：起服务后跑一遍主要接口，确认序列化与权限没问题。

用法：
    python run.py &            # 另开一个终端
    python smoke_api.py        # 默认 http://127.0.0.1:8090
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"
TOKEN = None
FAIL = []


def call(method, path, body=None, params=None, expect=200):
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


def step(name, method, path, body=None, params=None, check=None, expect=200):
    code, data = call(method, path, body, params)
    ok = code == expect
    detail = ""
    if ok and check:
        try:
            ok = bool(check(data))
        except Exception as e:
            ok, detail = False, f"check 异常 {e}"
    if not ok and not detail:
        detail = str(data)[:200]
    print(f"  {'✓' if ok else '✗'} {name} [{code}] {'— ' + detail if detail else ''}")
    if not ok:
        FAIL.append(name)
    return data


import urllib.parse  # noqa: E402

print(f"目标 {BASE}")
code, data = call("POST", "/api/login", {"username": "admin", "password": "admin123"})
if code != 200:
    print("登录失败", data)
    sys.exit(1)
TOKEN = data["token"]
print(f"  ✓ 登录 {data['user']['username']}")

P = "2026-11"

# 往来
step("往来档案列表", "GET", "/api/partners", check=lambda d: isinstance(d, list))
step("新建客户", "POST", "/api/partners",
     {"ptype": "customer", "name": "冒烟客户", "tax_rate": 1300, "settle_days": 30},
     check=lambda d: d.get("id"))
step("重名校验", "POST", "/api/partners",
     {"ptype": "customer", "name": "冒烟客户"}, expect=400)
step("往来下拉", "GET", "/api/partners/options", check=lambda d: isinstance(d, list))
step("账龄区间", "GET", "/api/aging-bands", check=lambda d: len(d) >= 3)
step("往来台账", "GET", "/api/partners/ledger", params={"period": P}, check=lambda d: "rows" in d)
step("账龄分析", "GET", "/api/partners/aging", params={"period": P, "ptype": "customer"},
     check=lambda d: "bands" in d)
step("自动核销", "POST", "/api/partners/settle", params={"period": P}, check=lambda d: "created" in d)
step("对账单", "GET", "/api/partners/statement",
     params={"name": "冒烟客户", "period_from": P, "period_to": P}, check=lambda d: "lines" in d)

# 固定资产
step("资产元数据", "GET", "/api/assets/meta", check=lambda d: len(d["categories"]) == 6)
step("资产列表", "GET", "/api/assets", check=lambda d: isinstance(d, list))
a = step("建卡", "POST", "/api/assets", {
    "name": "冒烟设备", "category": "电子设备", "original_value": 60000,
    "salvage_rate": 500, "useful_months": 36, "method": "straight",
    "in_service_date": "2026-11-10",
}, check=lambda d: d.get("id"))
step("折旧计划", "GET", f"/api/assets/{a['id']}/schedule", check=lambda d: len(d["rows"]) == 36)
step("折旧预览", "GET", "/api/assets/depreciation", params={"period": "2026-12"},
     check=lambda d: d["total"] > 0)
step("计提折旧", "POST", "/api/assets/depreciation", params={"period": "2026-12"},
     check=lambda d: d.get("voucher_no"))
step("重复计提被拦", "POST", "/api/assets/depreciation", params={"period": "2026-12"}, expect=400)
step("资产汇总", "GET", "/api/assets/summary", params={"period": P},
     check=lambda d: d["total"]["original"] >= 60000)

# 增值税
step("增值税台账", "GET", "/api/vat/book", params={"period": P}, check=lambda d: "payable" in d)
step("保存调整", "PUT", "/api/vat/book",
     {"adjust_output_tax": 0, "adjust_input_tax": 0, "paid_tax": 0, "adjust_note": "冒烟测试"},
     params={"period": P}, check=lambda d: d.get("ok"))
step("申报底稿", "GET", "/api/vat/declaration", params={"period": P},
     check=lambda d: len(d["main"]) == 13)
step("年度表", "GET", "/api/vat/table", params={"year": 2026}, check=lambda d: "rows" in d)
step("申报自检", "GET", "/api/vat/check", params={"period": P}, check=lambda d: "issues" in d)

# 原有功能不回归
step("科目余额表", "GET", "/api/reports/trial-balance", params={"period": P},
     check=lambda d: "rows" in d)
step("资产负债表", "GET", "/api/reports/balance-sheet", params={"period": P},
     check=lambda d: "balanced" in d)
step("利润表", "GET", "/api/reports/profit", params={"period": P}, check=lambda d: "rows" in d)
step("概览", "GET", "/api/stats", params={"period": P}, check=lambda d: "total_assets" in d)

print("\n" + ("全部通过 ✓" if not FAIL else f"失败：{FAIL}"))
sys.exit(0 if not FAIL else 1)
