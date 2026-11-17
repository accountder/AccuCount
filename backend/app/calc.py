"""账务引擎：余额聚合、报表公式求值、结转损益。

原则：一切取数只认已审核/已入账凭证；金额单位始终是「分」。
"""
from collections import defaultdict
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import BOOKED_STATUSES, Account, Voucher, VoucherLine
from .util import period_add


# ----------------------------------------------------------------- 基础聚合
def _posted_sums(db: Session, start: str | None = None, end: str | None = None):
    """返回 {account_id: (借方合计, 贷方合计)}，只统计已记账凭证。"""
    stmt = (
        select(
            VoucherLine.account_id,
            func.coalesce(func.sum(VoucherLine.debit), 0),
            func.coalesce(func.sum(VoucherLine.credit), 0),
        )
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.status.in_(BOOKED_STATUSES))
        .group_by(VoucherLine.account_id)
    )
    if start:
        stmt = stmt.where(Voucher.period >= start)
    if end:
        stmt = stmt.where(Voucher.period <= end)
    return {aid: (int(d), int(c)) for aid, d, c in db.execute(stmt).all()}


def _natural(acc: Account, d: int, c: int) -> int:
    """按余额方向取净额。借方科目 = 借-贷，贷方科目 = 贷-借。"""
    return d - c if acc.direction == "dr" else c - d


class Book:
    """一次报表/查询所需的账户口径缓存。

    取数规则（关键）：先把匹配前缀的所有科目的「借」「贷」原始发生额分别累加，
    再按「被引用代码本身」的余额方向决定用 借-贷 还是 贷-借。
    这样 2221 应交税费 下同时挂了进项税额(借)与销项税额(贷) 也能正确轧差。
    """

    def __init__(self, db: Session, period: str, period_from: str | None = None):
        """period 为取数截止月；period_from 给出时，「本期」= period_from..period 的区间合计（季报用）。"""
        self.db = db
        self.period = period
        self.period_from = period_from or period
        self.year = int(period[:4])
        self.year_first = f"{self.year:04d}-01"
        self.prev_dec = f"{self.year - 1:04d}-12"

        self.accounts = {a.id: a for a in db.scalars(select(Account)).all()}
        self.by_code = {a.code: a for a in self.accounts.values()}
        self._raw: dict[str, dict[int, tuple[int, int]]] = {}

    def raw(self, mode: str) -> dict[int, tuple[int, int]]:
        if mode in self._raw:
            return self._raw[mode]
        if mode == "end":
            data = _posted_sums(self.db, end=self.period)
        elif mode == "begin":
            data = _posted_sums(self.db, end=self.prev_dec)
        elif mode == "period":
            data = _posted_sums(self.db, start=self.period_from, end=self.period)
        elif mode == "ytd":
            data = _posted_sums(self.db, start=self.year_first, end=self.period)
        else:
            raise ValueError(f"未知取数模式 {mode}")
        self._raw[mode] = data
        return data

    def sum_codes(self, mode: str, codes: Iterable[str]) -> int:
        raw = self.raw(mode)
        total = 0
        for c in codes:
            d_sum = c_sum = 0
            for aid, (d, cr) in raw.items():
                code = self.accounts[aid].code
                if code == c or code.startswith(c + "."):
                    d_sum += d
                    c_sum += cr
            acc = self.by_code.get(c)
            total += (d_sum - c_sum) if (acc is None or acc.direction == "dr") else (c_sum - d_sum)
        return total

    def side_amount(self, mode: str, codes: Iterable[str]) -> int:
        """损益表取数：按科目余额方向取「同向发生额」。

        收入类取贷方发生额、费用类取借方发生额，这样结转损益时产生的
        反向分录（借收入 / 贷费用）不会把报表数字冲掉——与金蝶的
        「损益表本期实际发生额」口径一致。
        """
        raw = self.raw(mode)
        total = 0
        for c in codes:
            d_sum = c_sum = 0
            for aid, (d, cr) in raw.items():
                code = self.accounts[aid].code
                if code == c or code.startswith(c + "."):
                    d_sum += d
                    c_sum += cr
            acc = self.by_code.get(c)
            total += d_sum if (acc is not None and acc.direction == "dr") else c_sum
        return total

    def nets(self, mode: str) -> dict[int, int]:
        """每科目的自然净额（按自身余额方向），结转损益要用。"""
        return {aid: _natural(self.accounts[aid], d, c) for aid, (d, c) in self.raw(mode).items()}

    def profit(self, mode: str) -> int:
        """损益类净利（收入为正、费用为负），用于把未结转利润并入未分配利润。"""
        raw = self.raw(mode)
        total = 0
        for aid, (d, c) in raw.items():
            acc = self.accounts[aid]
            if acc.cat != "pl":
                continue
            total += (c - d) if acc.direction == "cr" else -(d - c)
        return total


