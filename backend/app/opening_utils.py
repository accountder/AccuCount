"""期初余额的科目树口径。

父科目既支持由下级自动汇总，也支持手工覆盖。所有报表和期初凭证都必须
使用同一套口径：存在手工父科目时，以手工父科目为准，并跳过其下级，
避免父科目与下级明细重复计数。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import Account, OpeningLine


AMOUNT_FIELDS = ("opening_balance", "ytd_debit", "ytd_credit", "year_opening", "actual_pl")


@dataclass
class OpeningAmount:
    opening_balance: int = 0
    ytd_debit: int = 0
    ytd_credit: int = 0
    year_opening: int = 0
    actual_pl: int = 0

    def add(self, other: "OpeningAmount") -> None:
        for field in AMOUNT_FIELDS:
            setattr(self, field, getattr(self, field) + getattr(other, field))

    def as_dict(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in AMOUNT_FIELDS}


def opening_year_opening(account: Account, opening_balance: int,
                         ytd_debit: int, ytd_credit: int) -> int:
    """按科目自然方向倒算年初余额；损益类期初和年初均为零。"""
    if account.cat == "pl":
        return 0
    if account.direction == "dr":
        return opening_balance - ytd_debit + ytd_credit
    return opening_balance + ytd_debit - ytd_credit


def _line_amount(account: Account, line: OpeningLine) -> OpeningAmount:
    opening_balance = int(line.opening_balance or 0)
    ytd_debit = int(line.ytd_debit or 0)
    ytd_credit = int(line.ytd_credit or 0)
    return OpeningAmount(
        opening_balance=opening_balance,
        ytd_debit=ytd_debit,
        ytd_credit=ytd_credit,
        year_opening=opening_year_opening(account, opening_balance, ytd_debit, ytd_credit),
        actual_pl=int(line.actual_pl or 0),
    )


def _amount_from_lines(account: Account, lines: list[OpeningLine]) -> OpeningAmount:
    amount = OpeningAmount()
    for line in lines:
        amount.add(_line_amount(account, line))
    return amount


def build_opening_snapshot(accounts: list[Account], lines: list[OpeningLine]):
    """返回科目树的有效期初金额、下级合计和选取的期初分录行。

    - 末级科目：同一科目的辅助核算行相加；
    - 非末级自动科目：取直接下级有效金额之和；
    - 非末级手工科目：取手工行金额，并停止向下选取凭证分录；
    - 下级合计始终按直接下级计算，用于比较手工合计与明细合计。
    """
    account_by_id = {account.id: account for account in accounts}
    children: dict[int, list[Account]] = defaultdict(list)
    roots: list[Account] = []
    for account in accounts:
        if account.parent_id and account.parent_id in account_by_id:
            children[account.parent_id].append(account)
        else:
            roots.append(account)

    lines_by_account: dict[int, list[OpeningLine]] = defaultdict(list)
    for line in lines:
        if line.account_id in account_by_id:
            lines_by_account[line.account_id].append(line)

    resolved: dict[int, OpeningAmount] = {}
    child_totals: dict[int, OpeningAmount] = {}
    manual_flags: dict[int, bool] = {}
    manual_line_ids: dict[int, int | None] = {}
    visiting: set[int] = set()

    def resolve(account: Account) -> OpeningAmount:
        if account.id in resolved:
            return resolved[account.id]
        if account.id in visiting:
            # 数据异常时避免递归死循环，仍按当前科目数据返回。
            return _amount_from_lines(account, lines_by_account.get(account.id, []))
        visiting.add(account.id)

        child_total = OpeningAmount()
        for child in children.get(account.id, []):
            child_total.add(resolve(child))
        child_totals[account.id] = child_total

        own_lines = lines_by_account.get(account.id, [])
        manual_lines = [
            line for line in own_lines
            if bool(line.manual_override) and not account.is_leaf
        ]
        manual_flags[account.id] = bool(manual_lines)
        manual_line_ids[account.id] = manual_lines[0].id if manual_lines else None

        if manual_lines:
            amount = _amount_from_lines(account, manual_lines)
        elif account.is_leaf:
            amount = _amount_from_lines(account, own_lines)
        else:
            amount = OpeningAmount(
                opening_balance=child_total.opening_balance,
                ytd_debit=child_total.ytd_debit,
                ytd_credit=child_total.ytd_credit,
                year_opening=opening_year_opening(
                    account,
                    child_total.opening_balance,
                    child_total.ytd_debit,
                    child_total.ytd_credit,
                ),
                actual_pl=child_total.actual_pl,
            )
        resolved[account.id] = amount
        visiting.remove(account.id)
        return amount

    for root in roots:
        resolve(root)
    # 兼容 parent_id 指向已停用科目等异常树，保证所有启用科目都有快照。
    for account in accounts:
        resolve(account)

    selected_account_ids: set[int] = set()
    walked: set[int] = set()

    def skip_descendants(account: Account) -> None:
        for child in children.get(account.id, []):
            if child.id not in walked:
                walked.add(child.id)
                skip_descendants(child)

    def select(account: Account) -> None:
        if account.id in walked:
            return
        walked.add(account.id)
        if manual_flags.get(account.id):
            selected_account_ids.add(account.id)
            # 手工父科目已经截断整棵子树，后续遍历不能再把下级单独选入。
            skip_descendants(account)
            return
        if account.is_leaf:
            selected_account_ids.add(account.id)
            return
        for child in children.get(account.id, []):
            select(child)

    for root in roots:
        select(root)
    for account in accounts:
        select(account)

    selected_lines: list[OpeningLine] = []
    for account_id in selected_account_ids:
        own_lines = lines_by_account.get(account_id, [])
        if manual_flags.get(account_id):
            selected_lines.extend(line for line in own_lines if line.manual_override)
        else:
            selected_lines.extend(own_lines)

    return {
        "amounts": resolved,
        "child_totals": child_totals,
        "manual_flags": manual_flags,
        "manual_line_ids": manual_line_ids,
        "selected_lines": selected_lines,
    }


def selected_opening_lines(accounts: list[Account], lines: list[OpeningLine]) -> list[OpeningLine]:
    """选取应按期初业务参与计算的明细行，父科目手工覆盖时自动跳过下级。"""
    return build_opening_snapshot(accounts, lines)["selected_lines"]
