"""Excel（xlsx）导出。

页面上的报表和导出件必须是同一份数据、同一套口径，所以这里只做排版，
取数全部复用 calc，不另算一遍。行次、合计加粗、数字格式与页面保持一致。
"""
import calendar
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

THIN = Side(style="thin", color="D0D5DD")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="F2F4F7")
NUM_FMT = "#,##0.00"
TITLE_FONT = Font(size=16, bold=True)
HEAD_FONT = Font(size=11, bold=True)
BODY_FONT = Font(size=10.5)
MUTED_FONT = Font(size=10)


def _sheet(wb, name, widths, title):
    ws = wb.active
    ws.title = name
    ws.sheet_view.showGridLines = False
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ncol = len(widths)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    c = ws.cell(row=1, column=1, value=title)
    c.font = TITLE_FONT
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    return ws


def _meta(ws, ncol, company, period_label):
    """第二行：编制单位（左）· 期间（中）· 单位：元（右）。

    不合并单元格——4 列的表里合并中途，期间和「单位：元」会互相覆盖。
    """
    c = ws.cell(row=2, column=1, value=f"编制单位：{company}")
    c.font = MUTED_FONT
    c.alignment = Alignment(horizontal="left", vertical="center")

    mid = ncol // 2 if ncol >= 8 else max(3, ncol - 1)
    c = ws.cell(row=2, column=mid, value=period_label)
    c.font = MUTED_FONT
    c.alignment = Alignment(horizontal="center", vertical="center")

    c = ws.cell(row=2, column=ncol, value="单位：元")
    c.font = MUTED_FONT
    c.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[2].height = 20


def _header(ws, row, headers):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
        c.border = BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 24


def _cell(ws, row, col, value, *, align="left", bold=False, num=False, indent=0):
    if num and not value:
        value = None
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(size=10.5, bold=bold)
    c.border = BORDER
    if num:
        c.number_format = NUM_FMT
        c.alignment = Alignment(horizontal="right", vertical="center")
    else:
        c.alignment = Alignment(horizontal=align, vertical="center", indent=indent)
    return c


def _last_day(period: str) -> int:
    y, m = int(period[:4]), int(period[5:7])
    return calendar.monthrange(y, m)[1]


def _to_bytes(wb) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def balance_sheet(data, company: str, period: str) -> bytes:
    wb = Workbook()
    ws = _sheet(wb, "资产负债表", [34, 6, 15, 15, 34, 6, 15, 15], "资产负债表")
    _meta(ws, 8, company, f"{period[:4]}-{period[5:]}-{_last_day(period)}")
    _header(ws, 3, ["资产", "行次", "期末余额", "年初余额",
                    "负债和所有者权益", "行次", "期末余额", "年初余额"])

    left, right = data["left"], data["right"]
    n = max(len(left), len(right))
    for i in range(n):
        r = 4 + i
        L = left[i] if i < len(left) else None
        R = right[i] if i < len(right) else None
        if L:
            _cell(ws, r, 1, L["label"], bold=L["bold"] or L["top"], indent=(L["indent"] or 0))
            _cell(ws, r, 2, L["no"], align="center")
            _cell(ws, r, 3, L["value"], num=True, bold=L["bold"])
            _cell(ws, r, 4, L.get("value_begin"), num=True, bold=L["bold"])
        else:
            for col in (1, 2, 3, 4):
                _cell(ws, r, col, None)
        if R:
            _cell(ws, r, 5, R["label"], bold=R["bold"] or R["top"], indent=(R["indent"] or 0))
            _cell(ws, r, 6, R["no"], align="center")
            _cell(ws, r, 7, R["value"], num=True, bold=R["bold"])
            _cell(ws, r, 8, R.get("value_begin"), num=True, bold=R["bold"])
        else:
            for col in (5, 6, 7, 8):
                _cell(ws, r, col, None)

    ws.freeze_panes = "A4"
    return _to_bytes(wb)


def profit(data, company: str, period: str, period_from: str | None = None) -> bytes:
    wb = Workbook()
    ws = _sheet(wb, "利润表", [46, 7, 18, 18], "利润表")
    if period_from and period_from != period:
        q = (int(period[5:7]) - 1) // 3 + 1
        meta_txt = f"{period[:4]} 年第 {q} 季度（{int(period_from[5:7])}-{int(period[5:7])} 月）"
    else:
        meta_txt = f"{period[:4]} 年 {int(period[5:7])} 月"
    _meta(ws, 4, company, meta_txt)
    col_cur = "本季金额" if (period_from and period_from != period) else "本月金额"
    _header(ws, 3, ["项目", "行次", "本年累计金额", col_cur])

    for i, row in enumerate(data["rows"]):
        r = 4 + i
        _cell(ws, r, 1, row["label"], bold=row["bold"], indent=(row["indent"] or 0))
        _cell(ws, r, 2, row["no"], align="center")
        _cell(ws, r, 3, row.get("value_ytd"), num=True, bold=row["bold"])
        _cell(ws, r, 4, row["value"], num=True, bold=row["bold"])

    ws.freeze_panes = "A4"
    return _to_bytes(wb)


def trial_balance(data, company: str, period: str, period_from: str | None = None) -> bytes:
    wb = Workbook()
    ws = _sheet(wb, "科目余额表", [16, 32, 15, 15, 15, 15, 15, 15], "科目余额表")
    if period_from and period_from != period:
        meta_txt = f"会计期间：{period_from} 至 {period}"
    else:
        meta_txt = f"会计期间：{period}"
    _meta(ws, 8, company, meta_txt)
    _header(ws, 3, ["科目编码", "科目名称", "期初借方", "期初贷方",
                    "本期借方", "本期贷方", "期末借方", "期末贷方"])

    for i, row in enumerate(data["rows"]):
        r = 4 + i
        bold = row["level"] == 1
        _cell(ws, r, 1, row["code"], bold=bold, indent=max(0, row["level"] - 1))
        _cell(ws, r, 2, row["name"], bold=bold, indent=max(0, row["level"] - 1))
        for col, key in ((3, "open_dr"), (4, "open_cr"), (5, "cur_dr"),
                         (6, "cur_cr"), (7, "close_dr"), (8, "close_cr")):
            _cell(ws, r, col, row[key], num=True, bold=bold)

    t = data.get("totals") or {}
    r = 4 + len(data["rows"])
    _cell(ws, r, 1, "合计", bold=True)
    _cell(ws, r, 2, "", bold=True)
    for col, key in ((3, "open_dr"), (4, "open_cr"), (5, "cur_dr"),
                     (6, "cur_cr"), (7, "close_dr"), (8, "close_cr")):
        _cell(ws, r, col, t.get(key) or 0, num=True, bold=True)

    ws.freeze_panes = "A4"
    return _to_bytes(wb)

