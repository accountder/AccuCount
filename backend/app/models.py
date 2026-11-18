"""数据模型。

设计要点：
- 科目为树形，用 code 点号分层（1002 / 1002.01）；只有叶子科目能做分录。
- 凭证状态机：draft（未审核）→ audited（已审核）→ posted（旧版已记账，兼容保留）；void 作废。
  余额与报表统计 audited 和 posted，审核即入账。
- 金额字段均为整数「分」。
"""
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# 已入账状态：正常流程到 audited 即进入账簿；posted 仅兼容旧版数据。
BOOKED_STATUSES = ("audited", "posted")


class Company(Base):
    __tablename__ = "company"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    tax_no: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(String(200), default="")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    standard: Mapped[str] = mapped_column(String(40), default="小企业会计准则")
    start_period: Mapped[str] = mapped_column(String(7), default="")  # 启用期间 YYYY-MM
    fiscal_start_month: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Period(Base):
    __tablename__ = "period"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(7), unique=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open / closed
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    cat: Mapped[str] = mapped_column(String(12))  # asset/liability/equity/cost/pl
    direction: Mapped[str] = mapped_column(String(2))  # dr / cr 余额方向
    parent_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=True)
    aux_types: Mapped[str] = mapped_column(String(60), default="")  # 允许的辅助核算类型，逗号分隔
    is_cash: Mapped[bool] = mapped_column(Boolean, default=False)  # 现金类，用于现金流量
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(200), default="")

    __table_args__ = (Index("ix_account_parent", "parent_id"),)


class AuxItem(Base):
    __tablename__ = "aux_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    aux_type: Mapped[str] = mapped_column(String(20))  # customer/supplier/dept/employee/project
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("aux_type", "code", name="uq_aux"),)


class Voucher(Base):
    __tablename__ = "voucher"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    vdate: Mapped[date] = mapped_column(Date, index=True)
    word: Mapped[str] = mapped_column(String(6), default="记")
    seq: Mapped[int] = mapped_column(Integer)
    attachments: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    maker: Mapped[str] = mapped_column(String(40), default="")
    auditor: Mapped[str] = mapped_column(String(40), default="")
    poster: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual/carryover/opening
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    lines: Mapped[list["VoucherLine"]] = relationship(
        back_populates="voucher", cascade="all, delete-orphan", order_by="VoucherLine.line_no"
    )

    __table_args__ = (UniqueConstraint("period", "word", "seq", name="uq_voucher_no"),)


class VoucherLine(Base):
    __tablename__ = "voucher_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(200), default="")
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    debit: Mapped[int] = mapped_column(Integer, default=0)
    credit: Mapped[int] = mapped_column(Integer, default=0)
    aux_type: Mapped[str] = mapped_column(String(20), default="")
    aux_name: Mapped[str] = mapped_column(String(120), default="")
    qty: Mapped[float] = mapped_column(Integer, default=0)  # 数量*1000 存的整数，0 表示未填
    price: Mapped[int] = mapped_column(Integer, default=0)  # 单价，分
    # 税率（万分比，1300 = 13%）。仅涉税行填，用于销项/进项按税率分档汇总。
    tax_rate: Mapped[int] = mapped_column(Integer, default=0)

    voucher: Mapped["Voucher"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship()

    __table_args__ = (Index("ix_line_account", "account_id"),)


class OpeningBatch(Base):
    """账套启用期初余额批次。"""

    __tablename__ = "opening_batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    start_period: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft/confirmed
    note: Mapped[str] = mapped_column(String(200), default="")
    opening_voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(40), default="")
    confirmed_by: Mapped[str] = mapped_column(String(40), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class OpeningLine(Base):
    """期初明细。金额均为分；opening_balance 按科目自然方向保存，可为负数。"""

    __tablename__ = "opening_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("opening_batch.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    aux_type: Mapped[str] = mapped_column(String(20), default="")
    aux_name: Mapped[str] = mapped_column(String(120), default="")
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    opening_balance: Mapped[int] = mapped_column(Integer, default=0)
    ytd_debit: Mapped[int] = mapped_column(Integer, default=0)
    ytd_credit: Mapped[int] = mapped_column(Integer, default=0)
    year_opening: Mapped[int] = mapped_column(Integer, default=0)
    actual_pl: Mapped[int] = mapped_column(Integer, default=0)
    remark: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(40), default="")
    role: Mapped[str] = mapped_column(String(20), default="admin")  # admin/accountant/viewer
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)