# ----------------------------------------------------------------- 报表求值
def _eval_rows(rows, getter, mode_map):
    """按顺序求值，refs 可引用之前已算好的行。返回 (行结果, 行值字典)。"""
    vals: dict[str, int] = {}
    out = []
    for r in rows:
        if r.get("top"):
            out.append({"key": r["key"], "label": r["label"], "no": None,
                        "indent": 0, "bold": False, "top": True, "value": None})
            continue
        total = 0
        for t in r.get("terms", []):
            if "pl" in t:
                total += getter("pl", None) * t.get("sign", 1)
            else:
                mode = mode_map.get(t.get("mode", "end"), t.get("mode", "end"))
                total += getter(mode, t["codes"]) * t.get("sign", 1)
        for ref in r.get("refs", []):
            key = ref[0]
            mult = ref[1] if len(ref) > 1 else 1
            total += vals.get(key, 0) * mult
        vals[r["key"]] = total
        out.append(
            {
                "key": r["key"],
                "label": r["label"],
                "no": r.get("no"),
                "indent": r.get("indent", 0),
                "bold": r.get("bold", False),
                "top": False,
                "value": total,
            }
        )
    return out, vals


def _total_key(rows, tag: str):
    """模板里带 total 标记的行（资产总计 / 负债和所有者权益总计）。"""
    for r in rows:
        if r.get("total") == tag:
            return r["key"]
    return None


def balance_sheet(db: Session, period: str, template: dict):
    book = Book(db, period)

    def getter_end(mode, codes):
        if codes is None:
            return book.profit("end")
        return book.sum_codes(mode, codes)

    def getter_begin(mode, codes):
        # 年初数一律取上年 12 月末余额，不能用行定义里的 mode
        # （行里默认写的是 "end"，照搬就会把期末余额当成年初余额，两列永远一样）
        if codes is None:
            return book.profit("begin")
        return book.sum_codes("begin", codes)

    left_end, lv_end = _eval_rows(template["left"], getter_end, {})
    left_beg, _ = _eval_rows(template["left"], getter_begin, {})
    right_end, rv_end = _eval_rows(template["right"], getter_end, {})
    right_beg, _ = _eval_rows(template["right"], getter_begin, {})

    for rows_end, rows_beg in ((left_end, left_beg), (right_end, right_beg)):
        for i, row in enumerate(rows_end):
            if row["value"] is not None:
                row["value_begin"] = rows_beg[i]["value"]

    total_assets = lv_end.get(_total_key(template["left"], "assets") or "", 0)
    total_le = rv_end.get(_total_key(template["right"], "liab_equity") or "", 0)
    diff = total_assets - total_le
    unclosed_profit = book.profit("end")
    unclosed_accounts = [
        {
            "code": book.accounts[aid].code,
            "name": book.accounts[aid].name,
            "amount": val,
        }
        for aid, val in book.nets("end").items()
        if book.accounts[aid].cat == "pl" and val != 0
    ]
    unclosed_accounts.sort(key=lambda x: x["code"])
    return {
        "title": template["title"],
        "subtitle": template["subtitle"],
        "period": period,
        "columns": template["columns"],
        "left": left_end,
        "right": right_end,
        "total_assets": total_assets,
        "total_liab_equity": total_le,
        "balanced": total_assets == total_le,
        "diff": diff,
        "unclosed_profit": unclosed_profit,
        "unclosed_accounts": unclosed_accounts,
        "diff_matches_unclosed_profit": diff == unclosed_profit,
    }


