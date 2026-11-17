"""第二阶段演示数据：往来挂账、固定资产卡片、12 月业务。

幂等——重复执行不会重复建档案或重复建凭证。

做三件事：
1. 建往来单位档案（客户 2 家、供应商 2 家）
2. 给 11 月已有凭证补往来单位与税率（只改辅助核算字段，金额一分不动）
3. 建固定资产卡片，并录入 2026-12 的收款、付款、销售、采购
"""
import json
import urllib.error
import urllib.request

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, Asset, Partner, Voucher, VoucherLine

BASE = "http://127.0.0.1:8090/api"
TOKEN = None

CUSTOMERS = [
    {"ptype": "customer", "code": "C001", "name": "深圳蓝海通讯", "tax_no": "91440300MA5DXXXX1A",
     "contact": "陈立", "phone": "0755-8899 0011", "settle_days": 30, "tax_rate": 1300,
     "address": "深圳市南山区科技园北区", "bank": "招商银行深圳科技园支行", "bank_account": "7559 0188 0011"},
    {"ptype": "customer", "code": "C002", "name": "广州锐讯电子", "tax_no": "91440101MA9YXXXX2B",
     "contact": "李慧", "phone": "020-3388 0022", "settle_days": 45, "tax_rate": 1300,
     "address": "广州市黄埔区科学城", "bank": "工商银行广州开发区支行", "bank_account": "3602 0088 0022"},
]
SUPPLIERS = [
    {"ptype": "supplier", "code": "S001", "name": "东莞立讯精密", "tax_no": "91441900MA4WXXXX3C",
     "contact": "王强", "phone": "0769-2288 0033", "settle_days": 60, "tax_rate": 1300,
     "address": "东莞市清溪镇", "bank": "建设银行东莞分行", "bank_account": "4400 1188 0033"},
    {"ptype": "supplier", "code": "S002", "name": "苏州晶方半导体", "tax_no": "91320594MA1MXXXX4D",
     "contact": "赵敏", "phone": "0512-6688 0044", "settle_days": 45, "tax_rate": 1300,
     "address": "苏州工业园区", "bank": "中国银行苏州工业园区支行", "bank_account": "4800 2288 0044"},
]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code} {e.read().decode()}")


