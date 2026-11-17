"""往来管理：台账、账龄、自动核销、对账单。

口径说明（与金蝶/用友一致的部分）：
- 「挂账」= 形成债权债务的那一侧发生额：应收/预付/应收票据在借方，
  应付/预收/应付票据在贷方。
- 「收回」= 反向发生额。一笔 1122 的贷方行就是客户还款（或转销）。
- 账龄按「挂账凭证日期」到「分析截止日」的天数算，而不是按凭证录入顺序。
- 核销按 FIFO：先还最早的账。这是实务默认做法，也是税务查账龄时的口径。
"""
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, AgingBand, Partner, Settle, Voucher, VoucherLine

# 往来科目 → (档案类型, 是资产类还是负债类)
PARTNER_ACCOUNTS: dict[str, tuple[str, str]] = {
    "1121": ("customer", "asset"),  # 应收票据
    "1122": ("customer", "asset"),  # 应收账款
    "1123": ("supplier", "asset"),  # 预付账款
    "2201": ("supplier", "liability"),  # 应付票据
    "2202": ("supplier", "liability"),  # 应付账款
    "2203": ("customer", "liability"),  # 预收账款
}

ACCOUNT_LABEL = {
    "1121": "应收票据",
    "1122": "应收账款",
    "1123": "预付账款",
    "2201": "应付票据",
    "2202": "应付账款",
    "2203": "预收账款",
}


def _match_account(code: str) -> str | None:
    """把明细科目编码归到往来一级科目（1122.01 → 1122）。"""
    for root in PARTNER_ACCOUNTS:
        if code == root or code.startswith(root + "."):
            return root
    return None


def _period_end(period: str) -> date:
    from .util import period_to_date

    return period_to_date(period, end=True)


def _load_lines(db: Session, period_end: str):
    """取出截止期末已记账、且挂着往来单位名的所有凭证行。"""
    stmt = (
        select(VoucherLine, Voucher, Account)
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .join(Account, Account.id == VoucherLine.account_id)
        .where(Voucher.status == "posted", Voucher.period <= period_end)
        .order_by(Voucher.vdate, Voucher.seq, VoucherLine.line_no)
    )
    out = []
    for vl, v, acc in db.execute(stmt).all():
        root = _match_account(acc.code)
        if not root:
            continue
        name = (vl.aux_name or "").strip()
        if not name:
            continue
        out.append(
            {
                "line_id": vl.id,
                "voucher_id": v.id,
                "voucher_no": f"{v.word}-{v.seq:04d}",
                "date": v.vdate,
                "period": v.period,
                "summary": vl.summary,
                "root": root,
                "account_code": acc.code,
                "account_name": acc.name,
                "partner_name": name,
                "debit": vl.debit,
                "credit": vl.credit,
                "aux_type": vl.aux_type or PARTNER_ACCOUNTS[root][0],
            }
        )
    return out


def _split_open_pay(row: dict) -> tuple[int, int]:
    """返回 (挂账额, 收回额)。资产类借方挂账、负债类贷方挂账。"""
    kind = PARTNER_ACCOUNTS[row["root"]][1]
    if kind == "asset":
        return row["debit"], row["credit"]
    return row["credit"], row["debit"]


def _settled_map(db: Session) -> dict[int, int]:
    """已核销金额：{open_line_id: 累计核销额}。"""
    out: dict[int, int] = defaultdict(int)
    for s in db.scalars(select(Settle)).all():
        out[s.open_line_id] += s.amount
    return out


def _bands(db: Session):
    rows = db.scalars(select(AgingBand).where(AgingBand.enabled == True).order_by(AgingBand.seq)).all()  # noqa: E712
    if not rows:
        return [{"seq": 1, "label": "全部", "days_from": 0, "days_to": 0}]
    return [{"seq": r.seq, "label": r.label, "days_from": r.days_from, "days_to": r.days_to} for r in rows]


def _band_of(days: int, bands) -> int:
    for b in bands:
        if days >= b["days_from"] and (b["days_to"] == 0 or days <= b["days_to"]):
            return b["seq"]
    return bands[-1]["seq"]


