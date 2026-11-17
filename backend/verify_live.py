"""只读核对线上数据：不写入任何业务，只验证取数是否与预期一致。"""
import json
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.5.189:8070"
TOKEN = None
BAD = []


def call(method, path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def chk(name, got, want, tol=0.01):
    ok = abs(got - want) <= tol
    print(f"  {'✓' if ok else '✗'} {name}: {got:,.2f}（期望 {want:,.2f}）")
    if not ok:
        BAD.append(name)


TOKEN = call("POST", "/api/login") if False else None
req = urllib.request.Request(
    BASE + "/api/login",
    data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=30) as r:
    TOKEN = json.loads(r.read().decode())["token"]
print(f"目标 {BASE}")

print("\n[账龄 2026-12]")
ag = call("GET", "/api/partners/aging", {"period": "2026-12", "ptype": "customer"})
for row in ag["rows"]:
    print(f"    {row['name']}: 余额 {row['balance']:,.2f}，账龄 {row['oldest_days']} 天，逾期 {row['overdue']:,.2f}")
chk("应收余额合计", ag["totals"]["balance"], 378000)

print("\n[固定资产]")
s = call("GET", "/api/assets/summary", {"period": "2026-12"})
chk("原值合计", s["total"]["original"], 204000)
chk("累计折旧", s["total"]["accum"], 2850)
pv = call("GET", "/api/assets/depreciation", {"period": "2026-12"})
chk("12 月已提折旧", pv["posted_total"], 2850)

print("\n[增值税 2026-12]")
vb = call("GET", "/api/vat/book", {"period": "2026-12"})
chk("销项税额", vb["output_tax"], 26000)
chk("进项税额", vb["input_tax"], 52000)
chk("应纳税额", vb["payable"], 0)
chk("期末留抵", vb["closing_credit"], 78000)
chk("账实差异", vb["diff"], 0)
d = call("GET", "/api/vat/declaration", {"period": "2026-12"})
m = {x["no"]: x["value"] for x in d["main"]}
chk("行 17 应抵扣合计", m["17"], 104000)
chk("行 18 实际抵扣", m["18"], 26000)
chk("行 20 期末留抵", m["20"], 78000)

print("\n[报表平衡]")
bs = call("GET", "/api/reports/balance-sheet", {"period": "2026-12"})
chk("资产", bs["total_assets"], bs["total_liab_equity"])
print(f"    平衡={bs['balanced']} 资产={bs['total_assets']:,.2f} 负债+权益={bs['total_liab_equity']:,.2f}")
if not bs["balanced"]:
    BAD.append("资产负债表不平")

print("\n" + ("全部一致 ✓" if not BAD else f"异常：{BAD}"))
sys.exit(0 if not BAD else 1)
