"""增值税台账与申报底稿。

两条取数路线，缺一不可：

1. **按税率分档**——申报表要求分 13%/9%/6% 列示销售额和税额，凭证行上
   的 tax_rate 就是为此存在的。分档销售额由税额倒推（税额 ÷ 税率），
   这与申报表「销售额 × 税率 = 税额」的校验关系天然一致。
2. **按科目累计**——期末是应交还是留抵，只能看 2221.01 应交增值税及其
   明细的累计余额。本期算出来的应纳税额如果小于上期留抵，就不用交税，
   差额继续挂账，这就是留抵。用累计余额判断可以自动处理这种情况，
   不需要手工维护一张「留抵结转表」。

申报表行次按国家税务总局 2019 年第 15 号公告后的合并版主表编排。
"""
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import BOOKED_STATUSES, Account, Voucher, VoucherLine
from .util import period_add

# 增值税一般纳税人主表行次（合并版）
LINES = [
    ("1", "按适用税率计税销售额"),
    ("11", "销项税额"),
    ("12", "进项税额"),
    ("13", "上期留抵税额"),
    ("14", "进项税额转出"),
    ("17", "应抵扣税额合计"),
    ("18", "实际抵扣税额"),
    ("19", "应纳税额"),
    ("20", "期末留抵税额"),
    ("24", "应纳税额合计"),
    ("28", "①分次预缴税额"),
    ("32", "本期已缴税额"),
    ("34", "本期应补（退）税额"),
]

RATE_LABEL = {1300: "13%", 900: "9%", 600: "6%", 500: "5%", 300: "3%", 100: "1%", 0: "未标记"}

# 附加税费：以实际缴纳的增值税为计税依据
SURTAX = [
    ("2221.08", "城市维护建设税", 700),
    ("2221.09", "教育费附加", 300),
    ("2221.10", "地方教育附加", 200),
]


def _acc_ids(db: Session, prefix: str) -> set[int]:
    return {a.id for a in db.scalars(select(Account)).all() if a.code == prefix or a.code.startswith(prefix + ".")}


def _sum_by_rate(db: Session, account_prefix: str, start: str, end: str, side: str):
    """按税率分档汇总指定科目前缀下某方向的发生额。

    side='credit' 取贷方（销项、转出），side='debit' 取借方（进项、已交）。
    """
    ids = _acc_ids(db, account_prefix)
    if not ids:
        return {}
    col = VoucherLine.credit if side == "credit" else VoucherLine.debit
    stmt = (
        select(VoucherLine.tax_rate, func.coalesce(func.sum(col), 0))
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(
            Voucher.status.in_(BOOKED_STATUSES),
            Voucher.period >= start,
            Voucher.period <= end,
            VoucherLine.account_id.in_(ids),
        )
        .group_by(VoucherLine.tax_rate)
    )
    return {int(r or 0): int(v or 0) for r, v in db.execute(stmt).all()}


def _sum_side(db: Session, account_prefix: str, start: str, end: str, side: str) -> int:
    return sum(_sum_by_rate(db, account_prefix, start, end, side).values())


def _cum_net(db: Session, period: str) -> int:
    """2221.01 应交增值税的累计净额（贷-借）。正数是应交，负数是留抵。

    刻意排除「转出未交增值税 / 转出多交增值税」（2221.01.05/06）：这两个
    科目一出现就把 2221.01 冲平了，如果算进来，期初留抵和本期应纳税额都会
    失真。排除之后，这个净额就纯粹是「累计销项 - 累计进项 + 转出 - 已交」，
    正好等于申报表的口径。
    """
    skip = {"2221.01.05", "2221.01.06"}
    ids = {
        a.id
        for a in db.scalars(select(Account)).all()
        if (a.code == "2221.01" or a.code.startswith("2221.01.")) and a.code not in skip
    }
    if not ids:
        return 0
    stmt = (
        select(
            func.coalesce(func.sum(VoucherLine.credit), 0),
            func.coalesce(func.sum(VoucherLine.debit), 0),
        )
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.status.in_(BOOKED_STATUSES), Voucher.period <= period, VoucherLine.account_id.in_(ids))
    )
    cr, dr = db.execute(stmt).one()
    return int(cr or 0) - int(dr or 0)


