"""通过 HTTP 接口灌一套真实业务，验证整条链路（录入→审核→记账→结转→报表）。"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8090/api"
TOKEN = None


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code} {e.read().decode()}")


def main():
    global TOKEN
    TOKEN = call("POST", "/login", {"username": "admin", "password": "admin123"})["token"]
    print("登录成功")

    accs = {a["code"]: a["id"] for a in call("GET", "/accounts")}

    def v(date, lines, note=""):
        return call("POST", "/vouchers", {"date": date, "word": "记", "note": note,
                                          "lines": [{"summary": s, "account_id": accs[c],
                                                     "debit": d, "credit": cr} for c, d, cr, s in lines]})

    made = []
    made.append(v("2026-11-01", [("1002.01", 1_000_000, 0, "收到股东投资款"),
                                 ("3001", 0, 1_000_000, "收到股东投资款")], "股东投入"))
    made.append(v("2026-11-03", [("1403.01", 200_000, 0, "采购芯片（含税 22.6 万）"),
                                 ("2221.01.01", 26_000, 0, "进项税额"),
                                 ("2202", 0, 226_000, "应付芯片款")], "采购"))
    made.append(v("2026-11-03", [("1403.02", 100_000, 0, "采购结构件"),
                                 ("2221.01.01", 13_000, 0, "进项税额"),
                                 ("2202", 0, 113_000, "应付结构件款")], "采购"))
    made.append(v("2026-11-05", [("1601.02", 500_000, 0, "购入贴片机"),
                                 ("2221.01.01", 65_000, 0, "进项税额"),
                                 ("1002.01", 0, 565_000, "银行付款")], "固定资产"))
    made.append(v("2026-11-10", [("4001.01", 260_000, 0, "生产领料"),
                                 ("1403.01", 0, 180_000, "领用电子料"),
                                 ("1403.02", 0, 80_000, "领用结构件")], "生产领料"))
    made.append(v("2026-11-30", [("4101.01", 20_000, 0, "计提设备折旧"),
                                 ("1602.02", 0, 20_000, "计提设备折旧")], "折旧"))
    made.append(v("2026-11-30", [("4001.01", 20_000, 0, "结转制造费用"),
                                 ("4101.01", 0, 20_000, "结转制造费用")], "制造费用"))
    made.append(v("2026-11-30", [("1405.01", 280_000, 0, "完工入库"),
                                 ("4001.01", 0, 280_000, "完工入库")], "完工"))
    made.append(v("2026-11-20", [("1122", 452_000, 0, "销售手机 1,000 台"),
                                 ("5001.01", 0, 400_000, "销售收入"),
                                 ("2221.01.02", 0, 52_000, "销项税额")], "销售"))
    made.append(v("2026-11-20", [("5401", 250_000, 0, "结转销售成本"),
                                 ("1405.01", 0, 250_000, "结转销售成本")], "成本"))
    made.append(v("2026-11-30", [("5602.01", 30_000, 0, "计提管理人员工资"),
                                 ("2211.01", 0, 30_000, "计提管理人员工资")], "管理费用"))
    print(f"录入 {len(made)} 张凭证")

    for vv in made:
        call("POST", f"/vouchers/{vv['id']}/audit")
        call("POST", f"/vouchers/{vv['id']}/post")
    print("全部审核并记账")

    # 未结转就结账，应当被拦
    try:
        call("POST", "/periods/2026-11/close")
        print("!! 结账未被拦截，异常")
    except SystemExit as e:
        print("结账拦截正常：", str(e).split("->")[-1].strip()[:80])

    co = call("POST", "/periods/2026-11/carryover")
    print(f"结转凭证 {co['no']}，本期利润 {co['profit']:,.2f}")
    call("POST", f"/vouchers/{co['voucher_id']}/audit")
    call("POST", f"/vouchers/{co['voucher_id']}/post")

    chk = call("GET", "/reports/check?period=2026-11")
    print("对账：", json.dumps(chk, ensure_ascii=False))

    bs = call("GET", "/reports/balance-sheet?period=2026-11")
    print(f"资产负债表：资产 {bs['total_assets']:,.2f} | 负债+权益 {bs['total_liab_equity']:,.2f} | 平 {bs['balanced']}")

    pl = call("GET", "/reports/profit?period=2026-11")
    print(f"利润表：收入 {pl['rows'][0]['value']:,.2f} | 净利润 {pl['net_profit']:,.2f}")

    # 结转后损益科目应为 0
    tb = call("GET", "/reports/trial-balance?period=2026-11&only_used=true")
    pl_left = [r for r in tb["rows"] if r["cat"] == "pl" and (r["close_dr"] or r["close_cr"])]
    print("结转后仍有余额的损益科目数：", len(pl_left))

    close = call("POST", "/periods/2026-11/close")
    print("结账：", close)


if __name__ == "__main__":
    main()
