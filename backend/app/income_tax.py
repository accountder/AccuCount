"""季度企业所得税预提测算。

季度模块只按累计会计利润预提，不处理年度汇算清缴的纳税调增、调减。
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import calc
from .models import BOOKED_STATUSES, Account, IncomeTaxCalc, ReportTemplate, Voucher, VoucherLine
from .util import period_add

QUARTER_END_MONTHS = {3, 6, 9, 12}
SMALL_MICRO_LIMIT = 300_000_000  # 300 万元，分
ASSET_LIMIT = 5_000_000_000      # 5000 万元，分
EMPLOYEE_LIMIT = 300


def quarter_of(period: str) -> int:
    return (int(period[5:7]) - 1) // 3 + 1


def is_quarter_end(period: str) -> bool:
    return int(period[5:7]) in QUARTER_END_MONTHS


def ytd_profit_before_tax(db: Session, period: str) -> int:
    template = db.scalar(select(ReportTemplate).where(ReportTemplate.code == "profit"))
    if template is None:
        raise ValueError("缺少利润表模板")
    report = calc.profit_statement(db, period, template.definition)
    row = next((r for r in report["rows"] if r.get("key") == "P30"), None)
    if row is None:
        raise ValueError("利润表缺少利润总额行")
    return int(row.get("value_ytd") or 0)


def previous_accrued_tax(db: Session, year: int, quarter: int) -> int:
    rows = db.scalars(
        select(IncomeTaxCalc).where(
            IncomeTaxCalc.year == year,
            IncomeTaxCalc.quarter < quarter,
            IncomeTaxCalc.confirmed.is_(True),
        )
    ).all()
    total = 0
    for row in rows:
        if row.voucher_id:
            voucher = db.get(Voucher, row.voucher_id)
            if voucher is not None and voucher.status == "void":
                continue
        total += row.current_accrual
    return total


def previous_book_expense(db: Session, period: str) -> int:
    """以前期间账面所得税费用（5801），排除结转损益凭证。"""
    start = f"{period[:4]}-01"
    end = period_add(period, -1)
    if end < start:
        return 0
    ids = {
        a.id for a in db.scalars(select(Account)).all()
        if a.code == "5801" or a.code.startswith("5801.")
    }
    if not ids:
        return 0
    stmt = (
        select(
            func.coalesce(func.sum(VoucherLine.debit), 0),
            func.coalesce(func.sum(VoucherLine.credit), 0),
        )
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(
            Voucher.status.in_(BOOKED_STATUSES),
            Voucher.source != "carryover",
            Voucher.period >= start,
            Voucher.period <= end,
            VoucherLine.account_id.in_(ids),
        )
    )
    debit, credit = db.execute(stmt).one()
    return int(debit or 0) - int(credit or 0)


def calculate(
    db: Session,
    period: str,
    *,
    prior_loss: int = 0,
    employee_count: int = 0,
    total_assets: int = 0,
    industry_ok: bool = True,
) -> dict:
    if not is_quarter_end(period):
        return {
            "applicable": False,
            "period": period,
            "quarter": quarter_of(period),
            "ytd_profit": 0,
            "prior_loss": 0,
            "taxable_income": 0,
            "employee_count": max(int(employee_count), 0),
            "total_assets": max(int(total_assets), 0),
            "industry_ok": bool(industry_ok),
            "small_micro": False,
            "effective_rate": 2500,
            "cumulative_tax": 0,
            "previous_accrued": 0,
            "current_accrual": 0,
            "conditions": {},
        }

    ytd_profit = ytd_profit_before_tax(db, period)
    prior_loss = max(int(prior_loss), 0)
    taxable_income = max(ytd_profit - prior_loss, 0)
    employee_count = max(int(employee_count), 0)
    total_assets = max(int(total_assets), 0)
    conditions = {
        "income_ok": taxable_income <= SMALL_MICRO_LIMIT,
        "employees_ok": 0 < employee_count <= EMPLOYEE_LIMIT,
        "assets_ok": 0 < total_assets <= ASSET_LIMIT,
        "industry_ok": bool(industry_ok),
    }
    small_micro = taxable_income > 0 and all(conditions.values())
    effective_rate = 500 if small_micro else 2500
    cumulative_tax = (taxable_income * effective_rate + 5000) // 10000
    quarter = quarter_of(period)
    previous_accrued = previous_accrued_tax(db, int(period[:4]), quarter)
    book_previous = previous_book_expense(db, period)
    previous_basis = book_previous if book_previous != 0 else previous_accrued
    current_accrual = cumulative_tax - previous_basis
    return {
        "applicable": True,
        "period": period,
        "quarter": quarter,
        "ytd_profit": ytd_profit,
        "prior_loss": prior_loss,
        "taxable_income": taxable_income,
        "employee_count": employee_count,
        "total_assets": total_assets,
        "industry_ok": bool(industry_ok),
        "small_micro": small_micro,
        "effective_rate": effective_rate,
        "cumulative_tax": cumulative_tax,
        "previous_accrued": previous_basis,
        "previous_snapshot": previous_accrued,
        "previous_book_expense": book_previous,
        "previous_diff": previous_accrued - book_previous,
        "current_accrual": current_accrual,
        "conditions": conditions,
        "calculated_at": datetime.now().isoformat(),
    }


def save_snapshot(db: Session, result: dict, user: str, note: str = "") -> IncomeTaxCalc:
    row = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == result["period"]))
    if row is None:
        row = IncomeTaxCalc(period=result["period"])
        db.add(row)
    row.year = int(result["period"][:4])
    row.quarter = result["quarter"]
    row.ytd_profit = result["ytd_profit"]
    row.prior_loss = result["prior_loss"]
    row.taxable_income = result["taxable_income"]
    row.employee_count = result["employee_count"]
    row.total_assets = result["total_assets"]
    row.industry_ok = result["industry_ok"]
    row.small_micro = result["small_micro"]
    row.effective_rate = result["effective_rate"]
    row.cumulative_tax = result["cumulative_tax"]
    row.previous_accrued = result["previous_accrued"]
    row.current_accrual = result["current_accrual"]
    row.note = note[:200]
    row.confirmed = True
    row.confirmed_by = user
    row.confirmed_at = datetime.now()
    row.voucher_id = None
    return row