def ledger(db: Session, period: str, ptype: str | None = None, only_unsettled: bool = True):
    """往来台账：按单位汇总余额，并给出逐笔挂账与账龄。"""
    end = _period_end(period)
    rows = _load_lines(db, period)
    settled = _settled_map(db)
    bands = _bands(db)

    partners = {
        (p.ptype, p.name): p for p in db.scalars(select(Partner)).all()
    }

    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["aux_type"], r["partner_name"])
        if ptype and r["aux_type"] != ptype:
            continue
        g = groups.setdefault(
            key,
            {
                "ptype": r["aux_type"],
                "name": r["partner_name"],
                "partner_id": (partners.get(key).id if partners.get(key) else None),
                "settle_days": (partners.get(key).settle_days if partners.get(key) else 30),
                "credit_limit": (partners.get(key).credit_limit if partners.get(key) else 0),
                "items": [],
                "open_total": 0,
                "paid_total": 0,
                "balance": 0,
                "oldest_days": 0,
                "overdue": 0,
                "bands": defaultdict(int),
            },
        )
        open_amt, pay_amt = _split_open_pay(r)
        done = settled.get(r["line_id"], 0)
        remain = open_amt - done if open_amt else 0
        days = (end - r["date"]).days if open_amt and remain > 0 else 0

        g["open_total"] += open_amt
        g["paid_total"] += pay_amt
        g["items"].append(
            {
                "line_id": r["line_id"],
                "voucher_id": r["voucher_id"],
                "voucher_no": r["voucher_no"],
                "date": r["date"].isoformat(),
                "period": r["period"],
                "summary": r["summary"],
                "account_code": r["account_code"],
                "account_name": r["account_name"],
                "root": r["root"],
                "direction": "open" if open_amt else "pay",
                "open_amount": open_amt,
                "pay_amount": pay_amt,
                "settled": min(done, open_amt) if open_amt else 0,
                "remain": remain,
                "days": days,
                "band": _band_of(days, bands) if remain > 0 else None,
                "overdue": 1 if (remain > 0 and days > (partners[key].settle_days if partners.get(key) else 30)) else 0,
            }
        )
        if remain > 0:
            g["bands"][_band_of(days, bands)] += remain
            g["oldest_days"] = max(g["oldest_days"], days)
            g["overdue"] += remain if days > g["settle_days"] else 0

    out = []
    for g in groups.values():
        # 负债类（预收/应付）方向相反：余额 = 挂账 - 收回；资产类同式
        g["balance"] = g["open_total"] - g["paid_total"]
        if only_unsettled and g["balance"] == 0 and not any(i["remain"] for i in g["items"]):
            continue
        g["items"].sort(key=lambda x: (x["date"], x["voucher_no"]))
        g["bands"] = {str(k): v for k, v in sorted(g["bands"].items())}
        out.append(g)

    out.sort(key=lambda x: -abs(x["balance"]))
    return {
        "period": period,
        "as_of": end.isoformat(),
        "bands": bands,
        "rows": out,
        "total": {
            "open": sum(g["open_total"] for g in out),
            "paid": sum(g["paid_total"] for g in out),
            "balance": sum(g["balance"] for g in out),
            "overdue": sum(g["overdue"] for g in out),
        },
    }


def aging(db: Session, period: str, ptype: str | None = None):
    """账龄分析表：单位 × 账龄区间 的金额矩阵。"""
    data = ledger(db, period, ptype=ptype, only_unsettled=False)
    bands = data["bands"]
    rows = []
    for g in data["rows"]:
        if g["open_total"] == 0:
            continue
        row = {
            "name": g["name"],
            "ptype": g["ptype"],
            "partner_id": g["partner_id"],
            "open_total": g["open_total"],
            "paid_total": g["paid_total"],
            "balance": g["balance"],
            "overdue": g["overdue"],
            "oldest_days": g["oldest_days"],
            "cells": {str(b["seq"]): g["bands"].get(str(b["seq"]), 0) for b in bands},
        }
        rows.append(row)
    tot = {str(b["seq"]): sum(r["cells"][str(b["seq"])] for r in rows) for b in bands}
    return {
        "period": period,
        "as_of": data["as_of"],
        "bands": bands,
        "rows": rows,
        "totals": {
            "cells": tot,
            "balance": sum(r["balance"] for r in rows),
            "overdue": sum(r["overdue"] for r in rows),
        },
    }


