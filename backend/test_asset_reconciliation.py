"""固定资产卡片与 1601/1602 总账勾稽测试。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-asset-recon-")

from sqlalchemy import select  # noqa: E402

from app.api import (  # noqa: E402
    _current_open_period,
    asset_summary,
    audit_voucher,
    close_period,
    period_closing_summary,
    post_depreciation,
)
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Asset, Voucher, VoucherLine  # noqa: E402
from app.seed import seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
user = {"id": 1, "username": "admin", "role": "admin"}
period = _current_open_period(db)
accs = {a.code: a for a in db.scalars(select(Account)).all()}

a = Asset(
    code="FA001", name="勾稽测试资产", category="电子设备", dept="财务部",
    asset_account_id=accs["1601.04"].id, dep_account_id=accs["1602.04"].id,
    expense_account_id=accs["5602.02"].id, original_value=12_000_00,
    salvage_rate=500, useful_months=36, method="straight",
    in_service_date=date(2025, 12, 15), dep_start_period="2026-01", status="in_use",
)
db.add(a)
db.commit()

dep = post_depreciation(period, db, user)
audit_voucher(dep["voucher_id"], db, user)
summary = period_closing_summary(period, db, user)
assert summary["asset_reconciliation"]["ok"] is False
try:
    close_period(period, db, user)
    raise AssertionError("总账缺固定资产仍允许结账")
except Exception as exc:
    assert exc.__class__.__name__ == "HTTPException"
    assert "固定资产卡片" in str(exc)

v = Voucher(period=period, vdate=date.fromisoformat(period + "-01"), word="记", seq=10,
            note="补固定资产入账", maker="test", auditor="test", status="audited", source="manual")
db.add(v)
db.flush()
db.add_all([
    VoucherLine(voucher_id=v.id, line_no=1, summary="固定资产入账", account_id=accs["1601.04"].id,
                debit=12_000_00, credit=0),
    VoucherLine(voucher_id=v.id, line_no=2, summary="固定资产入账", account_id=accs["1002.01"].id,
                debit=0, credit=12_000_00),
])
db.commit()
summary = period_closing_summary(period, db, user)
assert summary["asset_reconciliation"]["ok"] is True
assert asset_summary(period, db, user)["total"]["original"] == 12_000.0

db.close()
print("固定资产总账勾稽测试全部通过")