def main():
    global TOKEN
    TOKEN = call("POST", "/login", {"username": "admin", "password": "admin123"})["token"]
    print("登录成功")

    # ---------------- 1 往来档案
    have = {(p["ptype"], p["name"]) for p in call("GET", "/partners")}
    for p in CUSTOMERS + SUPPLIERS:
        if (p["ptype"], p["name"]) in have:
            print(f"  跳过已存在：{p['name']}")
            continue
        call("POST", "/partners", p)
        print(f"  建档：{p['name']}")

    # ---------------- 2 补 11 月的往来与税率（只改辅助字段）
    db = SessionLocal()
    try:
        accs = {a.code: a for a in db.scalars(select(Account)).all()}
        fixed = 0

        # 应收账款 → 客户
        for vl in db.scalars(select(VoucherLine).where(VoucherLine.account_id == accs["1122"].id)).all():
            if vl.aux_name:
                continue
            vl.aux_type = "customer"
            vl.aux_name = "深圳蓝海通讯"
            fixed += 1

        # 应付账款 → 按摘要关键字分供应商
        for vl in db.scalars(select(VoucherLine).where(VoucherLine.account_id == accs["2202"].id)).all():
            if vl.aux_name:
                continue
            if "芯片" in vl.summary:
                vl.aux_name = "东莞立讯精密"
            elif "结构件" in vl.summary:
                vl.aux_name = "苏州晶方半导体"
            else:
                continue
            vl.aux_type = "supplier"
            fixed += 1

        # 涉税行补税率（13%）
        for code in ("2221.01.01", "2221.01.02"):
            a = accs.get(code)
            if not a:
                continue
            for vl in db.scalars(select(VoucherLine).where(VoucherLine.account_id == a.id)).all():
                if not vl.tax_rate:
                    vl.tax_rate = 1300
                    fixed += 1

        db.commit()
        print(f"  补齐 {fixed} 行辅助核算/税率")
    finally:
        db.close()

    # ---------------- 3 固定资产卡片
    db = SessionLocal()
    try:
        exist = {a.code for a in db.scalars(select(Asset)).all()}
    finally:
        db.close()

    cards = [
        {"code": "FA202611001", "name": "注塑机（海天 HTF-250）", "category": "机器设备",
         "dept": "生产部", "location": "一号车间", "original_value": 180_000, "salvage_rate": 500,
         "useful_months": 60, "method": "straight", "in_service_date": "2026-11-25"},
        {"code": "FA202612002", "name": "联想 ThinkPad E14 笔记本（5 台）", "category": "电子设备",
         "dept": "销售部", "location": "办公区", "original_value": 24_000, "salvage_rate": 500,
         "useful_months": 36, "method": "straight", "in_service_date": "2026-12-05"},
    ]
    for c in cards:
        if c["code"] in exist:
            print(f"  跳过已存在资产：{c['name']}")
            continue
        r = call("POST", "/assets", c)
        print(f"  建卡：{r['name']} 月折旧 {r['monthly_dep']:,.2f}，{r['dep_start_period']} 起提")

    # ---------------- 4 2026-12 业务
    db = SessionLocal()
    try:
        has_dec = db.scalar(
            select(Voucher.id).where(Voucher.period == "2026-12", Voucher.status == "posted").limit(1)
        )
    finally:
        db.close()

    if has_dec:
        print("  12 月已有业务，跳过")
    else:
        accs = {a["code"]: a["id"] for a in call("GET", "/accounts")}
        made = []

        def v(date, lines, note=""):
            return call("POST", "/vouchers", {
                "date": date, "word": "记", "note": note,
                "lines": [
                    {"summary": s, "account_id": accs[c], "debit": d, "credit": cr,
                     "aux_type": at, "aux_name": an, "tax_rate": tr}
                    for c, d, cr, s, at, an, tr in lines
                ],
            })

        # 收到蓝海通讯货款（冲 11 月应收）
        made.append(v("2026-12-05", [
            ("1002.01", 300_000, 0, "收到蓝海通讯货款", "", "", 0),
            ("1122", 0, 300_000, "收到蓝海通讯货款", "customer", "深圳蓝海通讯", 0),
        ], "收款"))

        # 支付立讯芯片款
        made.append(v("2026-12-10", [
            ("2202", 226_000, 0, "支付立讯精密货款", "supplier", "东莞立讯精密", 0),
            ("1002.01", 0, 226_000, "支付立讯精密货款", "", "", 0),
        ], "付款"))

        # 销售给锐讯电子
        made.append(v("2026-12-15", [
            ("1122", 226_000, 0, "销售模组 500 套", "customer", "广州锐讯电子", 0),
            ("5001.01", 0, 200_000, "销售模组 500 套", "", "", 0),
            ("2221.01.02", 0, 26_000, "销项税额", "", "", 1300),
        ], "赊销"))

        # 采购结构件（形成留抵）
        made.append(v("2026-12-20", [
            ("1403.02", 400_000, 0, "采购结构件 4,000 套", "", "", 0),
            ("2221.01.01", 52_000, 0, "进项税额", "", "", 1300),
            ("2202", 0, 452_000, "采购结构件 4,000 套", "supplier", "苏州晶方半导体", 0),
        ], "采购"))

        for vv in made:
            call("POST", f"/vouchers/{vv['id']}/audit")
            call("POST", f"/vouchers/{vv['id']}/post")
        print(f"  录入并记账 12 月 {len(made)} 张凭证")

    # ---------------- 5 计提 12 月折旧
    try:
        pv = call("GET", "/assets/depreciation?period=2026-12")
        if pv["total"] > 0 and not any(r["posted"] for r in pv["rows"]):
            r = call("POST", "/assets/depreciation?period=2026-12")
            print(f"  计提 12 月折旧：凭证 {r['voucher_no']}，金额 {r['total']:,.2f}，{r['count']} 项资产")
        else:
            print(f"  12 月折旧已计提或无可提（应提 {pv['total']:,.2f}）")
    except SystemExit as e:
        print("  折旧计提跳过：", str(e)[:120])

    # ---------------- 6 自动核销
    for pt in ("customer", "supplier"):
        r = call("POST", f"/partners/settle?period=2026-12&ptype={pt}")
        print(f"  {pt} 自动核销 {r['created']} 笔 / {r['amount']:,.2f}")

    print("\n完成。")
    ag = call("GET", "/partners/aging?period=2026-12")
    for row in ag["rows"]:
        print(f"  {row['name']}: 余额 {row['balance']:,.2f}，最长账龄 {row['oldest_days']} 天")
    vb = call("GET", "/vat/book?period=2026-12")
    print(f"  12 月增值税：销项 {vb['output_tax']:,.2f} 进项 {vb['input_tax']:,.2f} "
          f"应纳 {vb['payable']:,.2f} 留抵 {vb['closing_credit']:,.2f}")


if __name__ == "__main__":
    main()
