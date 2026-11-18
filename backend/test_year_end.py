"""12月年度结转 3103 -> 3104 回归测试。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-year-end-")

from sqlalchemy import select  # noqa: E402

from app import calc  # noqa: E402
from app.api import (  # noqa: E402
    IncomeTaxConfirmIn,
    _current_open_period,
    audit_voucher,
    close_period,
    confirm_income_tax,
    create_income_tax_voucher,
    period_closing_summary,
    reopen_period,
)
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Period, Voucher, VoucherLine  # noqa: E402
from app.seed import seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
user = {"id": 1, "username": "admin", "role": "admin"}
for p in db.scalars(select(Period).where(Period.code < "2026-12")).all():
    p.status = "closed"
db.commit()
assert _current_open_period(db) == "2026-12"
accs = {a.code: a for a in db.scalars(select(Account)).all()}

v = Voucher(period="2026-12", vdate=date(2026, 12, 10), word="记", seq=1,
            note="年度结转测试收入", maker="test", auditor="test", status="audited", source="manual")
db.add(v)
db.flush()
db.add_all([
    VoucherLine(voucher_id=v.id, line_no=1, summary="年度结转测试收入", account_id=accs["1122"].id,
                debit=10_000_00, credit=0),
    VoucherLine(voucher_id=v.id, line_no=2, summary="年度结转测试收入", account_id=accs["5001.01"].id,
                debit=0, credit=10_000_00),
])
db.commit()

summary = period_closing_summary("2026-12", db, user)
assert any(c["key"] == "year_close" for c in summary["cards"])

body = IncomeTaxConfirmIn(period="2026-12", prior_loss=0, employee_count=1,
                          total_assets=10_000_00, industry_ok=True, note="年度测试")
confirm_income_tax("2026-12", body, db, user)
tax_voucher = create_income_tax_voucher("2026-12", db, user)
audit_voucher(tax_voucher["voucher_id"], db, user)

result = close_period("2026-12", db, user)
assert result["year_close_no"], result
year_voucher = db.scalar(select(Voucher).where(Voucher.period == "2026-12", Voucher.source == "year_close"))
assert year_voucher is not None and year_voucher.status == "audited", [(v.id, v.source, v.status) for v in db.scalars(select(Voucher).where(Voucher.period == "2026-12")).all()]

book = calc.Book(db, "2026-12")
assert book.sum_codes("end", ["3103"]) == 0
assert book.sum_codes("end", ["3104"]) == 9_500_00

reopen_period("2026-12", db, user)
db.refresh(year_voucher)
assert year_voucher.status == "void"
book = calc.Book(db, "2026-12")
assert book.sum_codes("end", ["3103"]) == 9_500_00

db.close()
print("年度结转测试全部通过")