"""利润表红字冲销、退货与结转排除测试。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-report-netting-")

from sqlalchemy import select  # noqa: E402

from app import calc  # noqa: E402
from app.api import close_period, _current_open_period  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, ReportTemplate, Voucher, VoucherLine  # noqa: E402
from app.seed import seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
period = _current_open_period(db)
accs = {a.code: a for a in db.scalars(select(Account)).all()}


def add(seq, lines):
    v = Voucher(period=period, vdate=date.fromisoformat(period + "-15"), word="记", seq=seq,
                note="报表净额测试", maker="test", auditor="test", status="audited", source="manual")
    db.add(v)
    db.flush()
    for i, (code, debit, credit) in enumerate(lines, 1):
        db.add(VoucherLine(voucher_id=v.id, line_no=i, summary="报表净额测试",
                           account_id=accs[code].id, debit=debit, credit=credit))
    db.flush()


add(1, [("1122", 100_000, 0), ("5001.01", 0, 100_000)])
add(2, [("5001.01", 10_000, 0), ("1122", 0, 10_000)])
add(3, [("5602.01", 50_000, 0), ("1002.01", 0, 50_000)])
add(4, [("1002.01", 10_000, 0), ("5602.01", 0, 10_000)])
db.commit()

tpl = db.scalar(select(ReportTemplate).where(ReportTemplate.code == "profit")).definition
pl = calc.profit_statement(db, period, tpl)
rows = {r["key"]: r for r in pl["rows"]}
assert rows["P1"]["value"] == 90_000, rows["P1"]
assert rows["P14"]["value"] == 40_000, rows["P14"]
assert rows["P30"]["value"] == 50_000, rows["P30"]
assert rows["P32"]["value"] == 50_000, rows["P32"]

close_period(period, db, {"username": "admin", "role": "admin"})
pl_after = calc.profit_statement(db, period, tpl)
rows_after = {r["key"]: r for r in pl_after["rows"]}
assert rows_after["P1"]["value"] == 90_000, rows_after["P1"]
assert rows_after["P14"]["value"] == 40_000, rows_after["P14"]
assert rows_after["P32"]["value"] == 50_000, rows_after["P32"]

db.close()
print("利润表净额与结转排除测试全部通过")