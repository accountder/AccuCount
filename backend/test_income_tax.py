"""季度企业所得税预提回归测试。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-income-tax-")

from sqlalchemy import select  # noqa: E402

from app.api import (  # noqa: E402
    IncomeTaxConfirmIn,
    _current_open_period,
    audit_voucher,
    confirm_income_tax,
    create_income_tax_voucher,
    delete_voucher,
    income_tax_preview,
)
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, IncomeTaxCalc, Period, Voucher, VoucherLine  # noqa: E402
from app.seed import seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
user = {"id": 1, "username": "admin", "role": "admin"}

for code in ("2026-01", "2026-02"):
    p = db.scalar(select(Period).where(Period.code == code))
    p.status = "closed"
db.commit()
assert _current_open_period(db) == "2026-03"
accs = {a.code: a for a in db.scalars(select(Account)).all()}


def add_sales(period, amount):
    seq = (db.scalar(select(Voucher.id).where(Voucher.period == period).order_by(Voucher.id.desc()).limit(1)) or 0)
    v = Voucher(period=period, vdate=date.fromisoformat(period + "-10"), word="记", seq=seq + 1,
                note="测试收入", maker="test", auditor="test", status="audited", source="manual")
    db.add(v)
    db.flush()
    db.add_all([
        VoucherLine(voucher_id=v.id, line_no=1, summary="测试收入", account_id=accs["1122"].id,
                    debit=amount, credit=0),
        VoucherLine(voucher_id=v.id, line_no=2, summary="测试收入", account_id=accs["5001.01"].id,
                    debit=0, credit=amount),
    ])
    db.commit()
    return v


add_sales("2026-03", 1_000_000_00)
missing_people = income_tax_preview("2026-03", 0, 0, 10_000_000, True, db, user)
assert missing_people["small_micro"] is False
assert missing_people["effective_rate"] == 2500

preview = income_tax_preview("2026-03", 0, 50, 10_000_000, True, db, user)
assert preview["applicable"] is True
assert preview["ytd_profit"] == 1_000_000.0
assert preview["taxable_income"] == 1_000_000.0
assert preview["small_micro"] is True
assert preview["effective_rate"] == 500
assert preview["cumulative_tax"] == 50_000.0
assert preview["current_accrual"] == 50_000.0

body = IncomeTaxConfirmIn(
    period="2026-03", prior_loss=0, employee_count=50,
    total_assets=10_000_000, industry_ok=True, note="季度预提测试",
)
confirmed = confirm_income_tax("2026-03", body, db, user)
assert confirmed["saved_confirmed"] is True
row = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == "2026-03"))
assert row is not None and row.small_micro and row.current_accrual == 5_000_000

created = create_income_tax_voucher("2026-03", db, user)
voucher = db.get(Voucher, created["voucher_id"])
assert voucher.status == "draft" and voucher.source == "income_tax"
assert len(voucher.lines) == 2
assert sum(line.debit for line in voucher.lines) == sum(line.credit for line in voucher.lines) == 5_000_000

# 所得税草稿删除后清理快照，可重新确认和生成
assert delete_voucher(voucher.id, db, user)["ok"] is True
db.expire_all()
row = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == "2026-03"))
assert row is not None and row.voucher_id is None and row.confirmed is False
confirm_income_tax("2026-03", body, db, user)
created = create_income_tax_voucher("2026-03", db, user)
voucher = db.get(Voucher, created["voucher_id"])
audit_voucher(voucher.id, db, user)

# 进入第二季度：1-3 月、4-5 月结账，6 月为当前期间
for code in ("2026-03", "2026-04", "2026-05"):
    p = db.scalar(select(Period).where(Period.code == code))
    p.status = "closed"
db.commit()
assert _current_open_period(db) == "2026-06"
add_sales("2026-06", 3_000_000_00)
q2 = income_tax_preview("2026-06", 0, 50, 10_000_000, True, db, user)
assert q2["ytd_profit"] == 4_000_000.0
assert q2["small_micro"] is False
assert q2["effective_rate"] == 2500
assert q2["cumulative_tax"] == 1_000_000.0
assert q2["previous_accrued"] == 50_000.0
assert q2["current_accrual"] == 950_000.0

db.close()
print("季度企业所得税测试全部通过")