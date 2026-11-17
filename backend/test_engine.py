"""引擎自测：用一套完整业务验证余额、报表、结转。

直接构造已记账凭证（绕过审批流，专测取数逻辑）。
预期结果在文末断言，任何一条不符即报错。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app import calc  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Voucher, VoucherLine  # noqa: E402
from app.seed import BALANCE_SHEET, PROFIT, seed  # noqa: E402
from app.util import from_cents  # noqa: E402


def c(yuan) -> int:
    return int(round(yuan * 100))


def reset():
    import app.db as dbmod

    for p in (dbmod.DB_PATH, Path(str(dbmod.DB_PATH) + "-wal"), Path(str(dbmod.DB_PATH) + "-shm")):
        if p.exists():
            p.unlink()
    Base.metadata.create_all(engine)


def make_voucher(db, accs, dt, lines, seq, note="", status="posted"):
    v = Voucher(period=dt[:7], vdate=date.fromisoformat(dt), word="记", seq=seq,
                note=note, status=status, maker="test", source="manual")
    db.add(v)
    db.flush()
    for i, (code, dr, cr, summary) in enumerate(lines, 1):
        v.lines.append(VoucherLine(line_no=i, summary=summary, account_id=accs[code].id,
                                   debit=c(dr) if dr else 0, credit=c(cr) if cr else 0))
    db.flush()
    return v


def main():
    reset()
    db = SessionLocal()
    seed(db)
    accs = {a.code: a for a in db.scalars(select(Account)).all()}
    P = "2026-11"

    # 1 股东投入
    make_voucher(db, accs, "2026-11-01", [
        ("1002.01", 1_000_000, 0, "收到股东投资款"),
        ("3001", 0, 1_000_000, "收到股东投资款"),
    ], 1, "股东投入")

    # 2 采购电子料（13% 进项）
    make_voucher(db, accs, "2026-11-03", [
        ("1403.01", 200_000, 0, "采购芯片"),
        ("2221.01.01", 26_000, 0, "进项税额"),
        ("2202", 0, 226_000, "应付芯片款"),
    ], 2, "采购")

    # 3 采购结构件
    make_voucher(db, accs, "2026-11-03", [
        ("1403.02", 100_000, 0, "采购结构件"),
        ("2221.01.01", 13_000, 0, "进项税额"),
        ("2202", 0, 113_000, "应付结构件款"),
    ], 3, "采购")

    # 4 购建设备
    make_voucher(db, accs, "2026-11-05", [
        ("1601.02", 500_000, 0, "购入贴片机"),
        ("2221.01.01", 65_000, 0, "进项税额"),
        ("1002.01", 0, 565_000, "银行付款"),
    ], 4, "固定资产")

    # 5 生产领料
    make_voucher(db, accs, "2026-11-10", [
        ("4001.01", 260_000, 0, "生产领料"),
        ("1403.01", 0, 180_000, "领用电子料"),
        ("1403.02", 0, 80_000, "领用结构件"),
    ], 5, "生产领料")

    # 6 计提折旧 → 制造费用
    make_voucher(db, accs, "2026-11-30", [
        ("4101.01", 20_000, 0, "计提设备折旧"),
        ("1602.02", 0, 20_000, "计提设备折旧"),
    ], 6, "折旧")

    # 7 制造费用结转生产成本
    make_voucher(db, accs, "2026-11-30", [
        ("4001.01", 20_000, 0, "结转制造费用"),
        ("4101.01", 0, 20_000, "结转制造费用"),
    ], 7, "结转账")

    # 8 完工入库
    make_voucher(db, accs, "2026-11-30", [
        ("1405.01", 280_000, 0, "完工入库"),
        ("4001.01", 0, 280_000, "完工入库"),
    ], 8, "完工")

    # 9 销售
    make_voucher(db, accs, "2026-11-20", [
        ("1122", 452_000, 0, "销售手机 1 批"),
        ("5001.01", 0, 400_000, "销售收入"),
        ("2221.01.02", 0, 52_000, "销项税额"),
    ], 9, "销售")

    # 10 结转销售成本
    make_voucher(db, accs, "2026-11-20", [
        ("5401", 250_000, 0, "结转销售成本"),
        ("1405.01", 0, 250_000, "结转销售成本"),
    ], 10, "成本")

    # 11 计提管理费用
    make_voucher(db, accs, "2026-11-30", [
        ("5602.01", 30_000, 0, "计提管理人员工资"),
        ("2211.01", 0, 30_000, "计提管理人员工资"),
    ], 11, "管理费")
    db.commit()

    # ---- 科目余额表
    tb = calc.trial_balance(db, P)
    print("=== 科目余额表（有余额的一级科目）===")
    for r in tb["rows"]:
        if r["level"] == 1 and (r["close_dr"] or r["close_cr"]):
            print(f"  {r['code']:<8}{r['name']:<12}{from_cents(r['close_dr']):>14,.2f}{from_cents(r['close_cr']):>14,.2f}")

    # ---- 资产负债表
    bs = calc.balance_sheet(db, P, BALANCE_SHEET)
    print("\n=== 资产负债表 ===")
    for side in ("left", "right"):
        for r in bs[side]:
            if r["value"] is not None:
                print(f"  {r['label']:<20}{from_cents(r['value']):>16,.2f}{from_cents(r['value_begin']):>16,.2f}")
    print(f"  资产总计 {from_cents(bs['total_assets']):,.2f} | "
          f"负债和所有者权益总计 {from_cents(bs['total_liab_equity']):,.2f} | 差额 {from_cents(bs['diff']):,.2f}")

    # ---- 利润表
    pl = calc.profit_statement(db, P, PROFIT)
    print("\n=== 利润表 ===")
    for r in pl["rows"]:
        if r["value"] is not None:
            print(f"  {r['label']:<18}{from_cents(r['value']):>16,.2f}{from_cents(r['value_ytd']):>16,.2f}")

    # ---- 结转损益
    lines, total = calc.build_carryover(db, P)
    print(f"\n=== 结转损益：{len(lines)} 行，本期利润 {from_cents(total):,.2f} ===")
    for l in lines:
        nm = next((a.name for a in accs.values() if a.id == l["account_id"]), "?")
        print(f"  {nm:<16} 借 {from_cents(l['debit']):>12,.2f}  贷 {from_cents(l['credit']):>12,.2f}")

    # ---- 断言
    checks = [
        ("资产总计 = 1,437,000", bs["total_assets"] == c(1_437_000)),
        ("报表平衡", bs["balanced"]),
        ("营业收入 400,000", pl["rows"][0]["value"] == c(400_000)),
        ("营业利润 120,000",
         next(r["value"] for r in pl["rows"] if r["label"].startswith("二、营业利润")) == c(120_000)),
        ("净利润 120,000", pl["net_profit"] == c(120_000)),
        ("应交税费为负（留抵 52,000）",
         [r for r in bs["right"] if r["key"] == "B6"][0]["value"] == c(-52_000)),
        ("结转后凭证借贷相等", sum(l["debit"] for l in lines) == sum(l["credit"] for l in lines)),
    ]
    print("\n=== 断言 ===")
    ok = True
    for name, cond in checks:
        print(f"  {'✓' if cond else '✗'} {name}")
        ok = ok and cond

    db.close()
    print("\n结果：", "全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