def profit_statement(db: Session, period: str, template: dict, period_from: str | None = None):
    """period 为截止月（季报时传季末月），period_from 给出时本期数 = period_from..period 区间合计。"""
    book = Book(db, period, period_from)

    def getter_period(mode, codes):
        if codes is None:
            return book.profit("period")
        return book.side_amount(mode, codes)

    def getter_ytd(mode, codes):
        if codes is None:
            return book.profit("ytd")
        return book.side_amount(mode, codes)

    rows_p, vp = _eval_rows(template["rows"], getter_period, {})
    rows_y, _ = _eval_rows(template["rows"], getter_ytd, {"period": "ytd"})
    for i, row in enumerate(rows_p):
        if row["value"] is not None:
            row["value_ytd"] = rows_y[i]["value"]
    net_key = next((r["key"] for r in template["rows"] if r["label"].startswith("四、净利润")), "P32")
    return {
        "title": template["title"],
        "subtitle": template["subtitle"],
        "period": period,
        "columns": template["columns"],
        "rows": rows_p,
        "net_profit": vp.get(net_key, 0),
    }


# ----------------------------------------------------------------- 科目余额表
def trial_balance(db: Session, period: str, level: int = 99, only_used: bool = False,
                  period_from: str | None = None):
    """科目余额表：期初、本期发生、期末。季报时 period_from 为季首月，本期发生=全季合计。"""
    book = Book(db, period)
    prev = period_add(period_from or period, -1)

    raw_prev = _posted_sums(db, end=prev)
    raw_cur = _posted_sums(db, start=period_from or period, end=period)

    rows = []
    used = defaultdict(int)
    for acc in book.accounts.values():
        d0, c0 = raw_prev.get(acc.id, (0, 0))
        d1, c1 = raw_cur.get(acc.id, (0, 0))
        if only_used and not (d0 or c0 or d1 or c1):
            continue
        open_net = d0 - c0
        close_net = open_net + d1 - c1
        rows.append(
            {
                "id": acc.id,
                "code": acc.code,
                "name": acc.name,
                "cat": acc.cat,
                "direction": acc.direction,
                "level": acc.level,
                "is_leaf": acc.is_leaf,
                "open_dr": max(open_net, 0),
                "open_cr": max(-open_net, 0),
                "cur_dr": d1,
                "cur_cr": c1,
                "close_dr": max(close_net, 0),
                "close_cr": max(-close_net, 0),
            }
        )
        used[acc.id] = close_net

    rows.sort(key=lambda r: r["code"])
    if level < 99:
        rows = [r for r in rows if r["level"] <= level]

    tot = {
        "open_dr": sum(r["open_dr"] for r in rows if r["level"] == 1),
        "open_cr": sum(r["open_cr"] for r in rows if r["level"] == 1),
        "cur_dr": sum(r["cur_dr"] for r in rows if r["level"] == 1),
        "cur_cr": sum(r["cur_cr"] for r in rows if r["level"] == 1),
        "close_dr": sum(r["close_dr"] for r in rows if r["level"] == 1),
        "close_cr": sum(r["close_cr"] for r in rows if r["level"] == 1),
    }
    return {"period": period, "period_from": period_from or period, "rows": rows, "totals": tot}


