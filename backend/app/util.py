"""金额换算与会计期间工具。

金额在库内一律为整数「分」。
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def to_cents(value) -> int:
    """把 123.45 / '123.45' / Decimal 转成 12345 分。空值返回 0。"""
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value * 100
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(d * 100)


def from_cents(cents) -> float:
    if not cents:
        return 0.0
    return float(Decimal(int(cents)) / Decimal(100))


def money_str(cents) -> str:
    return f"{Decimal(int(cents or 0)) / Decimal(100):,.2f}"


def period_of(d) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return f"{d.year:04d}-{d.month:02d}"


def period_to_date(code: str, end: bool = True) -> date:
    y, m = int(code[:4]), int(code[5:7])
    if end:
        nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
        from datetime import timedelta

        return date(nm_y, nm_m, 1) - timedelta(days=1)
    return date(y, m, 1)


def period_add(code: str, n: int) -> str:
    y, m = int(code[:4]), int(code[5:7])
    total = y * 12 + (m - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def year_of(code: str) -> int:
    return int(code[:4])


def year_periods(year: int):
    return [f"{year:04d}-{m:02d}" for m in range(1, 13)]


def period_range(start: str, end: str):
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = period_add(cur, 1)
    return out