def book(db: Session, period: str, adjust_output: int = 0, adjust_input: int = 0):
    """一期的增值税台账。adjust_* 为手工调整项（未开票收入等）。"""
    start = end = period
    prev = period_add(period, -1)

    out_by_rate = _sum_by_rate(db, "2221.01.02", start, end, "credit")
    out_rev_by_rate = _sum_by_rate(db, "2221.01.02", start, end, "debit")  # 红冲
    in_by_rate = _sum_by_rate(db, "2221.01.01", start, end, "debit")
    in_rev_by_rate = _sum_by_rate(db, "2221.01.01", start, end, "credit")
    transfer_out = _sum_side(db, "2221.01.03", start, end, "credit") - _sum_side(db, "2221.01.03", start, end, "debit")
    paid = _sum_side(db, "2221.01.04", start, end, "debit")  # 已交税金

    def merge(a: dict, b: dict, sign: int = -1):
        out = defaultdict(int)
        for k, v in a.items():
            out[k] += v
        for k, v in b.items():
            out[k] += sign * v
        return dict(out)

    out_net = merge(out_by_rate, out_rev_by_rate)
    in_net = merge(in_by_rate, in_rev_by_rate)

    output_tax = sum(out_net.values()) + adjust_output
    input_tax = sum(in_net.values()) + adjust_input
    deductible = max(input_tax - transfer_out, 0)

    # 前期累计决定期初留抵；留抵不做转出凭证，所以取负数部分就是留抵额
    cum_prev = _cum_net(db, prev)
    cum_cur = _cum_net(db, period)
    opening_credit = max(-cum_prev, 0)
    opening_payable = max(cum_prev, 0)  # 上期末应交未缴（未做转出时才有）

    # 申报表口径：
    #   行17 应抵扣税额合计 = 进项 + 上期留抵 − 进项转出
    #   行18 实际抵扣税额   = min(行17, 销项)
    #   行19 应纳税额       = 销项 − 行18
    #   行20 期末留抵       = 行17 − 行18
    available = input_tax + opening_credit - transfer_out
    actual = min(max(available, 0), max(output_tax, 0))
    payable = max(output_tax, 0) - actual
    closing_credit = max(available - max(output_tax, 0), 0)

    period_net = output_tax - deductible  # 本期形成的净税额（不含期初留抵）
    period_move = cum_cur - cum_prev
    diff = (period_move + paid) - period_net

    # 销售额：按税率倒推（税额 ÷ 税率），未标记税率的税额单独列出
    sales_by_rate, tax_by_rate = [], []
    for rate in sorted({**out_net, **in_net}.keys(), reverse=True):
        t = out_net.get(rate, 0)
        s = round(t * 10000 / rate) if rate else 0
        sales_by_rate.append(
            {"rate": rate, "label": RATE_LABEL.get(rate, f"{rate/100:.0f}%"), "sales": s, "tax": t}
        )
        tax_by_rate.append(
            {"rate": rate, "label": RATE_LABEL.get(rate, f"{rate/100:.0f}%"), "amount": in_net.get(rate, 0)}
        )

    total_sales = sum(x["sales"] for x in sales_by_rate)

    return {
        "period": period,
        "output_tax": output_tax,
        "output_by_rate": sales_by_rate,
        "input_tax": input_tax,
        "input_by_rate": tax_by_rate,
        "transfer_out": transfer_out,
        "deductible": deductible,
        "opening_credit": opening_credit,
        "opening_payable": opening_payable,
        "payable": payable,
        "closing_credit": closing_credit,
        "paid": paid,
        "unpaid": max(payable - paid, 0),
        "total_sales": total_sales,
        "book_payable": max(cum_cur, 0),
        "book_credit": max(-cum_cur, 0),
        "diff": diff,
        "lines": {
            "1": total_sales,
            "11": output_tax,
            "12": input_tax,
            "13": opening_credit,
            "14": transfer_out,
            "17": available,
            "18": actual,
            "19": payable,
            "20": closing_credit,
            "24": payable,
            "28": 0,
            "32": paid,
            "34": max(payable - paid, 0),
        },
        "surtax": [
            {"code": c, "name": n, "rate": r, "amount": round(payable * r / 10000)} for c, n, r in SURTAX
        ],
    }


