"""期初余额模块与报表取数测试。"""
import os
import tempfile

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-opening-")

from sqlalchemy import select  # noqa: E402

from app import calc  # noqa: E402
from app.api import (  # noqa: E402
    OpeningIn,
    OpeningLineIn,
    _opening_year_opening,
    confirm_opening,
    save_opening,
    unconfirm_opening,
)
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Account, Company, OpeningLine, Period, ReportTemplate, Voucher  # noqa: E402
from app.seed import BALANCE_SHEET, PROFIT, ensure_periods, seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
company = db.scalar(select(Company).limit(1))
company.start_period = "2025-01"
ensure_periods(db, 2025)
db.commit()
accs = {a.code: a for a in db.scalars(select(Account)).all()}
user = {"id": 1, "username": "admin", "role": "admin"}

# 图片示例：库存现金期初90，本年累计借100、贷600，年初余额应为590
assert _opening_year_opening(accs["1001"], 90_00, 100_00, 600_00) == 590_00
# 贷方科目公式：期初 + 本年借 - 本年贷
assert _opening_year_opening(accs["2202"], 500_00, 100_00, 200_00) == 400_00
# 损益类年初余额恒为零
assert _opening_year_opening(accs["5001.01"], 0, 9_000_00, 9_000_00) == 0

lines = [
    OpeningLineIn(account_id=accs["1002.01"].id, opening_balance=105_000, year_opening=105_000),
    OpeningLineIn(account_id=accs["1122"].id, aux_type="customer", aux_name="测试客户", opening_balance=20_000, year_opening=20_000),
    OpeningLineIn(account_id=accs["1122"].id, aux_type="customer", aux_name="负数客户", opening_balance=-5_000, year_opening=-5_000),
    OpeningLineIn(account_id=accs["1403.01"].id, opening_balance=30_000, year_opening=30_000),
    OpeningLineIn(account_id=accs["1601.04"].id, opening_balance=50_000, year_opening=50_000),
    OpeningLineIn(account_id=accs["1602.04"].id, opening_balance=10_000, year_opening=10_000),
    OpeningLineIn(account_id=accs["2202"].id, aux_type="supplier", aux_name="测试供应商", opening_balance=15_000, year_opening=15_000),
    OpeningLineIn(account_id=accs["2221.08"].id, opening_balance=5_000, year_opening=5_000),
    OpeningLineIn(account_id=accs["3001"].id, opening_balance=100_000, year_opening=100_000),
    OpeningLineIn(account_id=accs["3104"].id, opening_balance=70_000, year_opening=70_000),
]
saved = save_opening(OpeningIn(start_period="2025-01", note="测试期初", lines=lines), db, user)
assert saved["batch"]["status"] == "draft"
assert saved["totals"]["balanced"] is True, saved["totals"]

result = confirm_opening(saved["batch"]["id"], db, user)
assert result["ok"] is True
assert result["voucher_no"]
assert any(r["code"] == "2221.01" and r["is_leaf"] is False for r in result["rows"])
assert any(r["code"] == "5403" and r["is_leaf"] is False for r in result["rows"])
negative = db.scalar(select(OpeningLine).where(OpeningLine.aux_name == "负数客户"))
assert negative is not None and negative.opening_balance == -500_000
v = db.scalar(select(Voucher).where(Voucher.source == "opening"))
assert v is not None and v.period == "2024-12" and v.status == "audited"
assert db.scalar(select(Period).where(Period.code == "2024-12")).status == "closed"

bs = calc.balance_sheet(db, "2025-01", BALANCE_SHEET)
assert bs["balanced"], bs
assert bs["total_assets"] == 19_000_000, bs["total_assets"]
assert bs["total_liab_equity"] == 19_000_000, bs["total_liab_equity"]