def auto_settle(db: Session, period: str, ptype: str | None = None, partner_name: str | None = None) -> dict:
    """按 FIFO 自动核销。

    先清掉本范围内上一次的自动核销记录（is_auto=True），再从最早的挂账
    开始一笔笔被后面的收回冲抵。手工核销记录不受影响。
    """
    end = _period_end(period)
    rows = _load_lines(db, period)
    pmap = {(p.ptype, p.name): p.id for p in db.scalars(select(Partner)).all()}

    # 清掉旧的自动核销
    q = select(Settle)
    olds = [
        s
        for s in db.scalars(q).all()
        if s.is_auto and (partner_name is None or s.note.startswith(f"{partner_name}|"))
        and (ptype is None or s.note.endswith(f"|{ptype}"))
    ]
    for s in olds:
        db.delete(s)

    buckets: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        if ptype and r["aux_type"] != ptype:
            continue
        if partner_name and r["partner_name"] != partner_name:
            continue
        buckets[(r["partner_name"], r["aux_type"])].append(r)

    created, total_amt = 0, 0
    for (name, ptype_key), items in buckets.items():
        opens, pays = [], []
        for r in items:
            o, p = _split_open_pay(r)
            if o:
                opens.append([r, o, 0])
            if p:
                pays.append([r, p, 0])
        for pay in pays:
            left = pay[1] - pay[2]
            if left <= 0:
                continue
            for op in opens:
                if left <= 0:
                    break
                avail = op[1] - op[2]
                if avail <= 0:
                    continue
                amt = min(avail, left)
                db.add(
                    Settle(
                        partner_id=pmap.get((ptype_key, name)),
                        account_code=op[0]["root"],
                        open_line_id=op[0]["line_id"],
                        pay_line_id=pay[0]["line_id"],
                        amount=amt,
                        settle_date=pay[0]["date"],
                        is_auto=True,
                        note=f"{name}|{ptype_key}",
                    )
                )
                op[2] += amt
                pay[2] += amt
                left -= amt
                created += 1
                total_amt += amt

    db.commit()
    return {"created": created, "amount": total_amt, "as_of": end.isoformat()}


def statement(db: Session, partner_name: str, period_from: str, period_to: str):
    """往来对账单：区间内的发生额与余额，可直接发客户。"""
    rows = _load_lines(db, period_to)
    end = _period_end(period_to)
    items = [r for r in rows if r["partner_name"] == partner_name]
    items.sort(key=lambda x: (x["date"], x["voucher_no"]))

    opening = 0
    for r in items:
        if r["period"] < period_from:
            o, p = _split_open_pay(r)
            opening += o - p

    balance = opening
    lines, t_dr, t_cr = [], 0, 0
    for r in items:
        if r["period"] < period_from:
            continue
        o, p = _split_open_pay(r)
        debit, credit = r["debit"], r["credit"]
        # 统一按「对本单位债权增加」为正展示
        sign = 1 if PARTNER_ACCOUNTS[r["root"]][1] == "asset" else -1
        balance += (o - p) * sign
        t_dr += o
        t_cr += p
        lines.append(
            {
                "date": r["date"].isoformat(),
                "voucher_no": r["voucher_no"],
                "summary": r["summary"],
                "account_name": r["account_name"],
                "debit": debit,
                "credit": credit,
                "open_amount": o,
                "pay_amount": p,
                "balance": balance,
            }
        )

    return {
        "partner_name": partner_name,
        "period_from": period_from,
        "period_to": period_to,
        "as_of": end.isoformat(),
        "opening": opening,
        "lines": lines,
        "total_open": t_dr,
        "total_pay": t_cr,
        "closing": balance,
    }


def unassigned(db: Session, period: str):
    """挂了往来科目但没填往来单位的凭证行——账龄表会漏掉这些，必须提示。"""
    rows = _load_lines(db, period)
    have = {(r["voucher_id"], r["line_id"]) for r in rows}
    stmt = (
        select(VoucherLine, Voucher, Account)
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .join(Account, Account.id == VoucherLine.account_id)
        .where(Voucher.status == "posted", Voucher.period <= period)
        .order_by(Voucher.vdate)
    )
    out, total = [], 0
    for vl, v, acc in db.execute(stmt).all():
        root = _match_account(acc.code)
        if not root:
            continue
        if (v.id, vl.id) in have:
            continue
        if not (vl.debit or vl.credit):
            continue
        out.append(
            {
                "voucher_id": v.id,
                "voucher_no": f"{v.word}-{v.seq:04d}",
                "date": v.vdate.isoformat(),
                "account_name": acc.name,
                "summary": vl.summary,
                "amount": vl.debit or vl.credit,
            }
        )
        total += vl.debit or vl.credit
    return {"rows": out, "total": total}
