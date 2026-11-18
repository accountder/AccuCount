"""期末处理单期间、卡片金额和顺序结账回归测试。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-period-closing-")

from sqlalchemy import select  # noqa: E402

from app.api import (  # noqa: E402
    _current_open_period,
    SurtaxConfirmIn,
    audit_voucher,
    close_period,
    confirm_surtax,
    create_surtax_voucher,
    delete_voucher,
    period_closing_summary,
    reopen_period,
    transfer_vat,
)
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Period, Voucher, VoucherLine  # noqa: E402
from app.seed import seed  # noqa: E402
from app.util import period_add  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
user = {"id": 1, "username": "admin", "role": "admin"}
period = _current_open_period(db)
accs = {a.code: a for a in db.scalars(select(Account)).all()}


def add_voucher(seq, lines):
    v = Voucher(period=period, vdate=date.fromisoformat(period + "-01"), word="记", seq=seq,
                note="测试", maker="test", auditor="test", status="audited", source="manual")
    db.add(v)
    db.flush()
    for i, (code, debit, credit, rate) in enumerate(lines, 1):
        db.add(VoucherLine(voucher_id=v.id, line_no=i, summary="测试", account_id=accs[code].id,
                           debit=debit, credit=credit, tax_rate=rate))
    db.flush()
    return v


add_voucher(1, [
    ("1122", 226_000_00, 0, 0),
    ("5001.01", 0, 200_000_00, 0),
    ("2221.01.02", 0, 26_000_00, 1300),
])
add_voucher(2, [
    ("1403.01", 100_000_00, 0, 0),
    ("2221.01.01", 13_000_00, 0, 1300),
    ("2202", 0, 113_000_00, 0),
])
db.commit()

summary = period_closing_summary(period, db, user)
assert summary["period"] == period
cards = {c["key"]: c for c in summary["cards"]}
assert cards["vat_transfer"]["amount"] == 13_000.0, cards["vat_transfer"]
assert cards["vat_transfer"]["can_generate"] is True
assert cards["carryover"]["amount"] == 200_000.0, cards["carryover"]
assert len(summary["cards"]) == 5

created = transfer_vat(period, db, user)
assert created["amount"] == 13_000.0
v = db.get(Voucher, created["voucher_id"])
assert v.status == "draft" and v.source == "vat_transfer"
summary = period_closing_summary(period, db, user)
card = {c["key"]: c for c in summary["cards"]}["vat_transfer"]
assert card["status"] == "draft" and card["voucher"]["no"] == created["voucher_no"]

# 草稿未审核时不能结账
try:
    close_period(period, db, user)
    raise AssertionError("存在未审核期末凭证仍允许结账")
except Exception as exc:
    assert exc.__class__.__name__ == "HTTPException"

# 删除草稿后可以重新生成，说明关联清理正确
assert delete_voucher(v.id, db, user)["ok"] is True
created = transfer_vat(period, db, user)
audit_voucher(created["voucher_id"], db, user)

surtax_body = SurtaxConfirmIn(
    period=period, source="transfer", small_micro=True,
    city_rate=700, education_rate=300, local_education_rate=200,
    note="期末卡片流程测试",
)
confirm_surtax(surtax_body, db, user)
surtax_voucher = create_surtax_voucher(period, db, user)
audit_voucher(surtax_voucher["voucher_id"], db, user)

result = close_period(period, db, user)
assert result["ok"] is True
assert db.scalar(select(Period).where(Period.code == period)).status == "closed"
assert _current_open_period(db) == period_add(period, 1), _current_open_period(db)

# 不能跳过当前期提前结账
try:
    close_period("2027-01", db, user)
    raise AssertionError("允许跳过当前期结账")
except Exception as exc:
    assert exc.__class__.__name__ == "HTTPException"

# 最近已结期间可以反结账
reopen_period(period, db, user)
assert db.scalar(select(Period).where(Period.code == period)).status == "open"
assert _current_open_period(db) == period

db.close()
print("期末单期间与卡片流程测试全部通过")