class ReportTemplate(Base):
    __tablename__ = "report_template"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)  # balance_sheet / profit
    name: Mapped[str] = mapped_column(String(60))
    definition: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    user: Mapped[str] = mapped_column(String(40), default="")
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(80), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


# ============================================================ 第二阶段：往来
class Partner(Base):
    """往来单位档案。客户与供应商同表，用 ptype 区分。

    凭证行通过 (aux_type, aux_name) 与之关联：aux_type='customer' 对应
    ptype='customer' 的档案，aux_name 存档案名称。这样无需给凭证行加外键，
    历史凭证（含从别处导入的）只要名称对得上就能归集。
    """

    __tablename__ = "partner"
    id: Mapped[int] = mapped_column(primary_key=True)
    ptype: Mapped[str] = mapped_column(String(10), index=True)  # customer / supplier
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120), index=True)
    short_name: Mapped[str] = mapped_column(String(60), default="")
    tax_no: Mapped[str] = mapped_column(String(40), default="")
    contact: Mapped[str] = mapped_column(String(40), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(String(200), default="")
    bank: Mapped[str] = mapped_column(String(120), default="")
    bank_account: Mapped[str] = mapped_column(String(60), default="")
    tax_rate: Mapped[int] = mapped_column(Integer, default=1300)  # 万分比，默认 13%
    settle_days: Mapped[int] = mapped_column(Integer, default=30)  # 账期天数，用于信用期分析
    credit_limit: Mapped[int] = mapped_column(Integer, default=0)  # 信用额度，分
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint("ptype", "name", name="uq_partner_name"),)


