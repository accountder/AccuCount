"""第二阶段引擎自测：往来账龄、固定资产折旧、增值税台账。

构造两个月的完整业务，断言每个模块的口径。任何一条不符即报错。
金额单位：元。

业务脉络：
  2026-11  股东投入 → 采购原料（进项）→ 赊销 B 客户 → 收一半货款 → 买设备（含进项）
  2026-12  付清供应商 → 赊销 C 客户 → 大额采购形成留抵 → 计提折旧
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app import asset as asset_engine  # noqa: E402
from app import partner as partner_engine  # noqa: E402
from app import vat as vat_engine  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.migrate import ensure_defaults  # noqa: E402
from app.models import Account, Asset, Partner, Voucher, VoucherLine  # noqa: E402
from app.seed import seed  # noqa: E402
from app.util import from_cents  # noqa: E402

FAIL = []


def c(yuan) -> int:
    return int(round(yuan * 100))


def check(name, got, want, tol=0):
    ok = abs(got - want) <= tol
    print(f"  {'✓' if ok else '✗'} {name}: {got} (期望 {want})")
    if not ok:
        FAIL.append(f"{name}: got {got}, want {want}")


def reset():
    import app.db as dbmod

    for p in (dbmod.DB_PATH, Path(str(dbmod.DB_PATH) + "-wal"), Path(str(dbmod.DB_PATH) + "-shm")):
        if p.exists():
            p.unlink()
    Base.metadata.create_all(engine)


_seq = [0]


def mv(db, accs, dt, lines, note="", tax=None):
    """lines: [(code, dr, cr, summary)]，tax: {行序号: 税率万分比}"""
    _seq[0] += 1
    v = Voucher(period=dt[:7], vdate=date.fromisoformat(dt), word="记", seq=_seq[0],
                note=note, status="posted", maker="test", source="manual")
    db.add(v)
    db.flush()
    for i, (code, dr, cr, summary) in enumerate(lines, 1):
        v.lines.append(VoucherLine(
            line_no=i, summary=summary, account_id=accs[code].id,
            debit=c(dr) if dr else 0, credit=c(cr) if cr else 0,
            tax_rate=(tax or {}).get(i, 0),
            aux_type=(summary.split("|")[1] if "|" in summary else ""),
            aux_name=(summary.split("|")[2] if summary.count("|") >= 2 else ""),
        ))
    db.flush()
    return v


def main():
    reset()
    db = SessionLocal()
    seed(db)
    ensure_defaults(db)
    accs = {a.code: a for a in db.scalars(select(Account)).all()}

    # 往来档案
    db.add(Partner(ptype="customer", code="C001", name="B 客户", settle_days=30, tax_rate=1300))
    db.add(Partner(ptype="customer", code="C002", name="C 客户", settle_days=60, tax_rate=1300))
    db.add(Partner(ptype="supplier", code="S001", name="A 供应商", settle_days=45))
    db.add(Partner(ptype="supplier", code="S002", name="D 供应商", settle_days=30))
    db.commit()

    # ---------------- 2026-11
    mv(db, accs, "2026-11-01", [
        ("1002.01", 1_000_000, 0, "收到股东投资款"),
        ("3001", 0, 1_000_000, "收到股东投资款"),
    ], "股东投入")

    # 采购原料 200,000 + 进项 26,000，挂 A 供应商
    mv(db, accs, "2026-11-03", [
        ("1403.01", 200_000, 0, "采购电子料|customer|"),
        ("2221.01.01", 26_000, 0, "采购进项税额"),
        ("2202", 0, 226_000, "采购电子料|supplier|A 供应商"),
    ], "采购原料", tax={2: 1300})

    # 赊销 B 客户 400,000 + 销项 52,000
    mv(db, accs, "2026-11-05", [
        ("1122", 452_000, 0, "销售商品|customer|B 客户"),
        ("5001.01", 0, 400_000, "销售商品"),
        ("2221.01.02", 0, 52_000, "销项税额"),
    ], "赊销", tax={3: 1300})

    # 买设备 120,000 + 进项 15,600，付现
    mv(db, accs, "2026-11-15", [
        ("1601.04", 120_000, 0, "购入测试设备"),
        ("2221.01.01", 15_600, 0, "设备进项税额"),
        ("1002.01", 0, 135_600, "购入测试设备"),
    ], "购设备", tax={2: 1300})

    # 收 B 客户一半货款
    mv(db, accs, "2026-11-20", [
        ("1002.01", 200_000, 0, "收到货款"),
        ("1122", 0, 200_000, "收到货款|customer|B 客户"),
    ], "收款")

    # ---------------- 2026-12
    # 付清 A 供应商
    mv(db, accs, "2026-12-05", [
        ("2202", 226_000, 0, "支付货款|supplier|A 供应商"),
        ("1002.01", 0, 226_000, "支付货款"),
    ], "付款")

    # 赊销 C 客户 100,000 + 13,000
    mv(db, accs, "2026-12-10", [
        ("1122", 113_000, 0, "销售商品|customer|C 客户"),
        ("5001.01", 0, 100_000, "销售商品"),
        ("2221.01.02", 0, 13_000, "销项税额"),
    ], "赊销", tax={3: 1300})

    # 大额采购形成留抵 300,000 + 39,000
    mv(db, accs, "2026-12-20", [
        ("1403.01", 300_000, 0, "采购电子料"),
        ("2221.01.01", 39_000, 0, "采购进项税额"),
        ("2202", 0, 339_000, "采购电子料|supplier|D 供应商"),
    ], "采购", tax={2: 1300})

    db.commit()

    # ================================================== 1 往来账龄
    print("\n【1】往来台账与账龄")
    led = partner_engine.ledger(db, "2026-11")
    b = next(g for g in led["rows"] if g["name"] == "B 客户")
    check("11月 B客户 余额(元)", b["balance"] / 100, 252_000.0)
    check("11月 B客户 未核销笔数", sum(1 for i in b["items"] if i["remain"] > 0), 1)
    check("11月 A供应商 应付余额(元)",
          next(g for g in partner_engine.ledger(db, "2026-11")["rows"] if g["name"] == "A 供应商")["balance"] / 100,
          226_000.0)

    # 自动核销后
    r = partner_engine.auto_settle(db, "2026-11")
    check("11月自动核销笔数", r["created"], 1)
    check("11月自动核销金额(元)", r["amount"] / 100, 200_000.0)
    led = partner_engine.ledger(db, "2026-11")
    b = next(g for g in led["rows"] if g["name"] == "B 客户")
    check("核销后 B客户 余额(元)", b["balance"] / 100, 252_000.0)
    check("核销后 B客户 未核销(元)", sum(i["remain"] for i in b["items"]) / 100, 252_000.0)

    # 12 月账龄
    partner_engine.auto_settle(db, "2026-12")
    ag = partner_engine.aging(db, "2026-12")
    rowb = next(x for x in ag["rows"] if x["name"] == "B 客户")
    check("12月 B客户 余额(元)", rowb["balance"] / 100, 252_000.0)
    check("12月 B客户 账龄天数", rowb["oldest_days"], 56)
    check("12月 B客户 逾期(元)", rowb["overdue"] / 100, 252_000.0)  # 56 天 > 账期 30
    rowc = next(x for x in ag["rows"] if x["name"] == "C 客户")
    check("12月 C客户 账龄天数", rowc["oldest_days"], 21)
    check("12月 C客户 账龄(1个月内)", rowc["cells"]["1"] / 100, 113_000.0)
    # A 供应商已付清，12 月不应出现在账龄表（余额 0 且无未核销）
    check("12月 A供应商不在账龄表", 0 if not any(x["name"] == "A 供应商" and x["balance"] for x in ag["rows"]) else 1, 0)

    # ================================================== 2 固定资产
    print("\n【2】固定资产折旧")
    ids = asset_engine.resolve_accounts(db, "电子设备")
    a = Asset(code="FA0001", name="测试设备", category="电子设备",
              asset_account_id=ids[0], dep_account_id=ids[1], expense_account_id=ids[2],
              original_value=c(120_000), salvage_rate=500, useful_months=60,
              method="straight", in_service_date=date(2026, 11, 15),
              dep_start_period="2026-12")
    db.add(a)
    db.commit()

    sch = asset_engine.schedule(a)
    check("折旧计划期数", len(sch), 60)
    check("月折旧额(元)", sch[0] / 100, 1_900.0)
    check("60期合计=应计折旧总额(元)", sum(sch) / 100, 114_000.0)
    check("11月不该提", asset_engine.dep_of(a, "2026-11"), 0)
    check("12月应提(元)", asset_engine.dep_of(a, "2026-12") / 100, 1_900.0)

    pv = asset_engine.preview(db, "2026-12")
    check("12月应提合计(元)", pv["total"] / 100, 1_900.0)

    res = asset_engine.post_depreciation(db, "2026-12", "test", lambda d, p, w: 900)
    check("折旧凭证金额(元)", res["total"] / 100, 1_900.0)
    db.refresh(a)
    check("累计折旧(元)", a.accum_dep / 100, 1_900.0)
    check("净值(元)", (a.original_value - a.accum_dep) / 100, 118_100.0)
    check("重复计提被拦", 1 if _raise(lambda: asset_engine.post_depreciation(db, "2026-12", "t", lambda d, p, w: 901)) else 0, 1)

    # 双倍余额递减法总额校验
    a2 = Asset(code="FA0002", name="双倍法设备", category="机器设备",
               asset_account_id=ids[0], dep_account_id=ids[1], expense_account_id=ids[2],
               original_value=c(100_000), salvage_rate=500, useful_months=60, method="ddb",
               in_service_date=date(2026, 1, 1), dep_start_period="2026-02")
    s2 = asset_engine.schedule(a2)
    check("双倍余额法合计(元)", sum(s2) / 100, 95_000.0)
    check("双倍余额法首期>末期", 1 if s2[0] > s2[-1] else 0, 1)

    # ================================================== 3 增值税
    print("\n【3】增值税台账")
    v11 = vat_engine.book(db, "2026-11")
    check("11月销项税额(元)", v11["output_tax"] / 100, 52_000.0)
    check("11月进项税额(元)", v11["input_tax"] / 100, 41_600.0)
    check("11月应纳税额(元)", v11["payable"] / 100, 10_400.0)
    check("11月期末留抵(元)", v11["closing_credit"] / 100, 0.0)
    check("11月销售额(元)", v11["total_sales"] / 100, 400_000.0)
    check("11月账实一致(diff)", v11["diff"], 0)

    v12 = vat_engine.book(db, "2026-12")
    check("12月销项税额(元)", v12["output_tax"] / 100, 13_000.0)
    check("12月进项税额(元)", v12["input_tax"] / 100, 39_000.0)
    check("12月应纳税额(元)", v12["payable"] / 100, 0.0)
    check("12月期末留抵(元)", v12["closing_credit"] / 100, 26_000.0)
    check("12月账实一致(diff)", v12["diff"], 0)

    d = vat_engine.declaration(db, "2026-11")
    main = {x["no"]: x["value"] for x in d["main"]}
    check("申报表 11 行销项税额(元)", main["11"] / 100, 52_000.0)
    check("申报表 19 行应纳税额(元)", main["19"] / 100, 10_400.0)
    check("申报表 34 行应补税额(元)", main["34"] / 100, 10_400.0)
    check("附列一档数", len(d["sheet1"]), 1)
    check("附列一 13% 销售额(元)", d["sheet1"][0]["sales"] / 100, 400_000.0)

    # 附加税费 10400 × (7%+3%+2%) = 1248
    lines, total = vat_engine.surtax_voucher_lines(db, "2026-11")
    check("附加税费合计(元)", total / 100, 1_248.0)
    check("附加税费分录行数", len(lines), 6)

    # 全年表
    t = vat_engine.table(db, 2026)
    check("年度表行数（有发生额的月份）", len(t["rows"]), 2)
    check("年度应纳合计(元)", t["total"]["payable"] / 100, 10_400.0)

    print("\n" + ("全部通过 ✓" if not FAIL else f"失败 {len(FAIL)} 项：\n" + "\n".join(FAIL)))
    db.close()
    return 0 if not FAIL else 1


def _raise(fn):
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    sys.exit(main())
