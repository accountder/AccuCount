"""审核即入账、以及一键结账自动结转的回归测试。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-audit-close-")

from sqlalchemy import select  # noqa: E402

from app import calc  # noqa: E402
from app.api import close_period  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Period, Voucher, VoucherLine  # noqa: E402
from app.seed import BALANCE_SHEET, PROFIT, seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
accounts = {a.code: a for a in db.scalars(select(Account)).all()}
period = "2026-01"


def make_voucher(seq, debit_code, credit_code, amount, status="audited"):
    v = Voucher(
        period=period,
        vdate=date(2026, 1, min(seq, 28)),
        word="记",
        seq=seq,
        note="测试凭证",
        maker="test",
        auditor="test" if status == "audited" else "",
        status=status,
        source="manual",
    )
    db.add(v)
    db.flush()
    db.add_all([
        VoucherLine(voucher_id=v.id, line_no=1, summary="测试", account_id=accounts[debit_code].id,
                    debit=amount, credit=0),
        VoucherLine(voucher_id=v.id, line_no=2, summary="测试", account_id=accounts[credit_code].id,
                    debit=0, credit=amount),
    ])
    db.flush()
    return v


make_voucher(1, "1002.01", "5001.01", 100000)
make_voucher(2, "5602.01", "1002.01", 30000)
draft = make_voucher(3, "1002.01", "5001.01", 50000, status="draft")
db.commit()

tb = calc.trial_balance(db, period)
rows = {r["code"]: r for r in tb["rows"]}
assert rows["1002.01"]["close_dr"] == 70000, rows["1002.01"]
assert rows["5001.01"]["close_cr"] == 100000, rows["5001.01"]
assert rows["5602.01"]["close_dr"] == 30000, rows["5602.01"]

bs = calc.balance_sheet(db, period, BALANCE_SHEET)
assert not bs["balanced"]
assert bs["diff"] == 70000, bs["diff"]
assert bs["unclosed_profit"] == 70000, bs["unclosed_profit"]
assert bs["diff_matches_unclosed_profit"]

try:
    close_period(period, db, {"username": "admin"})
    raise AssertionError("存在未审核凭证时不应允许结账")
except Exception as exc:
    assert exc.__class__.__name__ == "HTTPException"
    assert "未审核凭证" in str(exc)

draft.status = "audited"
draft.auditor = "test"
db.commit()

bs_before_close = calc.balance_sheet(db, period, BALANCE_SHEET)
assert not bs_before_close["balanced"]
assert bs_before_close["diff"] == 120000, bs_before_close["diff"]
assert bs_before_close["unclosed_profit"] == 120000, bs_before_close["unclosed_profit"]
assert bs_before_close["diff_matches_unclosed_profit"]
pl_before_close = calc.profit_statement(db, period, PROFIT)
assert pl_before_close["net_profit"] == 120000, pl_before_close["net_profit"]

result = close_period(period, db, {"username": "admin"})
assert result["ok"] is True
assert result["carryover_no"]
p = db.scalar(select(Period).where(Period.code == period))
assert p.status == "closed"
carry = db.scalar(select(Voucher).where(Voucher.period == period, Voucher.source == "carryover"))
assert carry is not None
assert carry.status == "audited"

bs_after_close = calc.balance_sheet(db, period, BALANCE_SHEET)
assert bs_after_close["balanced"], bs_after_close["diff"]
assert bs_after_close["unclosed_profit"] == 0
pl_after_close = calc.profit_statement(db, period, PROFIT)
assert pl_after_close["net_profit"] == 120000, pl_after_close["net_profit"]

db.close()
print("审核即入账与一键结账测试全部通过")
