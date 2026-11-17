"""固定资产折旧引擎。

规则（中国准则口径）：
- 当月增加的固定资产，当月不提折旧，下月起提；当月减少的，当月照提。
  所以折旧起点统一为「入账月份的次月」，建卡时自动算好。
- 应计折旧总额 = 原值 − 预计净残值（原值 × 残值率）。
- 分币误差的处理：逐期算完再归一化，最后一期吸收尾差，保证累计折旧
  最终正好等于应计折旧总额，不会出现「提了 60 期还差 3 分钱」。
- 提足后自动停提；停用、已处置的资产不再计提。
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, Asset, AssetDep, Voucher, VoucherLine
from .util import period_add


def depreciable_total(asset: Asset) -> int:
    """应计折旧总额（分）。"""
    salvage = round(asset.original_value * asset.salvage_rate / 10000)
    return max(asset.original_value - salvage, 0)


def _years(asset: Asset) -> float:
    return max(asset.useful_months / 12.0, 1e-6)


def raw_schedule(asset: Asset) -> list[float]:
    """按折旧方法给出每期（月）折旧的原始序列，长度 = 使用月数。"""
    total = depreciable_total(asset)
    n = max(asset.useful_months, 1)
    if total <= 0:
        return [0.0] * n

    if asset.method == "ddb":
        # 双倍余额递减：前期按净值双倍余额，最后两年改直线（准则要求）
        rate = 2.0 / _years(asset) / 12.0
        out, nv = [], float(asset.original_value)
        floor = asset.original_value - total  # 净残值
        straight_from = max(n - 24, 0)
        remain_months = n - straight_from
        for i in range(n):
            if i >= straight_from and remain_months > 0:
                dep = (nv - floor) / remain_months
            else:
                dep = nv * rate
                if nv - dep < floor:
                    dep = max(nv - floor, 0.0)
            dep = max(dep, 0.0)
            out.append(dep)
            nv -= dep
        return out

    if asset.method == "syoyds":
        # 年数总和法
        years = int(round(_years(asset)))
        years = max(years, 1)
        sum_y = years * (years + 1) / 2
        out = []
        for m in range(n):
            y = m // 12 + 1
            y = min(y, years)
            remain = years - y + 1
            out.append(total * remain / sum_y / 12.0)
        return out

    # 直线法（默认）
    return [total / n] * n


def schedule(asset: Asset) -> list[int]:
    """归一化后的每期折旧额（分），累计正好等于应计折旧总额。"""
    total = depreciable_total(asset)
    raw = raw_schedule(asset)
    if total <= 0 or not raw:
        return [0] * max(asset.useful_months, 1)

    s = sum(raw)
    if s <= 0:
        return [0] * len(raw)
    scaled = [x * total / s for x in raw]
    out = [int(round(x)) for x in scaled]
    diff = total - sum(out)
    out[-1] += diff
    return [max(x, 0) for x in out]


def periods_between(start_period: str, period: str) -> int:
    """从折旧起始期到目标期的序号（含起点为第 1 期）。"""
    ys, ms = int(start_period[:4]), int(start_period[5:7])
    yp, mp = int(period[:4]), int(period[5:7])
    return (yp * 12 + mp) - (ys * 12 + ms) + 1


def dep_of(asset: Asset, period: str) -> int:
    """某期的折旧额，0 表示不该提（未启用/已提足/已处置/已停用）。"""
    if asset.status != "in_use":
        return 0
    if period < asset.dep_start_period:
        return 0
    idx = periods_between(asset.dep_start_period, period) - 1
    sch = schedule(asset)
    if idx < 0 or idx >= len(sch):
        return 0
    return sch[idx]


def next_dep_start(in_service: date) -> str:
    """当月增加下月起提。"""
    return period_add(f"{in_service.year:04d}-{in_service.month:02d}", 1)


def preview(db: Session, period: str):
    """本期应提折旧预览：区分「本期计提」「已提过」「不该提」三种。"""
    assets = db.scalars(select(Asset).order_by(Asset.code)).all()
    done = {
        d.asset_id: d
        for d in db.scalars(select(AssetDep).where(AssetDep.period == period)).all()
    }
    rows, total, posted_total = [], 0, 0
    for a in assets:
        amt = dep_of(a, period)
        rec = done.get(a.id)
        reason = ""
        if a.status == "disposed":
            reason = "已处置"
        elif a.status == "idle":
            reason = "停用"
        elif period < a.dep_start_period:
            reason = f"{a.dep_start_period} 起提"
        elif amt == 0:
            reason = "已提足"
        if rec:
            reason = "本期已计提"
        rows.append(
            {
                "id": a.id,
                "code": a.code,
                "name": a.name,
                "category": a.category,
                "dept": a.dept,
                "original_value": a.original_value,
                "accum_dep": a.accum_dep,
                "net_value": a.original_value - a.accum_dep,
                "method": a.method,
                "useful_months": a.useful_months,
                "dep_start_period": a.dep_start_period,
                "amount": rec.amount if rec else amt,
                "posted": bool(rec),
                "voucher_id": rec.voucher_id if rec else None,
                "reason": reason,
            }
        )
        if rec:
            posted_total += rec.amount
        else:
            total += amt
    return {
        "period": period,
        "rows": rows,
        "total": total,
        "posted_total": posted_total,
        "count": sum(1 for r in rows if r["amount"] and not r["posted"]),
        "posted_count": sum(1 for r in rows if r["posted"]),
    }


def post_depreciation(db: Session, period: str, user: str, voucher_no_fn) -> dict:
    """生成计提折旧凭证并回写累计折旧。

    按（折旧费用科目, 累计折旧科目）分组汇总，凭证摘要列明资产笔数。
    明细留在 asset_dep，报表和明细账都能追回单项资产。
    """
    from .models import Period

    p = db.scalar(select(Period).where(Period.code == period))
    if not p:
        raise ValueError(f"会计期间 {period} 不存在")
    if p.status == "closed":
        raise ValueError(f"期间 {period} 已结账，不能计提折旧")

    done = {d.asset_id for d in db.scalars(select(AssetDep).where(AssetDep.period == period)).all()}
    assets = db.scalars(select(Asset).where(Asset.status == "in_use").order_by(Asset.code)).all()

    groups: dict[tuple[int, int], dict] = {}
    for a in assets:
        if a.id in done:
            continue
        amt = dep_of(a, period)
        if amt <= 0:
            continue
        # 折旧费用和累计折旧都必须是明细科目，否则分录记到上级科目上，
        # 余额表按前缀能取到、但按末级科目取不到，报表和明细账会不一致
        for aid, label in ((a.expense_account_id, "折旧费用"), (a.dep_account_id, "累计折旧")):
            acc = db.get(Account, aid)
            if acc is None:
                raise ValueError(f"资产「{a.name}」的{label}科目不存在")
            if not acc.is_leaf:
                raise ValueError(f"资产「{a.name}」的{label}科目「{acc.code} {acc.name}」不是明细科目，请先在资产卡片里改选")
        key = (a.expense_account_id, a.dep_account_id)
        g = groups.setdefault(key, {"amount": 0, "items": []})
        g["amount"] += amt
        g["items"].append(a)

    if not groups:
        raise ValueError("本期没有可计提折旧的资产")

    vdate = date.fromisoformat(period + "-01")
    v = Voucher(
        period=period,
        vdate=vdate,
        word="记",
        seq=voucher_no_fn(db, period, "记"),
        note=f"计提 {period} 固定资产折旧",
        maker=user,
        source="depreciation",
    )
    db.add(v)
    db.flush()

    line_no = 0
    total = 0
    for (exp_id, dep_id), g in groups.items():
        names = "、".join(a.name for a in g["items"][:3])
        more = f" 等 {len(g['items'])} 项" if len(g["items"]) > 3 else ""
        line_no += 1
        db.add(
            VoucherLine(
                voucher_id=v.id,
                line_no=line_no,
                summary=f"计提折旧：{names}{more}",
                account_id=exp_id,
                debit=g["amount"],
                credit=0,
            )
        )
        line_no += 1
        db.add(
            VoucherLine(
                voucher_id=v.id,
                line_no=line_no,
                summary=f"计提折旧：{names}{more}",
                account_id=dep_id,
                debit=0,
                credit=g["amount"],
            )
        )
        total += g["amount"]

    db.flush()
    for (exp_id, dep_id), g in groups.items():
        for a in g["items"]:
            amt = dep_of(a, period)
            a.accum_dep += amt
            db.add(
                AssetDep(
                    asset_id=a.id,
                    period=period,
                    amount=amt,
                    accum_after=a.accum_dep,
                    voucher_id=v.id,
                )
            )

    db.commit()
    return {
        "voucher_id": v.id,
        "voucher_no": f"{v.word}-{v.seq:04d}",
        "total": total,
        "count": sum(len(g["items"]) for g in groups.values()),
    }


def unpost_depreciation(db: Session, period: str) -> dict:
    """撤销本期折旧：删凭证、回滚累计折旧、删明细。"""
    recs = db.scalars(select(AssetDep).where(AssetDep.period == period)).all()
    if not recs:
        raise ValueError("本期没有折旧记录")
    vids = {r.voucher_id for r in recs if r.voucher_id}
    for vid in vids:
        v = db.get(Voucher, vid)
        if v and v.status == "posted":
            raise ValueError("折旧凭证已记账，请先反记账再撤销")
    for r in recs:
        a = db.get(Asset, r.asset_id)
        if a:
            a.accum_dep = max(a.accum_dep - r.amount, 0)
        db.delete(r)
    for vid in vids:
        v = db.get(Voucher, vid)
        if v:
            db.delete(v)
    db.commit()
    return {"removed": len(recs)}


def asset_summary(db: Session, period: str):
    """固定资产台账汇总：原值、累计折旧、净值，按类别分组。"""
    assets = db.scalars(select(Asset).order_by(Asset.code)).all()
    by_cat: dict[str, dict] = {}
    tot = {"original": 0, "accum": 0, "net": 0, "count": 0}
    for a in assets:
        if a.status == "disposed":
            continue
        c = by_cat.setdefault(a.category, {"category": a.category, "count": 0, "original": 0, "accum": 0, "net": 0})
        net = a.original_value - a.accum_dep
        c["count"] += 1
        c["original"] += a.original_value
        c["accum"] += a.accum_dep
        c["net"] += net
        tot["count"] += 1
        tot["original"] += a.original_value
        tot["accum"] += a.accum_dep
        tot["net"] += net
    return {"period": period, "by_category": list(by_cat.values()), "total": tot}


def resolve_accounts(db: Session, category: str):
    """按类别给出默认的三类科目 id（固定资产/累计折旧/折旧费用）。

    一定返回**明细科目**：只有在最末级科目上才能做分录，挂到 5602 管理费用
    这种有下级的科目上，凭证本身不报错，但科目余额表和报表都会对不上。
    找不到明细时退回一级科目并由建卡接口报错，不静默写错账。
    """
    mapping = {
        "房屋建筑物": ("1601.01", "1602.01", "5602.02"),
        "机器设备": ("1601.02", "1602.02", "5602.02"),
        "运输设备": ("1601.03", "1602.03", "5602.02"),
        "电子设备": ("1601.04", "1602.04", "5602.02"),
        "办公设备": ("1601.05", "1602.05", "5602.02"),
        "其他": ("1601.06", "1602.06", "5602.02"),
    }
    codes = mapping.get(category, ("1601", "1602", "5602.02"))

    def pick(code: str):
        acc = db.scalar(select(Account).where(Account.code == code))
        if acc is None:
            acc = db.scalar(select(Account).where(Account.code == code.split(".")[0]))
        if acc is not None and not acc.is_leaf:
            sub = db.scalar(
                select(Account)
                .where(Account.code.like(acc.code + ".%"), Account.is_leaf == True)  # noqa: E712
                .order_by(Account.code)
            )
            if sub is not None:
                return sub.id
        return acc.id if acc else None

    return [pick(c) for c in codes]