def ledger(db: Session, account_id: int, period_from: str, period_to: str):
    """明细账：指定科目在期间内的逐笔发生与余额。"""
    acc = db.get(Account, account_id)
    if not acc:
        raise ValueError("科目不存在")

    raw_prev = _posted_sums(db, end=period_add(period_from, -1))
    d0, c0 = raw_prev.get(account_id, (0, 0))
    balance = d0 - c0

    stmt = (
        select(VoucherLine, Voucher)
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.status.in_(BOOKED_STATUSES), VoucherLine.account_id == account_id)
        .where(Voucher.period >= period_from, Voucher.period <= period_to)
        .order_by(Voucher.period, Voucher.seq, VoucherLine.line_no)
    )
    lines, t_dr, t_cr = [], 0, 0
    for vl, v in db.execute(stmt).all():
        balance += vl.debit - vl.credit
        t_dr += vl.debit
        t_cr += vl.credit
        lines.append(
            {
                "id": vl.id,
                "voucher_id": v.id,
                "period": v.period,
                "date": v.vdate.isoformat(),
                "no": f"{v.word}-{v.seq:04d}",
                "summary": vl.summary,
                "debit": vl.debit,
                "credit": vl.credit,
                "balance": balance,
                "aux": f"{vl.aux_type}:{vl.aux_name}" if vl.aux_name else "",
            }
        )
    return {
        "account": {"id": acc.id, "code": acc.code, "name": acc.name, "direction": acc.direction},
        "period_from": period_from,
        "period_to": period_to,
        "opening": d0 - c0,
        "lines": lines,
        "total_debit": t_dr,
        "total_credit": t_cr,
        "closing": balance,
    }


# ----------------------------------------------------------------- 凭证校验
def validate_voucher_lines(lines) -> tuple[bool, str]:
    if not lines:
        return False, "凭证至少需要一行分录"
    dr = sum(int(x.get("debit") or 0) for x in lines)
    cr = sum(int(x.get("credit") or 0) for x in lines)
    if dr == 0 and cr == 0:
        return False, "凭证金额不能全为零"
    if dr != cr:
        return False, f"借贷不平衡：借方 {dr/100:,.2f}，贷方 {cr/100:,.2f}，差额 {(dr-cr)/100:,.2f}"
    for i, x in enumerate(lines, 1):
        d = int(x.get("debit") or 0)
        c = int(x.get("credit") or 0)
        if d and c:
            return False, f"第 {i} 行不能同时填借方和贷方"
        if not d and not c:
            return False, f"第 {i} 行金额为空"
        if not x.get("account_id"):
            return False, f"第 {i} 行未选择科目"
    return True, ""


# ----------------------------------------------------------------- 结转损益
def build_carryover(db: Session, period: str):
    """生成结转本期损益的分录行（不含凭证头）。"""
    book = Book(db, period)
    nets = book.nets("period")

    lines = []
    profit_account = None
    total = 0
    for aid, val in nets.items():
        acc = book.accounts.get(aid)
        if not acc or acc.cat != "pl" or val == 0:
            continue
        if acc.direction == "cr":  # 收入类：借方冲平
            lines.append({"account_id": aid, "debit": val, "credit": 0, "summary": f"结转本期损益-{acc.name}"})
            total += val
        else:  # 费用/成本类：贷方冲平
            lines.append({"account_id": aid, "debit": 0, "credit": val, "summary": f"结转本期损益-{acc.name}"})
            total -= val

    if not lines:
        return [], 0

    pa = db.scalar(select(Account).where(Account.code == "3103"))
    if not pa:
        raise ValueError("缺少 3103 本年利润 科目")
    profit_account = pa

    if total > 0:  # 盈利：贷本年利润
        lines.append({"account_id": pa.id, "debit": 0, "credit": total, "summary": "结转本期损益"})
    elif total < 0:  # 亏损：借本年利润
        lines.append({"account_id": pa.id, "debit": -total, "credit": 0, "summary": "结转本期损益"})
    return lines, total