class Settle(Base):
    """往来核销单：把一笔挂账（应收借方/应付贷方）与一笔收回冲抵。

    open_line_id 是形成债权/债务的那一行（应收在借方、应付在贷方），
    pay_line_id 是收回或付款的那一行。一张收款凭证可能同时冲多笔挂账，
    因此按「行对行」记录，而非凭证对凭证。
    """

    __tablename__ = "settle"
    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partner.id"), nullable=True, index=True)
    account_code: Mapped[str] = mapped_column(String(20))  # 1122 / 2202 / 1123 / 2203
    open_line_id: Mapped[int] = mapped_column(ForeignKey("voucher_line.id"), index=True)
    pay_line_id: Mapped[int] = mapped_column(ForeignKey("voucher_line.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)  # 分
    settle_date: Mapped[date] = mapped_column(Date)
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)  # 由 FIFO 自动核销生成
    note: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (Index("ix_settle_open", "open_line_id"),)


# ============================================================ 第二阶段：固定资产
class Asset(Base):
    """固定资产卡片。

    金额为分；残值率、税率为万分比（500 = 5%）。
    dep_start_period 才是折旧起点：当月增加的资产当月不提、下月起提，
    建卡时按入账日期自动算好，也可手工改（比如以前年度购入的补录）。
    """

    __tablename__ = "asset"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(30), default="电子设备")
    dept: Mapped[str] = mapped_column(String(40), default="")
    location: Mapped[str] = mapped_column(String(80), default="")

    asset_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    dep_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))  # 累计折旧
    expense_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))  # 折旧费用归集

    original_value: Mapped[int] = mapped_column(Integer, default=0)  # 原值，分
    salvage_rate: Mapped[int] = mapped_column(Integer, default=500)  # 残值率，万分比
    useful_months: Mapped[int] = mapped_column(Integer, default=60)
    method: Mapped[str] = mapped_column(String(12), default="straight")  # straight/ddb/syoyds

    in_service_date: Mapped[date] = mapped_column(Date)  # 投入使用日期
    dep_start_period: Mapped[str] = mapped_column(String(7), index=True)
    accum_dep: Mapped[int] = mapped_column(Integer, default=0)  # 已提累计折旧，分
    voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id"), nullable=True)  # 入账凭证
    status: Mapped[str] = mapped_column(String(10), default="in_use")  # in_use/idle/disposed
    disposed_period: Mapped[str] = mapped_column(String(7), default="")
    dispose_income: Mapped[int] = mapped_column(Integer, default=0)  # 处置价款，分
    remark: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AssetDep(Base):
    """折旧计提明细，一个资产一个期间一条，避免重复计提。"""

    __tablename__ = "asset_dep"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("asset.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    accum_after: Mapped[int] = mapped_column(Integer, default=0)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (UniqueConstraint("asset_id", "period", name="uq_asset_period"),)


# ============================================================ 第二阶段：增值税
class VatLedger(Base):
    """增值税台账：每期一条，账面取数 + 手工调整。

    账面数由 calc.vat_book 实时汇总凭证得出，本表只存「人工确认的调整项」
    与确认状态。这样补录凭证后台账自动跟着变，不会两边打架。
    """

    __tablename__ = "vat_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True)
    # 手工调整项（分）。未开票收入对应的销项、简易计税、异地预缴等无法从凭证区分
    adjust_output_tax: Mapped[int] = mapped_column(Integer, default=0)
    adjust_input_tax: Mapped[int] = mapped_column(Integer, default=0)
    adjust_note: Mapped[str] = mapped_column(String(200), default="")
    paid_tax: Mapped[int] = mapped_column(Integer, default=0)  # 实际缴纳（与账面已交税金核对用）
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class SurtaxCalc(Base):
    """附加税测算与确认快照。

    确认时冻结计税依据、减免状态和税率，生成凭证只使用这份快照，
    避免确认后账簿变化导致凭证金额悄悄改变。
    """

    __tablename__ = "surtax_calc"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="transfer")
    source_account_code: Mapped[str] = mapped_column(String(20), default="2221.01.05")
    base_amount: Mapped[int] = mapped_column(Integer, default=0)
    small_micro: Mapped[bool] = mapped_column(Boolean, default=False)
    city_rate: Mapped[int] = mapped_column(Integer, default=700)
    education_rate: Mapped[int] = mapped_column(Integer, default=300)
    local_education_rate: Mapped[int] = mapped_column(Integer, default=200)
    reduction_rate: Mapped[int] = mapped_column(Integer, default=5000)
    gross_amount: Mapped[int] = mapped_column(Integer, default=0)
    reduction_amount: Mapped[int] = mapped_column(Integer, default=0)
    net_amount: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[str] = mapped_column(String(40), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class IncomeTaxCalc(Base):
    """季度企业所得税预提测算与确认快照。"""

    __tablename__ = "income_tax_calc"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    quarter: Mapped[int] = mapped_column(Integer)
    ytd_profit: Mapped[int] = mapped_column(Integer, default=0)
    prior_loss: Mapped[int] = mapped_column(Integer, default=0)
    taxable_income: Mapped[int] = mapped_column(Integer, default=0)
    employee_count: Mapped[int] = mapped_column(Integer, default=0)
    total_assets: Mapped[int] = mapped_column(Integer, default=0)
    industry_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    small_micro: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_rate: Mapped[int] = mapped_column(Integer, default=2500)  # 万分比
    cumulative_tax: Mapped[int] = mapped_column(Integer, default=0)
    previous_accrued: Mapped[int] = mapped_column(Integer, default=0)
    current_accrual: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[str] = mapped_column(String(40), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class AgingBand(Base):
    """账龄区间设置。默认 30/60/90/180/365/以上，可按公司口径改。"""

    __tablename__ = "aging_band"
    id: Mapped[int] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(30))
    days_from: Mapped[int] = mapped_column(Integer)  # 含
    days_to: Mapped[int] = mapped_column(Integer)  # 含，0 表示以上不限
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
