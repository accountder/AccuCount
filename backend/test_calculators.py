"""核算测算中心回归测试：科目补齐、附加税确认和折旧测算。"""
import os
import tempfile
from datetime import date

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-calculators-")

from sqlalchemy import select  # noqa: E402

from app import asset as asset_engine  # noqa: E402
from app import vat as vat_engine  # noqa: E402
from app.api import (  # noqa: E402
    SurtaxConfirmIn,
    calculate_depreciation,
    confirm_surtax,
    create_surtax_voucher,
    delete_voucher,
    surtax_preview,
)
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Period, SurtaxCalc, Voucher, VoucherLine  # noqa: E402
from app.seed import ensure_chart_accounts, seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
user = {"id": 1, "username": "admin", "role": "admin"}
period = "2026-11"
accounts = {a.code: a for a in db.scalars(select(Account)).all()}

# 科目补齐与幂等升级
required = {
    "2221.01.10", "2221.16", "2221.17", "2221.18", "2221.19",
    "2221.20", "2221.21", "2221.22", "2221.23", "2221.24",
    "5403.08", "5403.09", "5403.10", "5403.11",
}
assert required <= set(accounts), required - set(accounts)
assert accounts["2221"].is_leaf is False
assert ensure_chart_accounts(db) is False
removed = accounts["2221.16"]
db.delete(removed)
db.commit()
assert ensure_chart_accounts(db) is True
db.commit()
accounts = {a.code: a for a in db.scalars(select(Account)).all()}
assert "2221.16" in accounts

# 构造转出未交增值税：本期增值税 10,000 元
v = Voucher(
    period=period, vdate=date(2026, 11, 30), word="记", seq=1,
    note="转出未交增值税", maker="test", auditor="test", status="audited", source="manual",
)
db.add(v)
db.flush()
db.add_all([
    VoucherLine(voucher_id=v.id, line_no=1, summary="转出未交增值税", account_id=accounts["2221.01.05"].id,
                debit=1_000_000, credit=0),
    VoucherLine(voucher_id=v.id, line_no=2, summary="转出未交增值税", account_id=accounts["2221.02"].id,
                debit=0, credit=1_000_000),
])
db.commit()

sources = vat_engine.surtax_source_amounts(db, period)
assert sources[0]["amount"] == 1_000_000, sources
calc_full = vat_engine.calculate_surtax(1_000_000)
assert calc_full["gross_total"] == 120_000
assert calc_full["reduction_total"] == 0
assert calc_full["net_total"] == 120_000
calc_half = vat_engine.calculate_surtax(1_000_000, small_micro=True)
assert calc_half["gross_total"] == 120_000
assert calc_half["reduction_total"] == 60_000
assert calc_half["net_total"] == 60_000
assert [x["net"] for x in calc_half["items"]] == [35_000, 15_000, 10_000]

preview = surtax_preview(period, "transfer", None, True, 700, 300, 200, db, user)
assert preview["base_amount"] == 10_000.0
assert preview["gross_total"] == 1_200.0
assert preview["reduction_total"] == 600.0
assert preview["net_total"] == 600.0
assert preview["can_generate"] is True

body = SurtaxConfirmIn(
    period=period, source="transfer", small_micro=True,
    city_rate=700, education_rate=300, local_education_rate=200,
    note="已核对增值税申报表",
)
confirmed = confirm_surtax(body, db, user)
assert confirmed["saved_confirmed"] is True
row = db.scalar(select(SurtaxCalc).where(SurtaxCalc.period == period))
assert row is not None and row.confirmed and row.net_amount == 60_000

created = create_surtax_voucher(period, db, user)
assert created["total"] == 600.0
voucher = db.get(Voucher, created["voucher_id"])
assert voucher.status == "draft"
assert voucher.source == "surtax"
assert len(voucher.lines) == 6
assert sum(line.debit for line in voucher.lines) == sum(line.credit for line in voucher.lines) == 60_000

# 草稿附加税凭证可删除，且测算快照随之回到待确认状态
assert delete_voucher(created["voucher_id"], db, user)["ok"] is True
assert db.get(Voucher, created["voucher_id"]) is None
db.expire_all()
row = db.scalar(select(SurtaxCalc).where(SurtaxCalc.period == period))
assert row is not None and row.voucher_id is None and row.confirmed is False

# 删除后可以重新确认并生成，不能重复生成有效凭证
confirm_surtax(body, db, user)
created2 = create_surtax_voucher(period, db, user)
assert created2["total"] == 600.0
try:
    create_surtax_voucher(period, db, user)
    raise AssertionError("重复生成附加税凭证未被拦截")
except Exception as exc:
    assert exc.__class__.__name__ == "HTTPException"

# 单资产折旧测算
one = calculate_depreciation(
    original_value=100_000, useful_months=60, salvage_rate=500, method="straight",
    in_service_date="2026-01-15", dep_start_period=None, accum_dep=0,
    current_period="2026-02", db=db, user=user,
)
assert len(one["rows"]) == 60
assert one["summary"]["dep_start_period"] == "2026-02"
assert one["summary"]["monthly_dep"] == 1_583.33
assert one["summary"]["current_period_amount"] == 1_583.33
assert one["summary"]["depreciable_total"] == 95_000.0
assert round(sum(x["amount"] for x in one["rows"]), 2) == 95_000.0

for method in ("ddb", "syoyds"):
    result = calculate_depreciation(
        original_value=100_000, useful_months=60, salvage_rate=500, method=method,
        in_service_date="2026-01-15", dep_start_period=None, accum_dep=0,
        current_period="2026-02", db=db, user=user,
    )
    assert len(result["rows"]) == 60
    assert round(sum(x["amount"] for x in result["rows"]), 2) == 95_000.0

for method, months in (("syoyds", 18), ("ddb", 12)):
    try:
        calculate_depreciation(
            original_value=100_000, useful_months=months, salvage_rate=500, method=method,
            in_service_date="2026-01-15", dep_start_period=None, accum_dep=0,
            current_period="2026-02", db=db, user=user,
        )
        raise AssertionError(f"{method} 不应接受 {months} 个月")
    except Exception as exc:
        assert exc.__class__.__name__ == "HTTPException"

assert db.scalar(select(Period).where(Period.code == period)).status == "open"
db.close()
print("核算测算中心测试全部通过")