tb = calc.trial_balance(db, "2025-01")
assert tb["totals"]["close_dr"] == tb["totals"]["close_cr"], tb["totals"]
pl = calc.profit_statement(db, "2025-01", PROFIT)
rows = {r["key"]: r for r in pl["rows"]}
assert rows["P30"]["value_ytd"] == 0
assert rows["P32"]["value_ytd"] == 0

# 反确认后应删除期初凭证
unconfirm_opening(saved["batch"]["id"], db, user)
assert db.scalar(select(Voucher).where(Voucher.source == "opening")) is None

# 上级科目手工覆盖：与下级合计不一致只提示，不阻止保存；凭证不能重复计入下级
override_lines = [
    OpeningLineIn(account_id=accs["1002.01"].id, opening_balance=40_000),
    OpeningLineIn(
        account_id=accs["2221.01"].id,
        opening_balance=10_000,
        manual_override=True,
    ),
    OpeningLineIn(account_id=accs["2221.01.02"].id, opening_balance=12_000),
    OpeningLineIn(account_id=accs["3001"].id, opening_balance=30_000),
]
override = save_opening(
    OpeningIn(start_period="2025-03", note="上级科目手工覆盖测试", lines=override_lines),
    db,
    user,
)
assert override["totals"]["balanced"] is True, override["totals"]
parent_row = next(row for row in override["rows"] if row["code"] == "2221.01")
assert parent_row["manual_override"] is True
assert parent_row["opening_balance"] == 10_000.00, parent_row
assert parent_row["child_opening_balance"] == 12_000.00
assert parent_row["differences"]["opening_balance"] == -2_000.00
warning = next(item for item in override["warnings"] if item["code"] == "2221.01")
assert warning["differences"]["opening_balance"] == -2_000.00

override_result = confirm_opening(override["batch"]["id"], db, user)
assert override_result["ok"] is True
opening_voucher = db.scalar(select(Voucher).where(Voucher.source == "opening"))
assert opening_voucher is not None
voucher_accounts = [
    line.account_id
    for line in opening_voucher.lines
]
assert accs["2221.01"].id in voucher_accounts
assert accs["2221.01.02"].id not in voucher_accounts
unconfirm_opening(override["batch"]["id"], db, user)
assert db.scalar(select(Voucher).where(Voucher.source == "opening")) is None

# 年中启用：期初模块的损益实际发生额进入利润表本年累计
mid_lines = [
    OpeningLineIn(account_id=accs["1002.01"].id, opening_balance=190_000),
    OpeningLineIn(account_id=accs["2202"].id, aux_type="supplier", aux_name="年中供应商", opening_balance=20_000),
    OpeningLineIn(account_id=accs["3001"].id, opening_balance=100_000),
    OpeningLineIn(account_id=accs["3103"].id, opening_balance=60_000),
    OpeningLineIn(account_id=accs["3104"].id, opening_balance=10_000),
    OpeningLineIn(account_id=accs["5001.01"].id, actual_pl=100_000, ytd_credit=100_000),
    OpeningLineIn(account_id=accs["5401"].id, actual_pl=40_000, ytd_debit=40_000),
]
mid = save_opening(OpeningIn(start_period="2025-07", note="年中测试期初", lines=mid_lines), db, user)
confirm_opening(mid["batch"]["id"], db, user)
bs_mid = calc.balance_sheet(db, "2025-07", BALANCE_SHEET)
assert bs_mid["balanced"], bs_mid
assert bs_mid["total_assets"] == 19_000_000
pl_mid = calc.profit_statement(db, "2025-07", PROFIT)
rows_mid = {r["key"]: r for r in pl_mid["rows"]}
assert rows_mid["P1"]["value_ytd"] == 10_000_000, rows_mid["P1"]
assert rows_mid["P2"]["value_ytd"] == 4_000_000, rows_mid["P2"]
assert rows_mid["P32"]["value_ytd"] == 6_000_000, rows_mid["P32"]

db.close()
print("期初余额与报表勾稽测试全部通过")