def table(db: Session, year: int):
    """年度台账：只列有涉税发生额的月份，空月不占行。"""
    rows = []
    for m in range(1, 13):
        p = f"{year:04d}-{m:02d}"
        b = book(db, p)
        if not any(b[k] for k in ("total_sales", "output_tax", "input_tax", "transfer_out", "paid")):
            continue
        rows.append(
            {
                "period": p,
                "sales": b["total_sales"],
                "output": b["output_tax"],
                "input": b["input_tax"],
                "transfer_out": b["transfer_out"],
                "payable": b["payable"],
                "closing_credit": b["closing_credit"],
                "paid": b["paid"],
                "unpaid": b["unpaid"],
            }
        )
    return {
        "year": year,
        "rows": rows,
        "total": {
            "sales": sum(r["sales"] for r in rows),
            "output": sum(r["output"] for r in rows),
            "input": sum(r["input"] for r in rows),
            "payable": sum(r["payable"] for r in rows),
            "paid": sum(r["paid"] for r in rows),
        },
    }


def declaration(db: Session, period: str):
    """申报底稿：主表行次 + 附列资料一/二 + 附加税费。"""
    b = book(db, period)
    return {
        "period": period,
        "main": [{"no": no, "label": label, "value": b["lines"].get(no, 0)} for no, label in LINES],
        "sheet1": b["output_by_rate"],
        "sheet2": {
            "detail": b["input_by_rate"],
            "transfer_out": b["transfer_out"],
            "total": b["input_tax"],
        },
        "surtax": b["surtax"],
        "summary": {k: b[k] for k in ("output_tax", "input_tax", "deductible", "payable", "closing_credit", "paid", "unpaid")},
    }


def check(db: Session, period: str):
    """申报前自检：账面与台账是否一致、有没有漏填税率的涉税行。"""
    b = book(db, period)
    issues = []

    if abs(b["diff"]) > 0:
        issues.append(
            {
                "level": "warn",
                "msg": f"本期 2221.01 账面变动与台账应纳税额差 {b['diff']/100:,.2f} 元。"
                f"常见原因：做了「转出未交增值税」凭证（属正常），或涉税科目用错。",
            }
        )

    unmarked = b["output_by_rate"] and any(x["rate"] == 0 and x["tax"] for x in b["output_by_rate"])
    if unmarked:
        issues.append(
            {
                "level": "error",
                "msg": "有销项税额未标记税率，附列资料一无法分档。请到凭证里补填税率。",
            }
        )
    if b["transfer_out"] > 0:
        issues.append({"level": "info", "msg": f"本期进项税额转出 {b['transfer_out']/100:,.2f} 元。"})
    if b["closing_credit"] > 0:
        issues.append({"level": "info", "msg": f"本期期末留抵 {b['closing_credit']/100:,.2f} 元，无需缴纳增值税。"})

    return {"period": period, "issues": issues, "balanced": abs(b["diff"]) == 0}


def surtax_voucher_lines(db: Session, period: str) -> list[dict]:
    """生成附加税费计提分录：借 税金及附加-各税种 / 贷 应交税费-各税种。

    城建税、教育费附加、地方教育附加都以实际应纳增值税为计税依据，
    费率分别是 7%（市区）、3%、2%。小规模或优惠地区可在此调整。
    """
    b = book(db, period)
    base = b["payable"]
    if base <= 0:
        return [], 0

    expense_of = {
        "2221.08": "5403.01",
        "2221.09": "5403.02",
        "2221.10": "5403.03",
    }
    lines, total = [], 0
    for code, name, rate in SURTAX:
        amt = round(base * rate / 10000)
        if amt <= 0:
            continue
        acc = db.scalar(select(Account).where(Account.code == code))
        exp = db.scalar(select(Account).where(Account.code == expense_of.get(code, "")))
        if not acc or not exp:
            continue
        lines.append({"account_id": exp.id, "debit": amt, "credit": 0, "summary": f"计提{name}"})
        lines.append({"account_id": acc.id, "debit": 0, "credit": amt, "summary": f"计提{name}"})
        total += amt
    return lines, total
