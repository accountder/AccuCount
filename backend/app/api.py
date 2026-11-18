"""HTTP 接口。金额进出边界处换算：库里是分，接口是元。"""
import calendar
from datetime import date, datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from . import calc
from . import asset as asset_engine
from . import export as xlsx
from . import income_tax as income_tax_engine
from . import partner as partner_engine
from . import vat as vat_engine
from .opening_utils import build_opening_snapshot, opening_year_opening, selected_opening_lines
from .backup import create_backup_zip
from .auth import current_user, hash_password, make_token, require_role, verify_password
from .db import get_db
from .models import (
    BOOKED_STATUSES,
    Account,
    AgingBand,
    Asset,
    AssetDep,
    AuditLog,
    AuxItem,
    Company,
    IncomeTaxCalc,
    OpeningBatch,
    OpeningLine,
    Partner,
    Period,
    ReportTemplate,
    Settle,
    SurtaxCalc,
    User,
    VatLedger,
    Voucher,
    VoucherLine,
)
from .util import from_cents, period_add, period_of, to_cents
from .seed import ensure_periods

ROLE_LABEL = {"admin": "管理员", "accountant": "会计", "viewer": "只读"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def api_access_guard(request: Request):
    """集中权限守卫：登录接口放行，写接口仅允许管理员/会计。

    只读用户仍可修改自己的密码；其余写请求统一 403。这样以后新增写接口
    即使忘记单独声明角色，也不会意外对只读账号开放。
    """
    path = request.url.path.rstrip("/") or "/"
    if request.method == "POST" and path == "/api/login":
        return
    user = current_user(request.headers.get("authorization"))
    if request.method in WRITE_METHODS and user["role"] == "viewer" and path != "/api/me/password":
        raise HTTPException(403, "当前账号为只读角色，不能执行此操作")
    return user


router = APIRouter(prefix="/api", dependencies=[Depends(api_access_guard)])


# ------------------------------------------------------------------ 工具
def log(db, user, action, target="", detail=""):
    db.add(AuditLog(user=user.get("username", ""), action=action, target=str(target), detail=detail))


def user_out(u: User):
    return {
        "id": u.id,
        "username": u.username,
        "name": u.name,
        "role": u.role,
        "role_label": ROLE_LABEL.get(u.role, u.role),
        "enabled": bool(u.enabled),
    }


def active_admin_count(db, exclude_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(User.role == "admin", User.enabled.is_(True))
    if exclude_id:
        stmt = stmt.where(User.id != exclude_id)
    return int(db.scalar(stmt) or 0)


def acc_brief(a: Account):
    return {"id": a.id, "code": a.code, "name": a.name, "cat": a.cat,
            "direction": a.direction, "is_leaf": a.is_leaf, "level": a.level,
            "aux_types": a.aux_types, "enabled": a.enabled, "is_cash": a.is_cash,
            "parent_id": a.parent_id, "remark": a.remark}


def line_out(vl: VoucherLine, acc: Account | None):
    return {
        "id": vl.id,
        "line_no": vl.line_no,
        "summary": vl.summary,
        "account_id": vl.account_id,
        "account_code": acc.code if acc else "",
        "account_name": acc.name if acc else "",
        "debit": from_cents(vl.debit),
        "credit": from_cents(vl.credit),
        "aux_type": vl.aux_type,
        "aux_name": vl.aux_name,
        "qty": (vl.qty or 0) / 1000,
        "price": from_cents(vl.price),
        "tax_rate": vl.tax_rate or 0,
    }


def voucher_out(db, v: Voucher, with_lines=True, brief=None):
    data = {
        "id": v.id,
        "period": v.period,
        "date": v.vdate.isoformat(),
        "word": v.word,
        "seq": v.seq,
        "no": f"{v.word}-{v.seq:04d}",
        "attachments": v.attachments,
        "status": v.status,
        "note": v.note,
        "maker": v.maker,
        "auditor": v.auditor,
        "poster": v.poster,
        "source": v.source,
        "summary": "",
        "amount": 0.0,
    }
    if with_lines:
        accs = {a.id: a for a in db.scalars(select(Account)).all()}
        data["lines"] = [line_out(l, accs.get(l.account_id)) for l in v.lines]
        data["debit_total"] = from_cents(sum(l.debit for l in v.lines))
        data["credit_total"] = from_cents(sum(l.credit for l in v.lines))
        if v.lines:
            data["summary"] = v.lines[0].summary
            data["amount"] = max(data["debit_total"], data["credit_total"])
    elif brief:
        # 列表页不加载分录，摘要与金额由调用方一次性聚合后传入，避免 N+1
        data["summary"] = brief.get("summary", "")
        data["amount"] = from_cents(brief.get("amount", 0))
    return data


def get_template(db, code):
    t = db.scalar(select(ReportTemplate).where(ReportTemplate.code == code))
    if not t:
        raise HTTPException(500, f"报表模板 {code} 缺失")
    return t.definition


def check_period_open(db, period: str):
    p = db.scalar(select(Period).where(Period.code == period))
    if not p:
        raise HTTPException(400, f"会计期间 {period} 不存在")
    if p.status == "closed":
        raise HTTPException(
            400,
            f"期间 {period} 已结账，不能再录入或修改凭证。"
            f"如需调整请先到「期末处理」反结账；否则请把凭证记到未结账的期间。",
        )


# ------------------------------------------------------------------ 登录
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, db=Depends(get_db)):
    u = db.scalar(select(User).where(User.username == body.username))
    if not u or not u.enabled or not verify_password(body.password, u.password_hash, u.salt):
        raise HTTPException(401, "用户名或密码错误")
    log(db, {"username": u.username}, "login", u.username)
    db.commit()
    return {"token": make_token(u.id, u.token_version), "user": user_out(u)}


@router.get("/me")
def me(user=Depends(current_user)):
    return user


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/me/password")
def change_my_password(body: PasswordChangeIn, db=Depends(get_db), user=Depends(current_user)):
    u = db.get(User, user["id"])
    if not u or not verify_password(body.old_password, u.password_hash, u.salt):
        raise HTTPException(400, "原密码不正确")
    if body.old_password == body.new_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    u.password_hash, u.salt = hash_password(body.new_password)
    u.token_version = int(u.token_version or 0) + 1
    log(db, user, "user.password", u.username)
    db.commit()
    return {"ok": True, "token": make_token(u.id, u.token_version), "user": user_out(u)}


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=40)
    role: Literal["admin", "accountant", "viewer"] = "accountant"
    enabled: bool = True


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    role: Literal["admin", "accountant", "viewer"] | None = None
    enabled: bool | None = None


class UserResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


@router.get("/users")
def list_users(db=Depends(get_db), user=Depends(require_role("admin"))):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [user_out(u) for u in rows]


@router.post("/users")
def create_user(body: UserCreateIn, db=Depends(get_db), user=Depends(require_role("admin"))):
    username = body.username.strip()
    if len(username) < 3:
        raise HTTPException(400, "用户名至少 3 个字符")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(400, "用户名已存在")
    pw, salt = hash_password(body.password)
    u = User(
        username=username,
        password_hash=pw,
        salt=salt,
        name=(body.name or "").strip(),
        role=body.role,
        enabled=body.enabled,
        token_version=0,
    )
    db.add(u)
    db.flush()
    log(db, user, "user.create", u.username, f"role={u.role} enabled={u.enabled}")
    db.commit()
    return user_out(u)


@router.put("/users/{uid}")
def update_user(uid: int, body: UserUpdateIn, db=Depends(get_db), user=Depends(require_role("admin"))):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "账号不存在")
    new_role = body.role if body.role is not None else u.role
    new_enabled = body.enabled if body.enabled is not None else bool(u.enabled)
    if uid == user["id"] and (new_role != "admin" or not new_enabled):
        raise HTTPException(400, "不能停用或降级当前登录的管理员账号")
    if u.role == "admin" and u.enabled and (new_role != "admin" or not new_enabled):
        if active_admin_count(db, exclude_id=uid) == 0:
            raise HTTPException(400, "系统至少需要保留一个启用的管理员账号")
    role_changed = new_role != u.role
    enabled_changed = new_enabled != bool(u.enabled)
    if body.name is not None:
        u.name = body.name.strip()
    u.role = new_role
    u.enabled = new_enabled
    if role_changed or enabled_changed:
        u.token_version = int(u.token_version or 0) + 1
    log(db, user, "user.update", u.username, f"role={u.role} enabled={u.enabled}")
    db.commit()
    return user_out(u)


@router.post("/users/{uid}/reset-password")
def reset_user_password(uid: int, body: UserResetPasswordIn,
                        db=Depends(get_db), user=Depends(require_role("admin"))):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "账号不存在")
    if uid == user["id"]:
        raise HTTPException(400, "修改自己的密码请使用右上角「修改密码」")
    u.password_hash, u.salt = hash_password(body.new_password)
    u.token_version = int(u.token_version or 0) + 1
    log(db, user, "user.reset_password", u.username)
    db.commit()
    return {"ok": True}


@router.get("/audit-logs")
def list_audit_logs(page: int = 1, size: int = 100, q: str | None = None,
                    action: str | None = None,
                    db=Depends(get_db), user=Depends(require_role("admin"))):
    size = max(1, min(int(size), 300))
    stmt = select(AuditLog)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.user.like(like), AuditLog.target.like(like), AuditLog.detail.like(like)))
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.scalars(stmt.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).offset((page - 1) * size).limit(size)).all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "rows": [
            {
                "id": r.id,
                "time": r.ts.isoformat(timespec="seconds"),
                "user": r.user,
                "action": r.action,
                "target": r.target,
                "detail": r.detail,
            }
            for r in rows
        ],
    }


@router.get("/admin/backup")
def download_backup(user=Depends(require_role("admin"))):
    try:
        content = create_backup_zip()
    except Exception as exc:
        raise HTTPException(400, f"生成备份失败：{exc}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=accucount-backup-{stamp}.zip"},
    )


# ------------------------------------------------------------------ 基础信息
@router.get("/meta")
def meta(db=Depends(get_db), user=Depends(current_user)):
    comp = db.scalar(select(Company).limit(1))
    periods = db.scalars(select(Period).order_by(Period.code)).all()
    closed = [p.code for p in periods if p.status == "closed"]
    opens = [p.code for p in periods if p.status == "open"]
    # 当前可记账期间 = 最晚已结账期间之后第一个未结账期间。
    # 前端新增凭证、复制凭证都落在这里，避免用户选错期间被后端拦。
    if closed:
        last_closed = max(closed)
        cand = [c for c in opens if c > last_closed]
        open_period = cand[0] if cand else (opens[-1] if opens else None)
    else:
        last_closed = None
        open_period = opens[0] if opens else None
    return {
        "company": {
            "id": comp.id, "name": comp.name, "tax_no": comp.tax_no,
            "address": comp.address, "currency": comp.currency,
            "standard": comp.standard, "start_period": comp.start_period,
        } if comp else None,
        "periods": [{"code": p.code, "status": p.status,
                     "closed_at": p.closed_at.isoformat() if p.closed_at else None} for p in periods],
        "open_period": open_period,
        "last_closed_period": last_closed,
        "today": date.today().isoformat(),
    }


@router.put("/company")
def update_company(payload: dict = Body(...), db=Depends(get_db), user=Depends(require_role("admin"))):
    comp = db.scalar(select(Company).limit(1))
    if not comp:
        raise HTTPException(400, "尚未建账")
    for k in ("name", "tax_no", "address", "currency", "standard"):
        if k in payload:
            setattr(comp, k, payload[k])
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 科目
@router.get("/accounts")
def list_accounts(only_enabled: bool = False, db=Depends(get_db), user=Depends(current_user)):
    stmt = select(Account).order_by(Account.code)
    if only_enabled:
        stmt = stmt.where(Account.enabled.is_(True))
    return [acc_brief(a) for a in db.scalars(stmt).all()]


@router.post("/accounts")
def create_account(payload: dict = Body(...), db=Depends(get_db), user=Depends(current_user)):
    code = (payload.get("code") or "").strip()
    name = (payload.get("name") or "").strip()
    cat = payload.get("cat")
    direction = payload.get("direction")
    if not code or not name:
        raise HTTPException(400, "编码和名称不能为空")
    if db.scalar(select(Account).where(Account.code == code)):
        raise HTTPException(400, f"科目编码 {code} 已存在")
    if cat not in ("asset", "liability", "equity", "cost", "pl"):
        raise HTTPException(400, "科目类别不合法")
    if direction not in ("dr", "cr"):
        raise HTTPException(400, "余额方向不合法")

    parent = None
    if "." in code:
        pcode = code.rsplit(".", 1)[0]
        parent = db.scalar(select(Account).where(Account.code == pcode))
        if not parent:
            raise HTTPException(400, f"上级科目 {pcode} 不存在，请先建上级")

    a = Account(code=code, name=name, cat=cat, direction=direction,
                parent_id=parent.id if parent else None,
                level=code.count(".") + 1,
                aux_types=payload.get("aux_types") or "",
                is_cash=bool(payload.get("is_cash")),
                remark=payload.get("remark") or "")
    db.add(a)
    if parent:
        parent.is_leaf = False
    db.flush()
    log(db, user, "account.create", code, name)
    db.commit()
    return acc_brief(a)


QUICK_ACCOUNT_ROOTS = {
    "1121": "customer",
    "1122": "customer",
    "1123": "supplier",
    "2201": "supplier",
    "2202": "supplier",
    "2203": "customer",
}


class QuickChildAccountIn(BaseModel):
    parent_code: str
    name: str = Field(min_length=1, max_length=120)
    tax_rate: int = 1300
    remark: str = Field(default="", max_length=200)


@router.post("/accounts/quick-child")
def create_quick_child_account(body: QuickChildAccountIn,
                              db=Depends(get_db), user=Depends(current_user)):
    """在凭证录入时快速新增客户 / 供应商明细科目。

    - 自动生成下一号明细科目，如 1122.01、1122.02
    - 自动创建或复用对应往来档案
    - 继承父科目的类别、方向和辅助核算类型
    """
    parent_code = body.parent_code.strip()
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if parent_code not in QUICK_ACCOUNT_ROOTS:
        raise HTTPException(400, "仅支持客户 / 供应商类往来科目")

    parent = db.scalar(select(Account).where(Account.code == parent_code))
    if not parent:
        raise HTTPException(400, f"上级科目 {parent_code} 不存在")
    if db.scalar(select(Account).where(Account.parent_id == parent.id, Account.name == name)):
        raise HTTPException(400, f"“{name}”已存在于 {parent.code} {parent.name} 下")

    children = db.scalars(select(Account).where(Account.parent_id == parent.id)).all()
    nums = []
    for c in children:
        suffix = c.code.rsplit(".", 1)[-1]
        if suffix.isdigit():
            nums.append(int(suffix))
    code = f"{parent.code}.{(max(nums) + 1) if nums else 1:02d}"

    ptype = QUICK_ACCOUNT_ROOTS[parent_code]
    partner = db.scalar(select(Partner).where(Partner.ptype == ptype, Partner.name == name))
    if partner:
        partner.enabled = True
        if body.tax_rate:
            partner.tax_rate = body.tax_rate
    else:
        rows = db.scalars(select(Partner).where(Partner.ptype == ptype)).all()
        prefix = "C" if ptype == "customer" else "S"
        nums2 = []
        for p in rows:
            if p.code.startswith(prefix) and p.code[1:].isdigit():
                nums2.append(int(p.code[1:]))
        pcode = f"{prefix}{(max(nums2) + 1) if nums2 else 1:03d}"
        partner = Partner(
            ptype=ptype,
            code=pcode,
            name=name,
            tax_rate=body.tax_rate,
            enabled=True,
            remark=body.remark,
        )
        db.add(partner)
        db.flush()

    a = Account(
        code=code,
        name=name,
        cat=parent.cat,
        direction=parent.direction,
        parent_id=parent.id,
        level=code.count(".") + 1,
        aux_types=ptype,
        enabled=True,
        remark=body.remark,
    )
    db.add(a)
    parent.is_leaf = False
    db.flush()
    log(db, user, "account.quick_create", a.code, f"{parent.code} {parent.name} -> {name}")
    db.commit()
    return {"account": acc_brief(a), "partner": partner_out(partner)}


@router.put("/accounts/{acc_id}")
def update_account(acc_id: int, payload: dict = Body(...), db=Depends(get_db), user=Depends(current_user)):
    a = db.get(Account, acc_id)
    if not a:
        raise HTTPException(404, "科目不存在")
    used = db.scalar(select(func.count()).select_from(VoucherLine).where(VoucherLine.account_id == acc_id)) or 0
    if "code" in payload and payload["code"] != a.code:
        if used:
            raise HTTPException(400, "科目已有分录，不能修改编码，只能改名")
        if db.scalar(select(Account).where(Account.code == payload["code"])):
            raise HTTPException(400, "新编码已存在")
        a.code = payload["code"]
        a.level = a.code.count(".") + 1
    for k in ("name", "aux_types", "remark"):
        if k in payload:
            setattr(a, k, payload[k] or "")
    if "enabled" in payload:
        a.enabled = bool(payload["enabled"])
    if "is_cash" in payload:
        a.is_cash = bool(payload["is_cash"])
    log(db, user, "account.update", a.code, a.name)
    db.commit()
    return acc_brief(a)


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: int, db=Depends(get_db), user=Depends(current_user)):
    a = db.get(Account, acc_id)
    if not a:
        raise HTTPException(404, "科目不存在")
    if db.scalar(select(func.count()).select_from(VoucherLine).where(VoucherLine.account_id == acc_id)):
        raise HTTPException(400, "该科目已有分录，不能删除，可改为停用")
    if db.scalar(select(func.count()).select_from(Account).where(Account.parent_id == acc_id)):
        raise HTTPException(400, "该科目有下级，不能删除")
    parent_id = a.parent_id
    db.delete(a)
    if parent_id:
        p = db.get(Account, parent_id)
        if p and not db.scalar(select(func.count()).select_from(Account).where(Account.parent_id == parent_id)):
            p.is_leaf = True
    log(db, user, "account.delete", a.code, a.name)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 辅助核算
@router.get("/aux")
def list_aux(aux_type: str | None = None, db=Depends(get_db), user=Depends(current_user)):
    stmt = select(AuxItem).where(AuxItem.enabled.is_(True)).order_by(AuxItem.aux_type, AuxItem.code)
    if aux_type:
        stmt = stmt.where(AuxItem.aux_type == aux_type)
    return [{"id": a.id, "type": a.aux_type, "code": a.code, "name": a.name} for a in db.scalars(stmt).all()]


@router.post("/aux")
def create_aux(payload: dict = Body(...), db=Depends(get_db), user=Depends(current_user)):
    t = payload.get("aux_type")
    code = (payload.get("code") or "").strip()
    name = (payload.get("name") or "").strip()
    if t not in ("customer", "supplier", "dept", "employee", "project"):
        raise HTTPException(400, "辅助核算类型不合法")
    if not code or not name:
        raise HTTPException(400, "编码和名称不能为空")
    if db.scalar(select(AuxItem).where(AuxItem.aux_type == t, AuxItem.code == code)):
        raise HTTPException(400, "编码已存在")
    a = AuxItem(aux_type=t, code=code, name=name)
    db.add(a)
    db.commit()
    return {"id": a.id, "type": a.aux_type, "code": a.code, "name": a.name}


# ------------------------------------------------------------------ 凭证
class LineIn(BaseModel):
    summary: str = ""
    account_id: int
    debit: float = 0
    credit: float = 0
    aux_type: str = ""
    aux_name: str = ""
    qty: float = 0
    price: float = 0
    tax_rate: int = 0  # 万分比，1300 = 13%


class VoucherIn(BaseModel):
    date: str
    word: str = "记"
    attachments: int = 0
    note: str = ""
    lines: list[LineIn] = Field(default_factory=list)


def _next_seq(db, period: str, word: str) -> int:
    m = db.scalar(select(func.max(Voucher.seq)).where(Voucher.period == period, Voucher.word == word))
    return int(m or 0) + 1


def _apply_lines(db, v: Voucher, lines: list[LineIn]):
    accs = {a.id: a for a in db.scalars(select(Account)).all()}
    payload = []
    for i, l in enumerate(lines, 1):
        a = accs.get(l.account_id)
        if not a:
            raise HTTPException(400, f"第 {i} 行科目不存在")
        if not a.enabled:
            raise HTTPException(400, f"第 {i} 行科目「{a.name}」已停用")
        if not a.is_leaf:
            raise HTTPException(400, f"第 {i} 行科目「{a.code} {a.name}」是上级科目，请选明细科目")
        payload.append({
            "account_id": l.account_id,
            "debit": to_cents(l.debit),
            "credit": to_cents(l.credit),
        })

    ok, msg = calc.validate_voucher_lines(payload)
    if not ok:
        raise HTTPException(400, msg)

    v.lines.clear()
    db.flush()
    for i, (l, p) in enumerate(zip(lines, payload), 1):
        v.lines.append(VoucherLine(
            line_no=i,
            summary=(l.summary or "")[:200],
            account_id=p["account_id"],
            debit=p["debit"],
            credit=p["credit"],
            aux_type=l.aux_type or "",
            aux_name=(l.aux_name or "")[:120],
            qty=int(round((l.qty or 0) * 1000)),
            price=to_cents(l.price),
            tax_rate=int(l.tax_rate or 0),
        ))


@router.get("/vouchers")
def list_vouchers(period: str | None = None, period_from: str | None = None,
                  period_to: str | None = None, status: str | None = None,
                  q: str | None = None, page: int = 1, size: int = 50,
                  with_lines: bool = False, order: Literal["asc", "desc"] = "asc",
                  db=Depends(get_db), user=Depends(current_user)):
    # 默认按「期间升序 + 凭证号升序」，与纸质凭证本一致：记-0001 在最上面。
    # 首页「最近凭证」需要反着来，传 order=desc。
    if order == "desc":
        stmt = select(Voucher).order_by(Voucher.period.desc(), Voucher.seq.desc())
    else:
        stmt = select(Voucher).order_by(Voucher.period.asc(), Voucher.seq.asc())
    if period:
        stmt = stmt.where(Voucher.period == period)
    if period_from:
        stmt = stmt.where(Voucher.period >= period_from)
    if period_to:
        stmt = stmt.where(Voucher.period <= period_to)
    if status:
        stmt = stmt.where(Voucher.status == status)
    if q:
        sub = select(VoucherLine.voucher_id).where(
            or_(VoucherLine.summary.like(f"%{q}%"), VoucherLine.aux_name.like(f"%{q}%"))
        )
        stmt = stmt.where(Voucher.id.in_(sub))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.offset((page - 1) * size).limit(size)).all()

    # 列表不逐张查分录，一次把这页所有分录取回来聚合摘要与金额
    brief: dict[int, dict] = {}
    ids = [v.id for v in rows]
    line_map: dict[int, list] = {}
    accs = {a.id: a for a in db.scalars(select(Account)).all()} if with_lines else {}
    if ids:
        for l in db.scalars(
            select(VoucherLine)
            .where(VoucherLine.voucher_id.in_(ids))
            .order_by(VoucherLine.voucher_id, VoucherLine.line_no)
        ).all():
            b = brief.setdefault(l.voucher_id, {"summary": "", "amount": 0})
            if not b["summary"] and l.summary:
                b["summary"] = l.summary
            b["amount"] += l.debit
            if with_lines:
                line_map.setdefault(l.voucher_id, []).append(line_out(l, accs.get(l.account_id)))

    out = []
    for v in rows:
        item = voucher_out(db, v, with_lines=False, brief=brief.get(v.id))
        if with_lines:
            item["lines"] = line_map.get(v.id, [])
        out.append(item)

    return {"total": total, "page": page, "size": size, "rows": out}


@router.get("/vouchers/{vid}")
def get_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    return voucher_out(db, v)


@router.post("/vouchers")
def create_voucher(body: VoucherIn, insert_after: int | None = Query(None, description="插入到这张凭证之后"),
                   db=Depends(get_db), user=Depends(current_user)):
    period = period_of(body.date)
    check_period_open(db, period)
    word = body.word or "记"

    if insert_after:
        anchor = db.get(Voucher, insert_after)
        if not anchor:
            raise HTTPException(404, "插入位置所在的凭证不存在")
        if anchor.period != period or anchor.word != word:
            raise HTTPException(400, f"插入位置是 {anchor.period} 的 {anchor.word} 字凭证，与本期次不一致")
        seq = anchor.seq + 1
        # 凭证号顺移：先把 anchor 之后的整体挪开一个足够大的偏移，再回落到 +1 的位置，
        # 避免 (period, word, seq) 唯一约束在逐行更新时互相撞车
        base = select(Voucher.id).where(
            Voucher.period == period, Voucher.word == word, Voucher.seq >= seq
        )
        ids_to_shift = list(db.scalars(base).all())
        if ids_to_shift:
            OFF = 100000
            db.execute(
                update(Voucher).where(Voucher.id.in_(ids_to_shift)).values(seq=Voucher.seq + OFF),
                execution_options={"synchronize_session": False},
            )
            db.execute(
                update(Voucher).where(Voucher.id.in_(ids_to_shift)).values(seq=Voucher.seq - OFF + 1),
                execution_options={"synchronize_session": False},
            )
            db.expire_all()
    else:
        seq = _next_seq(db, period, word)

    v = Voucher(period=period, vdate=date.fromisoformat(body.date), word=word,
                seq=seq,
                attachments=body.attachments, note=body.note, maker=user["username"],
                status="draft", source="manual")
    db.add(v)
    db.flush()
    _apply_lines(db, v, body.lines)
    log(db, user, "voucher.create", v.id, f"{v.word}-{v.seq:04d} {body.date}")
    db.commit()
    return voucher_out(db, v)


@router.put("/vouchers/{vid}")
def update_voucher(vid: int, body: VoucherIn, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status != "draft":
        raise HTTPException(400, "只有未审核凭证可以修改，请先反审核")
    period = period_of(body.date)
    check_period_open(db, period)
    if period != v.period:
        check_period_open(db, period)
        v.period = period
        v.seq = _next_seq(db, period, v.word)
    v.vdate = date.fromisoformat(body.date)
    v.word = body.word or "记"
    v.attachments = body.attachments
    v.note = body.note
    _apply_lines(db, v, body.lines)
    log(db, user, "voucher.update", v.id, f"{v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


class BatchIn(BaseModel):
    ids: list[int]
    action: Literal["audit", "delete", "copy"]
    target_period: str | None = None  # action=copy 时的目标期间


@router.post("/vouchers/batch")
def batch_vouchers(body: BatchIn, db=Depends(get_db), user=Depends(current_user)):
    """批量审核 / 批量删除 / 批量复制。逐张判断，不能处理的收集原因返回，不做全 or 全无。"""
    label = {"draft": "未审核", "audited": "已审核", "posted": "已记账", "void": "已作废"}
    done, skipped = [], []

    # 批量复制先校验目标期间，避免复制一半才发现期间已结账
    if body.action == "copy":
        if not body.target_period:
            raise HTTPException(400, "请先选择要复制到的会计期间")
        target = db.scalar(select(Period).where(Period.code == body.target_period))
        if not target:
            raise HTTPException(400, f"会计期间 {body.target_period} 不存在")
        if target.status == "closed":
            raise HTTPException(400, f"{body.target_period} 已结账，不能复制进去")
        ty, tm = int(body.target_period[:4]), int(body.target_period[5:7])
        last_day = calendar.monthrange(ty, tm)[1]

    for vid in body.ids:
        v = db.get(Voucher, vid)
        if not v:
            skipped.append({"id": vid, "no": "", "reason": "凭证不存在"})
            continue
        no = f"{v.word}-{v.seq:04d}"
        p = db.scalar(select(Period).where(Period.code == v.period))
        if p and p.status == "closed" and body.action != "copy":
            skipped.append({"id": vid, "no": no, "reason": f"{v.period} 已结账"})
            continue

        if body.action == "audit":
            if v.status != "draft":
                skipped.append({"id": vid, "no": no, "reason": f"已是{label.get(v.status, v.status)}"})
                continue
            v.status = "audited"
            v.auditor = user["username"]
            done.append(no)

        elif body.action == "delete":
            if v.status not in ("draft", "void"):
                skipped.append({"id": vid, "no": no, "reason": f"{label.get(v.status, v.status)}的不能删除"})
                continue
            db.delete(v)
            done.append(no)

        else:  # copy
            # 保留原日期的「日」，月份换成目标期间；遇到 2 月 30 日这种就取月末
            td = date(ty, tm, min(v.vdate.day, last_day))
            nv = Voucher(
                period=body.target_period, vdate=td, word=v.word,
                seq=_next_seq(db, body.target_period, v.word),
                attachments=v.attachments, note=v.note,
                maker=user["username"], status="draft", source="copy",
            )
            db.add(nv)
            db.flush()
            for i, l in enumerate(v.lines, 1):
                db.add(VoucherLine(
                    voucher_id=nv.id, line_no=i, summary=l.summary, account_id=l.account_id,
                    debit=l.debit, credit=l.credit, aux_type=l.aux_type, aux_name=l.aux_name,
                    qty=l.qty, price=l.price, tax_rate=l.tax_rate,
                ))
            db.flush()
            done.append(f"{nv.word}-{nv.seq:04d}")

    log(db, user, f"voucher.batch.{body.action}", f"{len(done)} 张", ",".join(done[:20]))
    db.commit()
    return {"done": len(done), "nos": done, "skipped": skipped}


@router.post("/vouchers/renumber")
def renumber_vouchers(period: str, word: str = "记", db=Depends(get_db), user=Depends(current_user)):
    """整理凭证号：把某期间某字凭证按「日期 + 原编号」重排成 1..N 的连续号。

    插入、删除之后会留下断号，审计上和「凭证要连续编号」的要求不符。
    作废凭证仍然占号（保留痕迹），只有真正删掉的号才会被补上。
    """
    check_period_open(db, period)
    vs = db.scalars(
        select(Voucher)
        .where(Voucher.period == period, Voucher.word == word)
        .order_by(Voucher.vdate, Voucher.seq)
    ).all()
    if not vs:
        raise HTTPException(400, f"{period} 没有 {word} 字凭证")

    before = [v.seq for v in vs]
    ids = [v.id for v in vs]
    if before == list(range(1, len(vs) + 1)):
        return {"updated": 0, "unchanged": True, "count": len(vs), "from": before, "to": before}

    OFF = 100000
    db.execute(
        update(Voucher).where(Voucher.id.in_(ids)).values(seq=Voucher.seq + OFF),
        execution_options={"synchronize_session": False},
    )
    for i, vid in enumerate(ids, 1):
        db.execute(
            update(Voucher).where(Voucher.id == vid).values(seq=i),
            execution_options={"synchronize_session": False},
        )
    log(db, user, "voucher.renumber", f"{period} {word} 字", f"{len(vs)} 张")
    db.commit()
    db.expire_all()
    return {
        "updated": sum(1 for i, s in enumerate(before, 1) if s != i),
        "unchanged": False,
        "count": len(vs),
        "from": before,
        "to": list(range(1, len(vs) + 1)),
    }


@router.delete("/vouchers/{vid}")
def delete_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status not in ("draft", "void"):
        raise HTTPException(400, "已审核或已记账凭证不能删除，请先反审核")
    if v.period and db.scalar(select(Period).where(Period.code == v.period, Period.status == "closed")):
        raise HTTPException(400, "期间已结账，不能删除凭证")

    dep_ref = db.scalar(select(AssetDep).where(AssetDep.voucher_id == v.id))
    if dep_ref is not None:
        raise HTTPException(400, "该凭证关联折旧明细，不能直接删除；请到固定资产执行撤销本期折旧")

    surtax = db.scalar(select(SurtaxCalc).where(SurtaxCalc.voucher_id == v.id))
    if surtax is not None:
        surtax.voucher_id = None
        surtax.confirmed = False
        surtax.confirmed_by = ""
        surtax.confirmed_at = None

    income_tax = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.voucher_id == v.id))
    if income_tax is not None:
        income_tax.voucher_id = None
        income_tax.confirmed = False
        income_tax.confirmed_by = ""
        income_tax.confirmed_at = None

    for asset in db.scalars(select(Asset).where(Asset.voucher_id == v.id)).all():
        asset.voucher_id = None

    log(db, user, "voucher.delete", v.id, f"{v.word}-{v.seq:04d}")
    db.delete(v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "凭证仍被其他业务记录引用，请先解除业务关联后再删除")
    return {"ok": True}


class CopyIn(BaseModel):
    date: str | None = None
    note: str | None = None


@router.post("/vouchers/{vid}/copy")
def copy_voucher(vid: int, body: CopyIn | None = None, db=Depends(get_db), user=Depends(current_user)):
    """把一张凭证复制成新凭证（草稿）。

    已结账的凭证不能改，实务上的做法是「照原样再做一张到本期」——比如
    上月漏提的折旧、跨期摊销。这里原样复制分录（含往来单位与税率），
    金额一分不动，只换期间与凭证号，落成未审核状态由人复核。
    """
    src = db.get(Voucher, vid)
    if not src:
        raise HTTPException(404, "凭证不存在")
    target_date = (body.date if body and body.date else date.today().isoformat())
    period = period_of(target_date)
    check_period_open(db, period)

    v = Voucher(
        period=period,
        vdate=date.fromisoformat(target_date),
        word=src.word or "记",
        seq=_next_seq(db, period, src.word or "记"),
        attachments=src.attachments,
        note=(body.note if body and body.note else src.note)[:200],
        maker=user["username"],
        status="draft",
        source=src.source,
    )
    db.add(v)
    db.flush()
    for i, l in enumerate(src.lines, 1):
        db.add(VoucherLine(
            voucher_id=v.id, line_no=i, summary=l.summary, account_id=l.account_id,
            debit=l.debit, credit=l.credit, aux_type=l.aux_type, aux_name=l.aux_name,
            qty=l.qty, price=l.price, tax_rate=l.tax_rate,
        ))
    db.flush()
    log(db, user, "voucher.copy", v.id, f"{src.word}-{src.seq:04d} → {v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


@router.post("/vouchers/{vid}/audit")
def audit_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status != "draft":
        raise HTTPException(400, "只有未审核凭证可以审核")
    check_period_open(db, v.period)
    v.status = "audited"
    v.auditor = user["username"]
    log(db, user, "voucher.audit", v.id, f"{v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


@router.post("/vouchers/{vid}/unaudit")
def unaudit_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status != "audited":
        raise HTTPException(400, "只有已审核凭证可以反审核")
    check_period_open(db, v.period)
    v.status = "draft"
    v.auditor = ""
    log(db, user, "voucher.unaudit", v.id, f"{v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


@router.post("/vouchers/{vid}/post")
def post_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status != "audited":
        raise HTTPException(400, "只有已审核凭证可以记账")
    check_period_open(db, v.period)
    v.status = "posted"
    v.poster = user["username"]
    log(db, user, "voucher.post", v.id, f"{v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


@router.post("/vouchers/{vid}/unpost")
def unpost_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status != "posted":
        raise HTTPException(400, "只有已记账凭证可以反记账")
    check_period_open(db, v.period)
    v.status = "audited"
    v.poster = ""
    log(db, user, "voucher.unpost", v.id, f"{v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


@router.post("/vouchers/{vid}/void")
def void_voucher(vid: int, db=Depends(get_db), user=Depends(current_user)):
    v = db.get(Voucher, vid)
    if not v:
        raise HTTPException(404, "凭证不存在")
    if v.status in BOOKED_STATUSES:
        raise HTTPException(400, "已审核入账的凭证不能直接作废，请先反审核")
    check_period_open(db, v.period)
    v.status = "void"
    log(db, user, "voucher.void", v.id, f"{v.word}-{v.seq:04d}")
    db.commit()
    return voucher_out(db, v)


# ------------------------------------------------------------------ 期初余额
OPENING_CATEGORIES = {"asset", "liability", "equity", "cost", "pl"}


class OpeningLineIn(BaseModel):
    account_id: int
    aux_type: str = ""
    aux_name: str = ""
    opening_balance: float = 0
    ytd_debit: float = 0
    ytd_credit: float = 0
    year_opening: float = 0
    actual_pl: float = 0
    manual_override: bool = False
    remark: str = ""


class OpeningIn(BaseModel):
    start_period: str
    note: str = ""
    lines: list[OpeningLineIn] = Field(default_factory=list)


def _opening_year_opening(account: Account, opening_balance: int,
                          ytd_debit: int, ytd_credit: int) -> int:
    return opening_year_opening(account, opening_balance, ytd_debit, ytd_credit)


def _opening_rows_and_warnings(db, batch: OpeningBatch | None, start_period: str):
    accounts = db.scalars(
        select(Account).where(Account.enabled.is_(True)).order_by(Account.code)
    ).all()
    lines = db.scalars(select(OpeningLine).where(OpeningLine.batch_id == batch.id)).all() if batch else []
    snapshot = build_opening_snapshot(accounts, lines)
    amounts = snapshot["amounts"]
    child_totals = snapshot["child_totals"]
    manual_flags = snapshot["manual_flags"]
    manual_line_ids = snapshot["manual_line_ids"]
    lines_by_account = {}
    for line in lines:
        lines_by_account.setdefault(line.account_id, []).append(line)

    rows = []
    for account in accounts:
        if account.cat not in OPENING_CATEGORIES:
            continue
        if account.is_leaf:
            account_lines = lines_by_account.get(account.id) or [None]
            for line in account_lines:
                opening = int(line.opening_balance or 0) if line else 0
                ytd_dr = int(line.ytd_debit or 0) if line else 0
                ytd_cr = int(line.ytd_credit or 0) if line else 0
                rows.append({
                    "line_id": line.id if line else None,
                    "account_id": account.id,
                    "code": account.code,
                    "name": account.name.strip(),
                    "cat": account.cat,
                    "direction": account.direction,
                    "parent_id": account.parent_id,
                    "level": account.level,
                    "is_leaf": True,
                    "manual_override": False,
                    "aux_types": account.aux_types,
                    "aux_type": line.aux_type if line else "",
                    "aux_name": line.aux_name if line else "",
                    "opening_balance": from_cents(opening),
                    "ytd_debit": from_cents(ytd_dr),
                    "ytd_credit": from_cents(ytd_cr),
                    "year_opening": from_cents(_opening_year_opening(account, opening, ytd_dr, ytd_cr)),
                    "actual_pl": from_cents(line.actual_pl) if line else 0,
                    "remark": line.remark if line else "",
                    "child_opening_balance": 0,
                    "child_ytd_debit": 0,
                    "child_ytd_credit": 0,
                    "child_year_opening": 0,
                    "child_actual_pl": 0,
                    "differences": {field: 0 for field in (
                        "opening_balance", "ytd_debit", "ytd_credit", "year_opening", "actual_pl"
                    )},
                })
        else:
            amount = amounts[account.id]
            child = child_totals.get(account.id)
            if child is None:
                child = type(amount)()
            child_year_opening = _opening_year_opening(
                account,
                child.opening_balance,
                child.ytd_debit,
                child.ytd_credit,
            )
            row = {
                "line_id": manual_line_ids.get(account.id),
                "account_id": account.id,
                "code": account.code,
                "name": account.name.strip(),
                "cat": account.cat,
                "direction": account.direction,
                "parent_id": account.parent_id,
                "level": account.level,
                "is_leaf": False,
                "manual_override": bool(manual_flags.get(account.id)),
                "aux_types": "",
                "aux_type": "",
                "aux_name": "",
                "opening_balance": from_cents(amount.opening_balance),
                "ytd_debit": from_cents(amount.ytd_debit),
                "ytd_credit": from_cents(amount.ytd_credit),
                "year_opening": from_cents(amount.year_opening),
                "actual_pl": from_cents(amount.actual_pl),
                "remark": "",
                "child_opening_balance": from_cents(child.opening_balance),
                "child_ytd_debit": from_cents(child.ytd_debit),
                "child_ytd_credit": from_cents(child.ytd_credit),
                "child_year_opening": from_cents(child_year_opening),
                "child_actual_pl": from_cents(child.actual_pl),
                "differences": {
                    "opening_balance": from_cents(amount.opening_balance - child.opening_balance),
                    "ytd_debit": from_cents(amount.ytd_debit - child.ytd_debit),
                    "ytd_credit": from_cents(amount.ytd_credit - child.ytd_credit),
                    "year_opening": from_cents(amount.year_opening - child_year_opening),
                    "actual_pl": from_cents(amount.actual_pl - child.actual_pl),
                },
            }
            manual_line = next(
                (line for line in lines_by_account.get(account.id, []) if line.manual_override),
                None,
            )
            row["remark"] = manual_line.remark if manual_line else ""
            rows.append(row)

    labels = {
        "opening_balance": "期初余额",
        "ytd_debit": "本年累计借方",
        "ytd_credit": "本年累计贷方",
        "year_opening": "年初余额",
        "actual_pl": "实际损益发生额",
    }
    warnings = []
    for row in rows:
        if row["is_leaf"] or not row["manual_override"]:
            continue
        differences = {
            field: value for field, value in row["differences"].items()
            if abs(float(value or 0)) > 0.000001
        }
        if not differences:
            continue
        details = "；".join(
            f"{labels[field]} 手工 {row['opening_balance' if field == 'opening_balance' else field]:,.2f}，"
            f"下级合计 {row['child_' + field]:,.2f}，差额 {value:,.2f}"
            for field, value in differences.items()
        )
        warnings.append({
            "account_id": row["account_id"],
            "code": row["code"],
            "name": row["name"],
            "differences": differences,
            "message": f"{row['code']} {row['name']}：{details}",
        })

    totals = _opening_totals(db, batch) if batch else {
        "debit": 0, "credit": 0, "diff": 0, "balanced": True,
    }
    totals = {
        **totals,
        "debit": from_cents(totals["debit"]),
        "credit": from_cents(totals["credit"]),
        "diff": from_cents(totals["diff"]),
    }
    return rows, warnings, totals


def _opening_totals(db, batch: OpeningBatch):
    accounts = db.scalars(
        select(Account).where(Account.enabled.is_(True)).order_by(Account.code)
    ).all()
    account_by_id = {account.id: account for account in accounts}
    lines = db.scalars(select(OpeningLine).where(OpeningLine.batch_id == batch.id)).all()
    debit = credit = 0
    for line in selected_opening_lines(accounts, lines):
        account = account_by_id.get(line.account_id)
        if account is None or account.cat == "pl":
            continue
        amount = int(line.opening_balance or 0)
        if account.direction == "dr":
            debit += max(amount, 0)
            credit += max(-amount, 0)
        else:
            credit += max(amount, 0)
            debit += max(-amount, 0)
    return {"debit": debit, "credit": credit, "diff": debit - credit, "balanced": debit == credit}


def _opening_out(db, batch: OpeningBatch | None, start_period: str):
    rows, warnings, totals = _opening_rows_and_warnings(db, batch, start_period)
    return {
        "batch": ({
            "id": batch.id, "start_period": batch.start_period, "status": batch.status,
            "note": batch.note, "opening_voucher_id": batch.opening_voucher_id,
            "created_by": batch.created_by, "confirmed_by": batch.confirmed_by,
            "confirmed_at": batch.confirmed_at.isoformat() if batch.confirmed_at else None,
        } if batch else None),
        "start_period": batch.start_period if batch else start_period,
        "rows": rows,
        "warnings": warnings,
        "totals": totals,
    }


@router.get("/opening")
def get_opening(start_period: str | None = None, db=Depends(get_db), user=Depends(current_user)):
    batch = None
    if start_period:
        batch = db.scalar(select(OpeningBatch).where(OpeningBatch.start_period == start_period))
    if batch is None:
        batch = db.scalar(select(OpeningBatch).order_by(OpeningBatch.id.desc()).limit(1))
    company = db.scalar(select(Company).limit(1))
    fallback = batch.start_period if batch else (company.start_period if company else "2025-01")
    return _opening_out(db, batch, fallback)


@router.post("/opening")
def save_opening(body: OpeningIn, db=Depends(get_db), user=Depends(current_user)):
    batch = db.scalar(select(OpeningBatch).where(OpeningBatch.start_period == body.start_period))
    if batch and batch.status == "confirmed":
        raise HTTPException(400, "期初已确认，请先反确认再修改")
    if batch is None:
        batch = OpeningBatch(start_period=body.start_period, created_by=user["username"])
        db.add(batch)
        db.flush()
    batch.note = body.note[:200]
    db.query(OpeningLine).filter(OpeningLine.batch_id == batch.id).delete()
    seen_manual_parents = set()
    for line in body.lines:
        account = db.get(Account, line.account_id)
        if not account or not account.enabled or account.cat not in OPENING_CATEGORIES:
            raise HTTPException(400, "期初只能录入启用的会计科目")
        manual_override = bool(line.manual_override)
        if not account.is_leaf:
            if not manual_override:
                raise HTTPException(400, f"非末级科目「{account.name}」只能以手工覆盖方式保存")
            if account.id in seen_manual_parents:
                raise HTTPException(400, f"上级科目「{account.name}」只能保存一条手工合计")
            seen_manual_parents.add(account.id)
        opening_balance = to_cents(line.opening_balance)
        ytd_debit = to_cents(line.ytd_debit)
        ytd_credit = to_cents(line.ytd_credit)
        actual_pl = to_cents(line.actual_pl)
        if account.cat == "pl":
            if opening_balance != 0:
                raise HTTPException(400, f"损益科目「{account.name}」期初余额必须为0")
        elif actual_pl != 0:
            raise HTTPException(400, f"只有损益科目可以录入实际损益发生额：{account.name}")
        year_opening = _opening_year_opening(account, opening_balance, ytd_debit, ytd_credit)
        db.add(OpeningLine(
            batch_id=batch.id, account_id=line.account_id,
            aux_type=(line.aux_type if account.is_leaf else "")[:20],
            aux_name=(line.aux_name if account.is_leaf else "")[:120],
            manual_override=manual_override and not account.is_leaf,
            opening_balance=opening_balance,
            ytd_debit=ytd_debit, ytd_credit=ytd_credit,
            year_opening=year_opening, actual_pl=actual_pl,
            remark=line.remark[:200],
        ))
    db.flush()
    log(db, user, "opening.save", body.start_period, f"{len(body.lines)} 行")
    db.commit()
    return _opening_out(db, batch, body.start_period)


@router.post("/opening/{batch_id}/confirm")
def confirm_opening(batch_id: int, db=Depends(get_db), user=Depends(current_user)):
    batch = db.get(OpeningBatch, batch_id)
    if not batch:
        raise HTTPException(404, "期初批次不存在")
    if batch.status == "confirmed":
        raise HTTPException(400, "期初已经确认")
    totals = _opening_totals(db, batch)
    if not totals["balanced"]:
        raise HTTPException(400, f"期初借贷不平衡，差额 {from_cents(totals['diff']):,.2f}")
    start_year = int(batch.start_period[:4])
    ensure_periods(db, start_year)
    ensure_periods(db, start_year + 1)
    db.flush()
    accounts = db.scalars(
        select(Account).where(Account.enabled.is_(True)).order_by(Account.code)
    ).all()
    account_by_id = {account.id: account for account in accounts}
    lines = db.scalars(select(OpeningLine).where(OpeningLine.batch_id == batch.id)).all()
    voucher_lines = []
    # 手工父科目优先，并自动跳过其下级，避免父级与明细重复进入期初凭证。
    for line in selected_opening_lines(accounts, lines):
        if not line.opening_balance:
            continue
        account = account_by_id.get(line.account_id)
        if account is None or account.cat == "pl":
            continue
        if account.direction == "dr":
            debit, credit = line.opening_balance, 0
        else:
            debit, credit = 0, line.opening_balance
        voucher_lines.append({
            "account_id": line.account_id, "debit": debit, "credit": credit,
            "summary": "期初余额", "aux_type": line.aux_type, "aux_name": line.aux_name,
        })
    if not voucher_lines:
        raise HTTPException(400, "没有可确认的资产负债期初余额")
    ok, msg = calc.validate_voucher_lines(voucher_lines)
    if not ok:
        raise HTTPException(400, msg)
    prev_period = period_add(batch.start_period, -1)
    for period in db.scalars(select(Period).where(Period.code < batch.start_period)).all():
        period.status = "closed"
        period.closed_at = period.closed_at or datetime.now()
    period = db.scalar(select(Period).where(Period.code == prev_period))
    if period is None:
        y, m = int(prev_period[:4]), int(prev_period[5:7])
        period = Period(code=prev_period, year=y, month=m, status="closed")
        db.add(period)
    else:
        period.status = "closed"
        period.closed_at = datetime.now()
    py, pm = int(prev_period[:4]), int(prev_period[5:7])
    v = Voucher(
        period=prev_period, vdate=date(py, pm, calendar.monthrange(py, pm)[1]), word="记",
        seq=_next_seq(db, prev_period, "记"), note=f"{batch.start_period} 期初余额",
        maker=user["username"], auditor=user["username"], status="audited", source="opening",
    )
    db.add(v)
    db.flush()
    for i, line in enumerate(voucher_lines, 1):
        db.add(VoucherLine(voucher_id=v.id, line_no=i, summary=line["summary"],
                           account_id=line["account_id"], debit=line["debit"], credit=line["credit"],
                           aux_type=line["aux_type"], aux_name=line["aux_name"]))
    batch.status = "confirmed"
    batch.confirmed_by = user["username"]
    batch.confirmed_at = datetime.now()
    batch.opening_voucher_id = v.id
    company = db.scalar(select(Company).limit(1))
    if company is not None:
        company.start_period = batch.start_period
    log(db, user, "opening.confirm", batch.start_period, f"{len(voucher_lines)} 行")
    db.commit()
    return {"ok": True, "voucher_no": f"{v.word}-{v.seq:04d}", **_opening_out(db, batch, batch.start_period)}


@router.post("/opening/{batch_id}/unconfirm")
def unconfirm_opening(batch_id: int, db=Depends(get_db), user=Depends(current_user)):
    batch = db.get(OpeningBatch, batch_id)
    if not batch or batch.status != "confirmed":
        raise HTTPException(400, "期初未确认")
    used = db.scalar(select(func.count()).select_from(Voucher).where(
        Voucher.period >= batch.start_period, Voucher.source != "opening", Voucher.status != "void"
    )) or 0
    if used:
        raise HTTPException(400, "启用期间已有业务凭证，不能直接反确认期初")
    if batch.opening_voucher_id:
        v = db.get(Voucher, batch.opening_voucher_id)
        if v:
            db.delete(v)
    batch.status = "draft"
    batch.opening_voucher_id = None
    batch.confirmed_by = ""
    batch.confirmed_at = None
    log(db, user, "opening.unconfirm", batch.start_period)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 报表
def _bs_yuan(data):
    for side in ("left", "right"):
        for r in data[side]:
            if r.get("value") is not None:
                r["value"] = from_cents(r["value"])
            if r.get("value_begin") is not None:
                r["value_begin"] = from_cents(r["value_begin"])
    for k in ("total_assets", "total_liab_equity", "diff", "unclosed_profit"):
        data[k] = from_cents(data[k])
    for item in data.get("unclosed_accounts", []):
        item["amount"] = from_cents(item["amount"])
    return data


def _pl_yuan(data):
    for r in data["rows"]:
        if r.get("value") is not None:
            r["value"] = from_cents(r["value"])
        if r.get("value_ytd") is not None:
            r["value_ytd"] = from_cents(r["value_ytd"])
    data["net_profit"] = from_cents(data["net_profit"])
    return data


def _tb_yuan(data):
    for r in data["rows"]:
        for k in ("open_dr", "open_cr", "cur_dr", "cur_cr", "close_dr", "close_cr"):
            r[k] = from_cents(r[k])
    data["totals"] = {k: from_cents(v) for k, v in data["totals"].items()}
    return data


def _company_name(db) -> str:
    c = db.scalar(select(Company).limit(1))
    return c.name if c else ""


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/trial-balance")
def r_trial_balance(period: str, level: int = 99, only_used: bool = False,
                    period_from: str | None = None,
                    db=Depends(get_db), user=Depends(current_user)):
    return _tb_yuan(calc.trial_balance(db, period, level, only_used, period_from))


@router.get("/reports/trial-balance/export")
def export_trial_balance(period: str, level: int = 99, only_used: bool = False,
                         period_from: str | None = None,
                         db=Depends(get_db), user=Depends(current_user)):
    data = _tb_yuan(calc.trial_balance(db, period, level, only_used, period_from))
    return _xlsx(xlsx.trial_balance(data, _company_name(db), period, period_from),
                 f"trial-balance-{period}.xlsx")


@router.get("/accounts/{aid}/balance")
def r_account_balance(aid: int, period: str, db=Depends(get_db), user=Depends(current_user)):
    """单科目某期间的期初/期末余额，录凭证时按 Y 键查一下，避免选错科目。"""
    acc = db.get(Account, aid)
    if not acc:
        raise HTTPException(404, "科目不存在")
    book = calc.Book(db, period)
    return {
        "id": acc.id,
        "code": acc.code,
        "name": acc.name,
        "direction": acc.direction,
        "opening": from_cents(book.sum_codes("begin", [acc.code])),
        "closing": from_cents(book.sum_codes("end", [acc.code])),
    }


@router.get("/reports/ledger")
def r_ledger(account_id: int, period_from: str, period_to: str,
             db=Depends(get_db), user=Depends(current_user)):
    data = calc.ledger(db, account_id, period_from, period_to)
    data["opening"] = from_cents(data["opening"])
    data["closing"] = from_cents(data["closing"])
    data["total_debit"] = from_cents(data["total_debit"])
    data["total_credit"] = from_cents(data["total_credit"])
    for l in data["lines"]:
        l["debit"] = from_cents(l["debit"])
        l["credit"] = from_cents(l["credit"])
        l["balance"] = from_cents(l["balance"])
    return data


@router.get("/reports/balance-sheet")
def r_balance_sheet(period: str, db=Depends(get_db), user=Depends(current_user)):
    return _bs_yuan(calc.balance_sheet(db, period, get_template(db, "balance_sheet")))


@router.get("/reports/balance-sheet/export")
def export_balance_sheet(period: str, db=Depends(get_db), user=Depends(current_user)):
    data = _bs_yuan(calc.balance_sheet(db, period, get_template(db, "balance_sheet")))
    return _xlsx(xlsx.balance_sheet(data, _company_name(db), period),
                 f"balance-sheet-{period}.xlsx")


@router.get("/reports/profit")
def r_profit(period: str, period_from: str | None = None,
             db=Depends(get_db), user=Depends(current_user)):
    return _pl_yuan(calc.profit_statement(db, period, get_template(db, "profit"), period_from))


@router.get("/reports/profit/export")
def export_profit(period: str, period_from: str | None = None,
                  db=Depends(get_db), user=Depends(current_user)):
    data = _pl_yuan(calc.profit_statement(db, period, get_template(db, "profit"), period_from))
    return _xlsx(xlsx.profit(data, _company_name(db), period, period_from),
                 f"profit-{period}.xlsx")


@router.get("/reports/check")
def r_check(period: str, db=Depends(get_db), user=Depends(current_user)):
    dr = db.scalar(
        select(func.coalesce(func.sum(VoucherLine.debit), 0))
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.status.in_(BOOKED_STATUSES), Voucher.period <= period)
    ) or 0
    cr = db.scalar(
        select(func.coalesce(func.sum(VoucherLine.credit), 0))
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.status.in_(BOOKED_STATUSES), Voucher.period <= period)
    ) or 0
    unposted = db.scalar(
        select(func.count()).select_from(Voucher)
        .where(Voucher.period == period, Voucher.status == "draft", Voucher.source != "carryover")
    ) or 0
    tpl = get_template(db, "balance_sheet")
    bs = calc.balance_sheet(db, period, tpl)
    return {
        "period": period,
        "debit_total": from_cents(dr),
        "credit_total": from_cents(cr),
        "ledger_balanced": dr == cr,
        "unposted_count": unposted,
        "sheet_balanced": bs["balanced"],
        "sheet_diff": from_cents(bs["diff"]),
    }


# ------------------------------------------------------------------ 期末处理
@router.get("/periods")
def list_periods(db=Depends(get_db), user=Depends(current_user)):
    rows = db.scalars(select(Period).order_by(Period.code)).all()
    out = []
    for p in rows:
        posted = db.scalar(
            select(func.count()).select_from(Voucher)
            .where(Voucher.period == p.code, Voucher.status.in_(BOOKED_STATUSES))
        ) or 0
        pending = db.scalar(
            select(func.count()).select_from(Voucher)
            .where(Voucher.period == p.code, Voucher.status == "draft", Voucher.source != "carryover")
        ) or 0
        out.append({"code": p.code, "status": p.status, "posted": posted, "booked": posted,
                    "pending": pending, "closed_at": p.closed_at.isoformat() if p.closed_at else None})
    return out


def _current_open_period(db) -> str | None:
    periods = db.scalars(select(Period).order_by(Period.code)).all()
    opens = [p.code for p in periods if p.status == "open"]
    closed = [p.code for p in periods if p.status == "closed"]
    if not opens:
        return None
    if not closed:
        return opens[0]
    last_closed = max(closed)
    candidates = [c for c in opens if c > last_closed]
    return candidates[0] if candidates else opens[-1]


def _asset_reconciliation(db, period: str) -> dict:
    book = calc.Book(db, period)
    gl_original = book.sum_codes("end", ["1601"])
    gl_accum = book.sum_codes("end", ["1602"])
    assets = db.scalars(select(Asset).where(Asset.status != "disposed")).all()
    card_original = sum(a.original_value for a in assets)
    card_accum = sum(a.accum_dep for a in assets)
    original_diff = card_original - gl_original
    accum_diff = card_accum - gl_accum
    return {
        "ok": original_diff == 0 and accum_diff == 0,
        "card_original": from_cents(card_original),
        "gl_original": from_cents(gl_original),
        "original_diff": from_cents(original_diff),
        "card_accum": from_cents(card_accum),
        "gl_accum": from_cents(gl_accum),
        "accum_diff": from_cents(accum_diff),
    }


def _voucher_for_source(db, period: str, source: str) -> Voucher | None:
    return db.scalar(
        select(Voucher).where(
            Voucher.period == period,
            Voucher.source == source,
            Voucher.status != "void",
        ).order_by(Voucher.id.desc())
    )


def _voucher_amount(v: Voucher | None) -> int:
    if not v:
        return 0
    return sum(line.debit for line in v.lines)


def _voucher_card(v: Voucher | None, amount: int, *, can_generate: bool = False,
                  blocker: str = "", action: str = "generate") -> dict:
    if v:
        status = v.status
        label = {"draft": "待审核", "audited": "已审核", "posted": "已入账", "void": "已作废"}.get(status, status)
        amount = _voucher_amount(v) or amount
    else:
        status = "ready" if can_generate and amount > 0 else "pending"
        label = "可生成" if status == "ready" else "待处理"
    return {
        "amount": from_cents(amount),
        "status": status,
        "status_label": label,
        "can_generate": bool(can_generate and amount > 0 and v is None),
        "blocker": blocker,
        "action": action,
        "voucher": (
            {
                "id": v.id,
                "no": f"{v.word}-{v.seq:04d}",
                "status": v.status,
                "source": v.source,
            }
            if v else None
        ),
    }


def _income_tax_result(
    db,
    period: str,
    *,
    prior_loss: int = 0,
    employee_count: int = 0,
    total_assets: int | None = None,
    industry_ok: bool = True,
):
    if total_assets is None:
        bs = calc.balance_sheet(db, period, get_template(db, "balance_sheet"))
        total_assets = int(bs["total_assets"])
    return income_tax_engine.calculate(
        db, period,
        prior_loss=prior_loss,
        employee_count=employee_count,
        total_assets=total_assets,
        industry_ok=industry_ok,
    )


def _income_tax_preview_out(db, period: str, result: dict):
    existing = _voucher_for_source(db, period, "income_tax")
    saved = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == period))
    out = dict(result)
    for key in ("ytd_profit", "prior_loss", "taxable_income", "total_assets",
                "cumulative_tax", "previous_accrued", "previous_snapshot",
                "previous_book_expense", "previous_diff", "current_accrual"):
        out[key] = from_cents(out.get(key, 0))
    out["existing_voucher"] = (
        {"id": existing.id, "no": f"{existing.word}-{existing.seq:04d}", "status": existing.status}
        if existing else None
    )
    out["saved_confirmed"] = bool(saved and saved.confirmed)
    out["can_generate"] = bool(
        saved and saved.confirmed and saved.current_accrual != 0 and not existing
    )
    return out


@router.get("/periods/{code}/closing-summary")
def period_closing_summary(code: str, db=Depends(get_db), user=Depends(current_user)):
    p = db.scalar(select(Period).where(Period.code == code))
    if not p:
        raise HTTPException(404, "期间不存在")
    counts = {}
    for st in ("draft", "audited", "posted", "void"):
        counts[st] = db.scalar(
            select(func.count()).select_from(Voucher).where(Voucher.period == code, Voucher.status == st)
        ) or 0
    total_vouchers = db.scalar(
        select(func.count()).select_from(Voucher).where(Voucher.period == code, Voucher.status != "void")
    ) or 0

    # 结转未交增值税
    vat_book_data = vat_engine.book(db, code)
    vat_transfer = _voucher_for_source(db, code, "vat_transfer")
    vat_transfer_amount = _voucher_amount(vat_transfer) or max(int(vat_book_data["payable"]), 0)

    # 附加税：已确认快照优先，否则展示默认测算值
    surtax = db.scalar(select(SurtaxCalc).where(SurtaxCalc.period == code))
    surtax_voucher = _voucher_for_source(db, code, "surtax")
    if surtax and surtax.confirmed:
        surtax_amount = surtax.net_amount
    else:
        sources = vat_engine.surtax_source_amounts(db, code)
        chosen = next((x for x in sources if x["available"]), sources[0])
        surtax_amount = vat_engine.calculate_surtax(chosen["amount"], small_micro=True)["net_total"]
    surtax_card = _voucher_card(
        surtax_voucher, surtax_amount,
        can_generate=bool(surtax and surtax.confirmed),
        blocker="" if surtax and surtax.confirmed else "请先打开测算并确认附加税",
        action="surtax",
    )
    if surtax_voucher is None and surtax and surtax.confirmed:
        surtax_card["status"] = "confirmed"
        surtax_card["status_label"] = "已确认待生成"

    # 季度企业所得税预提
    income_card = None
    if income_tax_engine.is_quarter_end(code):
        income_row = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == code))
        income_voucher = _voucher_for_source(db, code, "income_tax")
        if income_row and income_row.confirmed:
            current_accrual = income_row.current_accrual
            income_result = {
                "small_micro": income_row.small_micro,
                "effective_rate": income_row.effective_rate,
            }
            income_status = "confirmed"
            income_label = "应冲回" if current_accrual < 0 else "已确认待生成"
            income_blocker = ""
        else:
            income_result = _income_tax_result(db, code, employee_count=0)
            current_accrual = int(income_result["current_accrual"])
            income_status = "pending"
            income_label = "应冲回" if current_accrual < 0 else ("可计提" if current_accrual > 0 else "无需计提")
            income_blocker = "请打开测算并确认所得税" if current_accrual != 0 else "累计利润为零或亏损"
        if income_voucher:
            income_status = income_voucher.status
            income_label = {"draft": "待审核", "audited": "已审核", "posted": "已入账"}.get(
                income_voucher.status, income_voucher.status
            )
            current_accrual = _voucher_amount(income_voucher) or current_accrual
        income_card = {
            "key": "income_tax",
            "title": "计提所得税",
            "settings": True,
            "amount": from_cents(current_accrual),
            "status": income_status,
            "status_label": income_label,
            "can_generate": bool(income_row and income_row.confirmed and current_accrual != 0 and not income_voucher),
            "blocker": income_blocker,
            "action": "income_tax",
            "voucher": (
                {"id": income_voucher.id, "no": f"{income_voucher.word}-{income_voucher.seq:04d}",
                 "status": income_voucher.status, "source": income_voucher.source}
                if income_voucher else None
            ),
            "small_micro": income_result.get("small_micro", False),
            "effective_rate": income_result.get("effective_rate", 2500),
        }

    # 折旧
    dep = asset_engine.preview(db, code)
    dep_voucher = _voucher_for_source(db, code, "depreciation")
    dep_card = _voucher_card(
        dep_voucher, dep["total"] or dep["posted_total"],
        can_generate=dep["count"] > 0 and not dep["posted_total"],
        blocker="" if dep["count"] > 0 or dep["posted_total"] else "本期没有可计提折旧的资产",
        action="depreciation",
    )

    # 往来冲销
    partner_ledger = partner_engine.ledger(db, code, only_unsettled=True)
    settle_amount = abs(int(partner_ledger["total"]["balance"]))
    settle_card = {
        "amount": from_cents(settle_amount),
        "status": "ready" if settle_amount > 0 else "pending",
        "status_label": "可核销" if settle_amount > 0 else "无需核销",
        "can_generate": False,
        "blocker": "",
        "action": "settle",
        "voucher": None,
    }

    # 年度结转：仅12月，把本年利润转入利润分配
    year_close_card = None
    if int(code[5:7]) == 12:
        year_voucher = _voucher_for_source(db, code, "year_close")
        if year_voucher:
            year_close_card = {
                "key": "year_close", "title": "年度结转", "settings": False,
                **_voucher_card(year_voucher, _voucher_amount(year_voucher), action="year_close"),
            }
        else:
            carry_preview_lines, carry_preview_total = calc.build_carryover(db, code)
            book_for_year = calc.Book(db, code)
            year_amount = book_for_year.sum_codes("end", ["3103"]) + (carry_preview_total if carry_preview_lines else 0)
            year_close_card = {
                "key": "year_close", "title": "年度结转", "settings": False,
                "amount": from_cents(year_amount), "status": "ready" if year_amount else "pending",
                "status_label": "待随结账自动结转" if year_amount else "无需结转",
                "can_generate": False, "blocker": "", "action": "year_close", "voucher": None,
            }

    # 结转损益：最终结转到下期时自动完成
    carry = _voucher_for_source(db, code, "carryover")
    if carry:
        carry_amount = _voucher_amount(carry)
        carry_card = _voucher_card(carry, carry_amount, action="carryover")
    else:
        carry_lines, carry_total = calc.build_carryover(db, code)
        carry_amount = carry_total if carry_lines else 0
        carry_card = {
            "amount": from_cents(carry_amount),
            "status": "ready" if carry_amount else "pending",
            "status_label": "待随结账自动结转" if carry_amount else "无需结转",
            "can_generate": False,
            "blocker": "",
            "action": "carryover",
            "voucher": None,
        }

    cards = [
        {"key": "vat_transfer", "title": "结转未交增值税", "settings": False,
         **_voucher_card(vat_transfer, vat_transfer_amount,
                          can_generate=vat_transfer_amount > 0,
                          blocker="" if vat_transfer_amount > 0 else "本期无应纳增值税",
                          action="vat_transfer")},
        {"key": "surtax", "title": "计提税金", "settings": True, **surtax_card},
    ]
    if income_card is not None:
        cards.append(income_card)
    cards.extend([
        {"key": "depreciation", "title": "计提折旧", "settings": True, **dep_card},
        {"key": "settle", "title": "往来冲销", "settings": False, **settle_card},
        {"key": "carryover", "title": "结转损益", "settings": False, **carry_card},
    ])
    if year_close_card is not None:
        cards.append(year_close_card)
    return {
        "period": code,
        "period_status": p.status,
        "current_open_period": _current_open_period(db),
        "total_vouchers": total_vouchers,
        "counts": counts,
        "asset_reconciliation": _asset_reconciliation(db, code),
        "cards": cards,
    }


@router.post("/periods/{code}/transfer-vat")
def transfer_vat(code: str, db=Depends(get_db), user=Depends(current_user)):
    if code != _current_open_period(db):
        raise HTTPException(400, "只能处理当前待结账期间")
    check_period_open(db, code)
    existing = _voucher_for_source(db, code, "vat_transfer")
    if existing:
        raise HTTPException(400, "本期已生成过结转未交增值税凭证")
    b = vat_engine.book(db, code)
    amount = max(int(b["payable"]), 0)
    if amount <= 0:
        raise HTTPException(400, "本期无应纳增值税，无需结转")
    transfer = db.scalar(select(Account).where(Account.code == "2221.01.05"))
    unpaid = db.scalar(select(Account).where(Account.code == "2221.02"))
    if not transfer or not unpaid:
        raise HTTPException(500, "缺少结转未交增值税科目")
    lines = [
        {"account_id": transfer.id, "debit": amount, "credit": 0, "summary": "结转本期未交增值税"},
        {"account_id": unpaid.id, "debit": 0, "credit": amount, "summary": "结转本期未交增值税"},
    ]
    ok, msg = calc.validate_voucher_lines(lines)
    if not ok:
        raise HTTPException(400, msg)
    v = _system_voucher(
        db, code, date.fromisoformat(code + "-01"),
        f"结转 {code} 未交增值税", "vat_transfer", user["username"], lines,
    )
    log(db, user, "period.transfer_vat", code, f"{from_cents(amount):,.2f}")
    db.commit()
    return {"voucher_id": v.id, "voucher_no": f"{v.word}-{v.seq:04d}", "amount": from_cents(amount)}


class IncomeTaxConfirmIn(BaseModel):
    period: str
    prior_loss: float = 0
    employee_count: int = 0
    total_assets: float | None = None
    industry_ok: bool = True
    note: str = ""


@router.get("/periods/{code}/income-tax/preview")
def income_tax_preview(
    code: str,
    prior_loss: float = 0,
    employee_count: int = 0,
    total_assets: float | None = None,
    industry_ok: bool = True,
    db=Depends(get_db),
    user=Depends(current_user),
):
    result = _income_tax_result(
        db, code,
        prior_loss=to_cents(prior_loss),
        employee_count=employee_count,
        total_assets=to_cents(total_assets) if total_assets is not None else None,
        industry_ok=industry_ok,
    )
    return _income_tax_preview_out(db, code, result)


@router.post("/periods/{code}/income-tax/confirm")
def confirm_income_tax(code: str, body: IncomeTaxConfirmIn, db=Depends(get_db), user=Depends(current_user)):
    if code != _current_open_period(db):
        raise HTTPException(400, "只能处理当前待结账期间")
    if not income_tax_engine.is_quarter_end(code):
        raise HTTPException(400, "所得税只在季度末月份计提")
    check_period_open(db, code)
    existing = _voucher_for_source(db, code, "income_tax")
    if existing:
        raise HTTPException(400, "本季度已生成企业所得税凭证")
    result = _income_tax_result(
        db, code,
        prior_loss=to_cents(body.prior_loss),
        employee_count=body.employee_count,
        total_assets=to_cents(body.total_assets) if body.total_assets is not None else None,
        industry_ok=body.industry_ok,
    )
    if body.employee_count <= 0:
        raise HTTPException(400, "请填写季度平均从业人数")
    if result["total_assets"] <= 0:
        raise HTTPException(400, "请填写季度平均资产总额")
    if result["current_accrual"] == 0:
        raise HTTPException(400, "本季度应计提所得税为零，无需生成凭证")
    row = income_tax_engine.save_snapshot(db, result, user["username"], body.note)
    log(db, user, "period.income_tax.confirm", code,
        f"应纳税所得额 {from_cents(row.taxable_income):,.2f}，本季预提 {from_cents(row.current_accrual):,.2f}")
    db.commit()
    return _income_tax_preview_out(db, code, result)


@router.post("/periods/{code}/income-tax/voucher")
def create_income_tax_voucher(code: str, db=Depends(get_db), user=Depends(current_user)):
    if code != _current_open_period(db):
        raise HTTPException(400, "只能处理当前待结账期间")
    check_period_open(db, code)
    existing = _voucher_for_source(db, code, "income_tax")
    if existing:
        raise HTTPException(400, "本季度已生成企业所得税凭证")
    row = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == code))
    if row is None or not row.confirmed:
        raise HTTPException(400, "请先确认企业所得税测算结果")
    if row.current_accrual == 0:
        raise HTTPException(400, "本季度应计提所得税为零，无需生成凭证")
    expense = db.scalar(select(Account).where(Account.code == "5801"))
    payable = db.scalar(select(Account).where(Account.code == "2221.06"))
    if not expense or not payable:
        raise HTTPException(500, "缺少所得税费用或应交企业所得税科目")
    amount = abs(row.current_accrual)
    if row.current_accrual > 0:
        lines = [
            {"account_id": expense.id, "debit": amount, "credit": 0, "summary": "预提本季度企业所得税"},
            {"account_id": payable.id, "debit": 0, "credit": amount, "summary": "预提本季度企业所得税"},
        ]
    else:
        lines = [
            {"account_id": payable.id, "debit": amount, "credit": 0, "summary": "冲回多提企业所得税"},
            {"account_id": expense.id, "debit": 0, "credit": amount, "summary": "冲回多提企业所得税"},
        ]
    ok, msg = calc.validate_voucher_lines(lines)
    if not ok:
        raise HTTPException(400, msg)
    note = f"预提 {row.year} 年第 {row.quarter} 季度企业所得税"
    if row.current_accrual < 0:
        note = f"冲回 {row.year} 年第 {row.quarter} 季度多提企业所得税"
    v = _system_voucher(db, code, date.fromisoformat(code + "-01"), note,
                        "income_tax", user["username"], lines)
    row.voucher_id = v.id
    log(db, user, "period.income_tax", code,
        f"应纳税所得额 {from_cents(row.taxable_income):,.2f}，本季预提 {from_cents(row.current_accrual):,.2f}")
    db.commit()
    return {
        "voucher_id": v.id,
        "voucher_no": f"{v.word}-{v.seq:04d}",
        "amount": from_cents(row.current_accrual),
        "small_micro": row.small_micro,
        "effective_rate": row.effective_rate,
    }


@router.post("/periods/{code}/carryover")
def carryover(code: str, db=Depends(get_db), user=Depends(current_user)):
    check_period_open(db, code)
    pending = db.scalar(
        select(func.count()).select_from(Voucher)
        .where(Voucher.period == code, Voucher.status == "draft", Voucher.source != "carryover")
    ) or 0
    if pending:
        raise HTTPException(400, f"本期还有 {pending} 张未审核凭证，请先审核再结转")

    existing = db.scalar(
        select(Voucher).where(Voucher.period == code, Voucher.source == "carryover", Voucher.status != "void")
    )
    if existing:
        raise HTTPException(400, "本期已生成过结转凭证，如需重做请先删除原凭证")

    lines, total = calc.build_carryover(db, code)
    if not lines:
        raise HTTPException(400, "本期没有损益发生额，无需结转")

    v = Voucher(period=code, vdate=date.fromisoformat(code + "-01"), word="记",
                seq=_next_seq(db, code, "记"), note="结转本期损益", maker=user["username"],
                auditor=user["username"], status="audited", source="carryover")
    db.add(v)
    db.flush()
    for i, l in enumerate(lines, 1):
        v.lines.append(VoucherLine(line_no=i, summary=l["summary"], account_id=l["account_id"],
                                   debit=l["debit"], credit=l["credit"]))
    log(db, user, "period.carryover", code, f"转入本年利润 {total/100:,.2f}")
    db.commit()
    return {"voucher_id": v.id, "no": f"{v.word}-{v.seq:04d}", "profit": from_cents(total)}


@router.post("/periods/{code}/close")
def close_period(code: str, db=Depends(get_db), user=Depends(require_role("admin"))):
    """一键结账：先自动结转损益，再锁定期间。"""
    p = db.scalar(select(Period).where(Period.code == code))
    if not p:
        raise HTTPException(404, "期间不存在")
    if p.status == "closed":
        raise HTTPException(400, "该期间已结账")
    if code != _current_open_period(db):
        raise HTTPException(400, "只能结账当前待处理期间，不能跳过或提前结账")

    pending = db.scalar(
        select(func.count()).select_from(Voucher)
        .where(Voucher.period == code, Voucher.status == "draft", Voucher.source != "carryover")
    ) or 0
    if pending:
        raise HTTPException(400, f"本期还有 {pending} 张未审核凭证，不能结账")

    vat_data = vat_engine.book(db, code)
    if vat_data["payable"] > 0 and not _voucher_for_source(db, code, "vat_transfer"):
        raise HTTPException(400, "本期有应纳增值税，请先生成并审核结转未交增值税凭证")
    if vat_data["payable"] > 0 and not _voucher_for_source(db, code, "surtax"):
        raise HTTPException(400, "本期有应纳增值税，请先完成并审核计提税金凭证")
    dep_data = asset_engine.preview(db, code)
    if dep_data["count"] > 0 and not _voucher_for_source(db, code, "depreciation"):
        raise HTTPException(400, "本期还有应提折旧，请先生成并审核折旧凭证")
    if income_tax_engine.is_quarter_end(code) and not _voucher_for_source(db, code, "income_tax"):
        income_row = db.scalar(select(IncomeTaxCalc).where(IncomeTaxCalc.period == code))
        income_accrual = income_row.current_accrual if income_row and income_row.confirmed else             _income_tax_result(db, code, employee_count=0)["current_accrual"]
        if income_accrual != 0:
            raise HTTPException(400, "本季度还有企业所得税预提未完成，请先测算、确认并审核凭证")
    asset_check = _asset_reconciliation(db, code)
    if not asset_check["ok"]:
        raise HTTPException(400, "固定资产卡片与 1601/1602 总账不一致，请先核对处理")

    carry = db.scalar(
        select(Voucher).where(
            Voucher.period == code,
            Voucher.source == "carryover",
            Voucher.status != "void",
        )
    )
    carryover_no = f"{carry.word}-{carry.seq:04d}" if carry else None
    profit = None

    if carry and carry.status == "draft":
        carry.status = "audited"
        carry.auditor = user["username"]
        db.flush()
        carryover_no = f"{carry.word}-{carry.seq:04d}"
    elif not carry:
        lines, total = calc.build_carryover(db, code)
        if lines:
            carry = Voucher(
                period=code,
                vdate=date.fromisoformat(code + "-01"),
                word="记",
                seq=_next_seq(db, code, "记"),
                note="结转本期损益",
                maker=user["username"],
                auditor=user["username"],
                status="audited",
                source="carryover",
            )
            db.add(carry)
            db.flush()
            for i, l in enumerate(lines, 1):
                carry.lines.append(VoucherLine(
                    line_no=i,
                    summary=l["summary"],
                    account_id=l["account_id"],
                    debit=l["debit"],
                    credit=l["credit"],
                ))
            db.flush()
            carryover_no = f"{carry.word}-{carry.seq:04d}"
            profit = from_cents(total)
        else:
            profit = 0.0

    book = calc.Book(db, code)
    not_carried = [
        book.accounts[aid].name
        for aid, val in book.nets("period").items()
        if book.accounts[aid].cat == "pl" and val != 0
    ]
    if not_carried:
        raise HTTPException(
            400,
            "本期损益类科目仍未结转：" + "、".join(not_carried[:4])
            + ("…" if len(not_carried) > 4 else ""),
        )

    year_close_no = None
    if int(code[5:7]) == 12:
        yc = _voucher_for_source(db, code, "year_close")
        if yc is None:
            book_year = calc.Book(db, code)
            year_profit = book_year.sum_codes("end", ["3103"])
            if year_profit != 0:
                profit_acc = db.scalar(select(Account).where(Account.code == "3103"))
                retained_acc = db.scalar(select(Account).where(Account.code == "3104"))
                if not profit_acc or not retained_acc:
                    raise HTTPException(500, "缺少本年利润或利润分配科目")
                amount = abs(year_profit)
                if year_profit > 0:
                    lines = [
                        {"account_id": profit_acc.id, "debit": amount, "credit": 0, "summary": "年度结转本年利润"},
                        {"account_id": retained_acc.id, "debit": 0, "credit": amount, "summary": "年度结转本年利润"},
                    ]
                else:
                    lines = [
                        {"account_id": retained_acc.id, "debit": amount, "credit": 0, "summary": "年度结转本年亏损"},
                        {"account_id": profit_acc.id, "debit": 0, "credit": amount, "summary": "年度结转本年亏损"},
                    ]
                yc = _system_voucher(
                    db, code, date.fromisoformat(code + "-31"),
                    f"{code[:4]} 年度结转", "year_close", user["username"], lines,
                )
                yc.status = "audited"
                yc.auditor = user["username"]
            if yc is not None and yc.status == "draft":
                yc.status = "audited"
                yc.auditor = user["username"]
        year_close_no = f"{yc.word}-{yc.seq:04d}" if yc else None

    p.status = "closed"
    p.closed_at = datetime.now()
    log(db, user, "period.close", code, f"carryover={carryover_no or '-'}")
    db.commit()
    return {
        "ok": True,
        "code": code,
        "status": p.status,
        "carryover_no": carryover_no,
        "year_close_no": year_close_no,
        "profit": profit,
    }


@router.post("/periods/{code}/reopen")
def reopen_period(code: str, db=Depends(get_db), user=Depends(require_role("admin"))):
    p = db.scalar(select(Period).where(Period.code == code))
    if not p:
        raise HTTPException(404, "期间不存在")
    if p.status != "closed":
        raise HTTPException(400, "该期间未结账")
    latest_closed = db.scalar(
        select(Period.code).where(Period.status == "closed").order_by(Period.code.desc()).limit(1)
    )
    if latest_closed and code != latest_closed:
        raise HTTPException(400, "只能按倒序反结账最近的已结账期间")
    if int(code[5:7]) == 12:
        year_close = _voucher_for_source(db, code, "year_close")
        if year_close and year_close.status != "void":
            year_close.status = "void"
            year_close.auditor = ""
            log(db, user, "period.year_close.void", code, f"{year_close.word}-{year_close.seq:04d}")
    p.status = "open"
    p.closed_at = None
    log(db, user, "period.reopen", code)
    db.commit()
    return {"ok": True, "code": code, "status": p.status}


# ------------------------------------------------------------------ 首页统计
@router.get("/stats")
def stats(period: str, db=Depends(get_db), user=Depends(current_user)):
    tpl = get_template(db, "profit")
    pl = calc.profit_statement(db, period, tpl)
    bs_tpl = get_template(db, "balance_sheet")
    bs = calc.balance_sheet(db, period, bs_tpl)
    counts = {}
    for st in ("draft", "audited", "posted", "void"):
        counts[st] = db.scalar(
            select(func.count()).select_from(Voucher).where(Voucher.period == period, Voucher.status == st)
        ) or 0
    counts["booked"] = counts["audited"] + counts["posted"]

    book = calc.Book(db, period)
    cash = book.sum_codes("end", ["1001", "1002", "1012"])
    ar = book.sum_codes("end", ["1122"])
    ap = book.sum_codes("end", ["2202"])
    inventory = book.sum_codes("end", ["1401", "1402", "1403", "1405", "1408", "1411"])
    expense = book.profit_amount("period", ["5403", "5601", "5602", "5603"])
    try:
        vat = vat_engine.book(db, period)
        vat_payable, vat_credit = vat["payable"], vat["closing_credit"]
    except Exception:
        vat_payable, vat_credit = 0, 0

    return {
        "period": period,
        "revenue": from_cents(pl["rows"][0]["value"] or 0),
        "net_profit": from_cents(pl["net_profit"]),
        "total_assets": from_cents(bs["total_assets"]),
        "balanced": bs["balanced"],
        "diff": from_cents(bs["diff"]),
        "counts": counts,
        "cash": from_cents(cash),
        "ar": from_cents(ar),
        "ap": from_cents(ap),
        "inventory": from_cents(inventory),
        "expense": from_cents(expense),
        "vat_payable": from_cents(vat_payable),
        "vat_credit": from_cents(vat_credit),
    }


# ================================================================== 系统凭证
def _system_voucher(db, period: str, vdate: date, note: str, source: str, user: str, lines: list[dict], word: str = "记"):
    """由业务动作生成凭证（折旧、附加税等），统一走这里，保证编号与字段一致。

    这些凭证是系统生成的，不走 _apply_lines，所以这里必须补上科目校验：
    非明细科目一旦记了分录，余额表和报表都取不到数，而且很难发现。
    """
    for l in lines:
        a = db.get(Account, l["account_id"])
        if not a:
            raise HTTPException(400, "分录引用的科目不存在")
        if not a.is_leaf:
            raise HTTPException(400, f"科目「{a.code} {a.name}」不是明细科目，不能做分录，请先指定明细科目")
    v = Voucher(period=period, vdate=vdate, word=word, seq=_next_seq(db, period, word),
                note=note[:200], maker=user, source=source)
    db.add(v)
    db.flush()
    for i, l in enumerate(lines, 1):
        db.add(VoucherLine(
            voucher_id=v.id, line_no=i,
            summary=(l.get("summary") or "")[:200],
            account_id=l["account_id"],
            debit=int(l.get("debit") or 0), credit=int(l.get("credit") or 0),
            aux_type=l.get("aux_type") or "", aux_name=(l.get("aux_name") or "")[:120],
            tax_rate=int(l.get("tax_rate") or 0),
        ))
    db.flush()
    return v


# ================================================================== 往来档案
def partner_out(p: Partner):
    return {
        "id": p.id, "ptype": p.ptype, "code": p.code, "name": p.name,
        "short_name": p.short_name, "tax_no": p.tax_no, "contact": p.contact,
        "phone": p.phone, "address": p.address, "bank": p.bank,
        "bank_account": p.bank_account, "tax_rate": p.tax_rate,
        "settle_days": p.settle_days, "credit_limit": from_cents(p.credit_limit),
        "enabled": p.enabled, "remark": p.remark,
    }


class PartnerIn(BaseModel):
    ptype: Literal["customer", "supplier"] = "customer"
    code: str = ""
    name: str
    short_name: str = ""
    tax_no: str = ""
    contact: str = ""
    phone: str = ""
    address: str = ""
    bank: str = ""
    bank_account: str = ""
    tax_rate: int = 1300
    settle_days: int = 30
    credit_limit: float = 0
    enabled: bool = True
    remark: str = ""


@router.get("/partners")
def list_partners(ptype: str | None = None, q: str | None = None, enabled: bool | None = None,
                  db=Depends(get_db), user=Depends(current_user)):
    stmt = select(Partner).order_by(Partner.ptype, Partner.code, Partner.name)
    if ptype:
        stmt = stmt.where(Partner.ptype == ptype)
    if q:
        stmt = stmt.where(or_(Partner.name.like(f"%{q}%"), Partner.code.like(f"%{q}%"),
                              Partner.tax_no.like(f"%{q}%")))
    if enabled is not None:
        stmt = stmt.where(Partner.enabled == enabled)
    return [partner_out(p) for p in db.scalars(stmt).all()]


@router.get("/partners/options")
def partner_options(db=Depends(get_db), user=Depends(current_user)):
    """给凭证录入用的轻量列表，按名称排序。"""
    rows = db.scalars(select(Partner).where(Partner.enabled == True).order_by(Partner.name)).all()  # noqa: E712
    return [{"id": p.id, "ptype": p.ptype, "name": p.name, "code": p.code, "tax_rate": p.tax_rate} for p in rows]


@router.post("/partners")
def create_partner(body: PartnerIn, db=Depends(get_db), user=Depends(current_user)):
    if db.scalar(select(Partner).where(Partner.ptype == body.ptype, Partner.name == body.name)):
        raise HTTPException(400, f"「{body.name}」已存在")
    p = Partner(
        ptype=body.ptype, code=body.code or f"{body.ptype[:2].upper()}{int(datetime.now().timestamp()) % 100000}",
        name=body.name, short_name=body.short_name, tax_no=body.tax_no, contact=body.contact,
        phone=body.phone, address=body.address, bank=body.bank, bank_account=body.bank_account,
        tax_rate=body.tax_rate, settle_days=body.settle_days,
        credit_limit=to_cents(body.credit_limit), enabled=body.enabled, remark=body.remark,
    )
    db.add(p)
    log(db, user, "partner.create", p.name)
    db.commit()
    return partner_out(p)


@router.put("/partners/{pid}")
def update_partner(pid: int, body: PartnerIn, db=Depends(get_db), user=Depends(current_user)):
    p = db.get(Partner, pid)
    if not p:
        raise HTTPException(404, "往来单位不存在")
    for k, v in body.model_dump().items():
        if k == "credit_limit":
            p.credit_limit = to_cents(v)
        elif hasattr(p, k):
            setattr(p, k, v)
    log(db, user, "partner.update", p.name)
    db.commit()
    return partner_out(p)


@router.delete("/partners/{pid}")
def delete_partner(pid: int, db=Depends(get_db), user=Depends(current_user)):
    p = db.get(Partner, pid)
    if not p:
        raise HTTPException(404, "往来单位不存在")
    used = db.scalar(
        select(func.count()).select_from(VoucherLine).where(VoucherLine.aux_name == p.name)
    ) or 0
    if used:
        raise HTTPException(400, f"已有 {used} 行凭证引用了「{p.name}」，只能停用不能删除")
    db.delete(p)
    log(db, user, "partner.delete", p.name)
    db.commit()
    return {"ok": True}


@router.get("/partners/ledger")
def partner_ledger(period: str, ptype: str | None = None, db=Depends(get_db), user=Depends(current_user)):
    data = partner_engine.ledger(db, period, ptype=ptype)
    for g in data["rows"]:
        for f in ("open_total", "paid_total", "balance", "overdue"):
            g[f] = from_cents(g[f])
        for i in g["items"]:
            for f in ("open_amount", "pay_amount", "settled", "remain"):
                i[f] = from_cents(i[f])
        g["bands"] = {k: from_cents(v) for k, v in g["bands"].items()}
    data["total"] = {k: from_cents(v) for k, v in data["total"].items()}
    data["unassigned"] = partner_engine.unassigned(db, period)
    data["unassigned"]["total"] = from_cents(data["unassigned"]["total"])
    for r in data["unassigned"]["rows"]:
        r["amount"] = from_cents(r["amount"])
    return data


@router.get("/partners/aging")
def partner_aging(period: str, ptype: str | None = None, db=Depends(get_db), user=Depends(current_user)):
    data = partner_engine.aging(db, period, ptype=ptype)
    for r in data["rows"]:
        for f in ("open_total", "paid_total", "balance", "overdue"):
            r[f] = from_cents(r[f])
        r["cells"] = {k: from_cents(v) for k, v in r["cells"].items()}
    data["totals"]["cells"] = {k: from_cents(v) for k, v in data["totals"]["cells"].items()}
    data["totals"]["balance"] = from_cents(data["totals"]["balance"])
    data["totals"]["overdue"] = from_cents(data["totals"]["overdue"])
    return data


@router.post("/partners/settle")
def run_settle(period: str, ptype: str | None = None, name: str | None = None,
               db=Depends(get_db), user=Depends(current_user)):
    check_period_open(db, period)
    r = partner_engine.auto_settle(db, period, ptype=ptype, partner_name=name)
    log(db, user, "partner.settle", period, f"{r['created']} 笔 / {from_cents(r['amount']):,.2f}")
    r["amount"] = from_cents(r["amount"])
    return r


@router.delete("/partners/settle")
def clear_settle(period: str, db=Depends(get_db), user=Depends(current_user)):
    """清空自动核销记录，回到「按凭证顺序」的原始状态。"""
    olds = [s for s in db.scalars(select(Settle)).all() if s.is_auto]
    for s in olds:
        db.delete(s)
    db.commit()
    return {"removed": len(olds)}


@router.get("/partners/statement")
def partner_statement(name: str, period_from: str, period_to: str,
                      db=Depends(get_db), user=Depends(current_user)):
    data = partner_engine.statement(db, name, period_from, period_to)
    for f in ("opening", "total_open", "total_pay", "closing"):
        data[f] = from_cents(data[f])
    for l in data["lines"]:
        for f in ("debit", "credit", "open_amount", "pay_amount", "balance"):
            l[f] = from_cents(l[f])
    return data


# ================================================================== 账龄区间
@router.get("/aging-bands")
def list_bands(db=Depends(get_db), user=Depends(current_user)):
    rows = db.scalars(select(AgingBand).order_by(AgingBand.seq)).all()
    return [{"id": b.id, "seq": b.seq, "label": b.label, "days_from": b.days_from,
             "days_to": b.days_to, "enabled": b.enabled} for b in rows]


@router.put("/aging-bands")
def update_bands(payload: list[dict] = Body(...), db=Depends(get_db), user=Depends(current_user)):
    for item in payload:
        b = db.get(AgingBand, item["id"]) if item.get("id") else None
        if not b:
            b = AgingBand()
            db.add(b)
        b.seq = int(item.get("seq") or 0)
        b.label = item.get("label") or ""
        b.days_from = int(item.get("days_from") or 0)
        b.days_to = int(item.get("days_to") or 0)
        b.enabled = bool(item.get("enabled", True))
    db.commit()
    return list_bands(db, user)


# ================================================================== 固定资产
ASSET_CATEGORIES = ["房屋建筑物", "机器设备", "运输设备", "电子设备", "办公设备", "其他"]
METHOD_LABEL = {"straight": "年限平均法", "ddb": "双倍余额递减法", "syoyds": "年数总和法"}


def _validate_asset_method(method: str, useful_months: int) -> None:
    if method not in METHOD_LABEL:
        raise HTTPException(400, "折旧方法无效")
    if useful_months < 1 or useful_months > 1200:
        raise HTTPException(400, "使用月数必须在 1 到 1200 之间")
    if method == "syoyds" and useful_months % 12 != 0:
        raise HTTPException(400, "年数总和法要求使用月数为 12 的整数倍")
    if method == "ddb" and useful_months < 24:
        raise HTTPException(400, "双倍余额递减法要求使用年限不少于 24 个月")


def asset_out(a: Asset, accs: dict | None = None):
    accs = accs or {}
    def nm(i):
        x = accs.get(i)
        return f"{x.code} {x.name}" if x else ""
    salvage = round(a.original_value * a.salvage_rate / 10000)
    total = max(a.original_value - salvage, 0)
    left = max(total - a.accum_dep, 0)
    sch = asset_engine.schedule(a)
    return {
        "id": a.id, "code": a.code, "name": a.name, "category": a.category,
        "dept": a.dept, "location": a.location,
        "asset_account_id": a.asset_account_id, "asset_account": nm(a.asset_account_id),
        "dep_account_id": a.dep_account_id, "dep_account": nm(a.dep_account_id),
        "expense_account_id": a.expense_account_id, "expense_account": nm(a.expense_account_id),
        "original_value": from_cents(a.original_value),
        "salvage_rate": a.salvage_rate, "salvage": from_cents(salvage),
        "useful_months": a.useful_months, "method": a.method,
        "method_label": METHOD_LABEL.get(a.method, a.method),
        "monthly_dep": from_cents(sch[0] if sch else 0),
        "in_service_date": a.in_service_date.isoformat(),
        "dep_start_period": a.dep_start_period,
        "accum_dep": from_cents(a.accum_dep),
        "net_value": from_cents(a.original_value - a.accum_dep),
        "depreciable_total": from_cents(total),
        "depreciable_left": from_cents(left),
        "progress": round(a.accum_dep * 100 / total, 1) if total else 0,
        "status": a.status, "disposed_period": a.disposed_period,
        "dispose_income": from_cents(a.dispose_income),
        "remark": a.remark,
    }


class AssetIn(BaseModel):
    code: str = ""
    name: str
    category: str = "电子设备"
    dept: str = ""
    location: str = ""
    asset_account_id: int | None = None
    dep_account_id: int | None = None
    expense_account_id: int | None = None
    original_value: float
    salvage_rate: int = 500
    useful_months: int = 60
    method: str = "straight"
    in_service_date: str
    dep_start_period: str | None = None
    status: str = "in_use"
    remark: str = ""


@router.get("/assets")
def list_assets(status: str | None = None, db=Depends(get_db), user=Depends(current_user)):
    accs = {a.id: a for a in db.scalars(select(Account)).all()}
    stmt = select(Asset).order_by(Asset.code)
    if status:
        stmt = stmt.where(Asset.status == status)
    return [asset_out(a, accs) for a in db.scalars(stmt).all()]


@router.get("/assets/meta")
def asset_meta(db=Depends(get_db), user=Depends(current_user)):
    accs = {a.id: a for a in db.scalars(select(Account)).all()}
    cats = []
    for c in ASSET_CATEGORIES:
        ids = asset_engine.resolve_accounts(db, c)
        cats.append({
            "category": c,
            "asset_account_id": ids[0], "dep_account_id": ids[1], "expense_account_id": ids[2],
            "asset_account": f"{accs[ids[0]].code} {accs[ids[0]].name}" if ids[0] and ids[0] in accs else "",
            "dep_account": f"{accs[ids[1]].code} {accs[ids[1]].name}" if ids[1] and ids[1] in accs else "",
            "expense_account": f"{accs[ids[2]].code} {accs[ids[2]].name}" if ids[2] and ids[2] in accs else "",
        })
    # 折旧费用可选的明细科目
    expense = [
        {"id": a.id, "label": f"{a.code} {a.name}"}
        for a in accs.values()
        if a.is_leaf and a.enabled and (a.code.startswith("5602") or a.code.startswith("5601") or a.code.startswith("5101"))
    ]
    expense.sort(key=lambda x: x["label"])
    return {"categories": cats, "methods": [{"value": k, "label": v} for k, v in METHOD_LABEL.items()],
            "expense_accounts": expense}


@router.post("/assets")
def create_asset(body: AssetIn, db=Depends(get_db), user=Depends(current_user)):
    _validate_asset_method(body.method, body.useful_months)
    if db.scalar(select(Asset).where(Asset.name == body.name, Asset.status != "disposed")):
        pass  # 同名资产允许（如同一型号多台），不做唯一校验
    defaults = asset_engine.resolve_accounts(db, body.category)
    d = date.fromisoformat(body.in_service_date)
    code = body.code or f"FA{int(datetime.now().timestamp()) % 1000000:06d}"
    if db.scalar(select(Asset).where(Asset.code == code)):
        raise HTTPException(400, f"资产编码 {code} 已存在")
    a = Asset(
        code=code, name=body.name, category=body.category, dept=body.dept, location=body.location,
        asset_account_id=body.asset_account_id or defaults[0],
        dep_account_id=body.dep_account_id or defaults[1],
        expense_account_id=body.expense_account_id or defaults[2],
        original_value=to_cents(body.original_value),
        salvage_rate=body.salvage_rate, useful_months=body.useful_months, method=body.method,
        in_service_date=d,
        dep_start_period=body.dep_start_period or asset_engine.next_dep_start(d),
        status=body.status, remark=body.remark,
    )
    if not all([a.asset_account_id, a.dep_account_id, a.expense_account_id]):
        raise HTTPException(400, "缺少默认科目，请手工指定固定资产/累计折旧/折旧费用科目")
    db.add(a)
    log(db, user, "asset.create", a.name)
    db.commit()
    return asset_out(a, {x.id: x for x in db.scalars(select(Account)).all()})


@router.put("/assets/{aid}")
def update_asset(aid: int, body: AssetIn, db=Depends(get_db), user=Depends(current_user)):
    _validate_asset_method(body.method, body.useful_months)
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "资产不存在")
    if a.accum_dep > 0 and (to_cents(body.original_value) != a.original_value
                            or body.useful_months != a.useful_months
                            or body.salvage_rate != a.salvage_rate):
        raise HTTPException(400, "已计提折旧的资产不能改原值/年限/残值率，请先撤销折旧")
    a.name = body.name
    a.category = body.category
    a.dept = body.dept
    a.location = body.location
    a.asset_account_id = body.asset_account_id or a.asset_account_id
    a.dep_account_id = body.dep_account_id or a.dep_account_id
    a.expense_account_id = body.expense_account_id or a.expense_account_id
    a.original_value = to_cents(body.original_value)
    a.salvage_rate = body.salvage_rate
    a.useful_months = body.useful_months
    a.method = body.method
    a.in_service_date = date.fromisoformat(body.in_service_date)
    a.dep_start_period = body.dep_start_period or a.dep_start_period
    a.status = body.status
    a.remark = body.remark
    log(db, user, "asset.update", a.name)
    db.commit()
    return asset_out(a, {x.id: x for x in db.scalars(select(Account)).all()})


@router.get("/assets/summary")
def asset_summary(period: str, db=Depends(get_db), user=Depends(current_user)):
    s = asset_engine.asset_summary(db, period)
    for c in s["by_category"]:
        for f in ("original", "accum", "net"):
            c[f] = from_cents(c[f])
    for f in ("original", "accum", "net"):
        s["total"][f] = from_cents(s["total"][f])
    return s


@router.get("/assets/depreciation")
def depreciation_preview(period: str, db=Depends(get_db), user=Depends(current_user)):
    r = asset_engine.preview(db, period)
    for row in r["rows"]:
        for f in ("original_value", "accum_dep", "net_value", "amount"):
            row[f] = from_cents(row[f])
    r["total"] = from_cents(r["total"])
    r["posted_total"] = from_cents(r["posted_total"])
    return r


@router.get("/assets/depreciation/calculate")
def calculate_depreciation(
    original_value: float,
    useful_months: int = 60,
    salvage_rate: int = 500,
    method: str = "straight",
    in_service_date: str = "",
    dep_start_period: str | None = None,
    accum_dep: float = 0,
    current_period: str | None = None,
    db=Depends(get_db),
    user=Depends(current_user),
):
    """单资产折旧测算，不写资产卡片、不生成凭证。"""
    _validate_asset_method(method, useful_months)
    if salvage_rate < 0 or salvage_rate > 10000:
        raise HTTPException(400, "残值率必须在 0% 到 100% 之间")
    try:
        service_date = date.fromisoformat(in_service_date)
    except ValueError:
        raise HTTPException(400, "投入使用日期格式无效")
    start_period = dep_start_period or asset_engine.next_dep_start(service_date)
    if len(start_period) != 7 or start_period[4] != "-":
        raise HTTPException(400, "折旧起始期间格式应为 YYYY-MM")

    original = max(to_cents(original_value), 0)
    accum_in = max(to_cents(accum_dep), 0)
    schedule = asset_engine.schedule_for_values(original, salvage_rate, useful_months, method)
    salvage = round(original * salvage_rate / 10000)
    depreciable = max(original - salvage, 0)
    accum_in = min(accum_in, depreciable)
    rows, running = [], 0
    for seq, amount in enumerate(schedule, 1):
        running += amount
        rows.append({
            "seq": seq,
            "period": period_add_local(start_period, seq - 1),
            "amount": from_cents(amount),
            "accum": from_cents(running),
            "net": from_cents(original - running),
        })
    current_amount = 0
    if current_period and current_period >= start_period:
        idx = asset_engine.periods_between(start_period, current_period) - 1
        if 0 <= idx < len(schedule):
            current_amount = schedule[idx]
    return {
        "summary": {
            "original_value": from_cents(original),
            "salvage_rate": salvage_rate,
            "salvage": from_cents(salvage),
            "depreciable_total": from_cents(depreciable),
            "useful_months": useful_months,
            "method": method,
            "method_label": METHOD_LABEL[method],
            "in_service_date": service_date.isoformat(),
            "dep_start_period": start_period,
            "monthly_dep": from_cents(schedule[0] if schedule else 0),
            "current_period": current_period or "",
            "current_period_amount": from_cents(current_amount),
            "accum_dep": from_cents(accum_in),
            "net_value": from_cents(original - accum_in),
        },
        "rows": rows,
    }


@router.post("/assets/depreciation")
def post_depreciation(period: str, db=Depends(get_db), user=Depends(current_user)):
    check_period_open(db, period)
    try:
        r = asset_engine.post_depreciation(db, period, user["username"], _next_seq)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, user, "asset.depreciation", period, f"{r['total']/100:,.2f}")
    r["total"] = from_cents(r["total"])
    return r


@router.delete("/assets/depreciation")
def unpost_depreciation(period: str, db=Depends(get_db), user=Depends(current_user)):
    check_period_open(db, period)
    try:
        r = asset_engine.unpost_depreciation(db, period)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, user, "asset.depreciation.undo", period)
    return r


@router.delete("/assets/{aid}")
def delete_asset(aid: int, db=Depends(get_db), user=Depends(current_user)):
    """注意：必须注册在 /assets/depreciation 之后，否则 depreciation 会被当成 aid。"""
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "资产不存在")
    if db.scalar(select(func.count()).select_from(AssetDep).where(AssetDep.asset_id == aid)):
        raise HTTPException(400, "该资产已有折旧记录，不能删除；如不再使用请做处置")
    db.delete(a)
    log(db, user, "asset.delete", a.name)
    db.commit()
    return {"ok": True}


@router.get("/assets/{aid}/schedule")
def asset_schedule(aid: int, db=Depends(get_db), user=Depends(current_user)):
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "资产不存在")
    sch = asset_engine.schedule(a)
    rows, accum = [], 0
    for i, amt in enumerate(sch, 1):
        accum += amt
        rows.append({
            "seq": i,
            "period": period_add_local(a.dep_start_period, i - 1),
            "amount": from_cents(amt),
            "accum": from_cents(accum),
            "net": from_cents(a.original_value - accum),
        })
    return {"asset": asset_out(a), "rows": rows}


def period_add_local(code: str, n: int) -> str:
    y, m = int(code[:4]), int(code[5:7])
    total = y * 12 + (m - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


class CapitalizeIn(BaseModel):
    credit_account_id: int
    date: str | None = None
    tax_rate: int = 0
    note: str = ""


@router.post("/assets/{aid}/capitalize")
def capitalize_asset(aid: int, body: CapitalizeIn, db=Depends(get_db), user=Depends(current_user)):
    """生成固定资产入账凭证：借 固定资产 / 贷 银行存款（或应付账款）。"""
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "资产不存在")
    period = period_of(body.date or a.in_service_date)
    check_period_open(db, period)
    lines = [
        {"account_id": a.asset_account_id, "debit": a.original_value, "credit": 0,
         "summary": f"购入 {a.name}", "tax_rate": body.tax_rate},
        {"account_id": body.credit_account_id, "debit": 0, "credit": a.original_value,
         "summary": f"购入 {a.name}"},
    ]
    ok, msg = calc.validate_voucher_lines(lines)
    if not ok:
        raise HTTPException(400, msg)
    v = _system_voucher(db, period, date.fromisoformat(body.date or a.in_service_date.isoformat()),
                        f"固定资产入账 {a.name}", "asset", user["username"], lines)
    a.voucher_id = v.id
    db.commit()
    log(db, user, "asset.capitalize", a.name)
    return {"voucher_id": v.id, "voucher_no": f"{v.word}-{v.seq:04d}"}


class DisposeIn(BaseModel):
    period: str
    income: float = 0
    tax_rate: int = 1300
    date: str | None = None


@router.post("/assets/{aid}/dispose")
def dispose_asset(aid: int, body: DisposeIn, db=Depends(get_db), user=Depends(current_user)):
    """资产处置：
    1. 转出原值与累计折旧到固定资产清理
    2. 收到处置价款（含税，拆销项）
    3. 清理余额转营业外收入/支出
    """
    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404, "资产不存在")
    if a.status == "disposed":
        raise HTTPException(400, "该资产已处置")
    check_period_open(db, body.period)

    clear = db.scalar(select(Account).where(Account.code == "1606"))
    if not clear:
        raise HTTPException(500, "缺少 1606 固定资产清理 科目")
    bank = db.scalar(select(Account).where(Account.code == "1002.01"))
    income_gain = db.scalar(select(Account).where(Account.code == "5301"))
    income_loss = db.scalar(select(Account).where(Account.code == "5711"))
    out_tax = db.scalar(select(Account).where(Account.code == "2221.01.02"))
    if not all([bank, income_gain, income_loss, out_tax]):
        raise HTTPException(500, "处置所需科目缺失")

    income = to_cents(body.income)
    tax = round(income / (10000 + body.tax_rate) * body.tax_rate) if body.tax_rate else 0
    net = income - tax
    book_value = a.original_value - a.accum_dep

    vd = date.fromisoformat(body.date) if body.date else date.fromisoformat(body.period + "-01")
    lines = [
        {"account_id": clear.id, "debit": a.accum_dep, "credit": 0, "summary": f"处置 {a.name}：转出累计折旧"},
        {"account_id": clear.id, "debit": book_value, "credit": 0, "summary": f"处置 {a.name}：转出净值"},
        {"account_id": a.asset_account_id, "debit": 0, "credit": a.original_value, "summary": f"处置 {a.name}：转出原值"},
    ]
    if income:
        lines.append({"account_id": bank.id, "debit": income, "credit": 0, "summary": f"处置 {a.name}：收到价款"})
        if tax:
            lines.append({"account_id": out_tax.id, "debit": 0, "credit": tax,
                          "summary": f"处置 {a.name}：销项税额", "tax_rate": body.tax_rate})
        lines.append({"account_id": clear.id, "debit": 0, "credit": net, "summary": f"处置 {a.name}：清理收入"})
    gain = net - book_value
    if gain > 0:
        lines.append({"account_id": clear.id, "debit": 0, "credit": gain, "summary": f"处置 {a.name}：清理净收益"})
        lines.append({"account_id": income_gain.id, "debit": 0, "credit": gain, "summary": f"处置 {a.name}：转营业外收入"})
    elif gain < 0:
        lines.append({"account_id": clear.id, "debit": -gain, "credit": 0, "summary": f"处置 {a.name}：清理净损失"})
        lines.append({"account_id": income_loss.id, "debit": -gain, "credit": 0, "summary": f"处置 {a.name}：转营业外支出"})

    ok, msg = calc.validate_voucher_lines(lines)
    if not ok:
        raise HTTPException(400, f"处置分录不平：{msg}")

    v = _system_voucher(db, body.period, vd, f"固定资产处置 {a.name}", "dispose", user["username"], lines)
    a.status = "disposed"
    a.disposed_period = body.period
    a.dispose_income = income
    db.commit()
    log(db, user, "asset.dispose", a.name, f"价款 {from_cents(income):,.2f}")
    return {"voucher_id": v.id, "voucher_no": f"{v.word}-{v.seq:04d}", "gain": from_cents(gain), "tax": from_cents(tax)}


# ================================================================== 增值税
def _vat_row(db, period: str) -> VatLedger | None:
    return db.scalar(select(VatLedger).where(VatLedger.period == period))


@router.get("/vat/book")
def vat_book(period: str, db=Depends(get_db), user=Depends(current_user)):
    row = _vat_row(db, period)
    b = vat_engine.book(
        db, period,
        adjust_output=row.adjust_output_tax if row else 0,
        adjust_input=row.adjust_input_tax if row else 0,
    )
    for k in ("output_tax", "input_tax", "transfer_out", "deductible", "opening_credit",
              "opening_payable", "payable", "closing_credit", "paid", "unpaid",
              "total_sales", "book_payable", "book_credit", "diff"):
        b[k] = from_cents(b[k])
    for x in b["output_by_rate"]:
        x["sales"] = from_cents(x["sales"])
        x["tax"] = from_cents(x["tax"])
    for x in b["input_by_rate"]:
        x["amount"] = from_cents(x["amount"])
    b["lines"] = {k: from_cents(v) for k, v in b["lines"].items()}
    for x in b["surtax"]:
        x["amount"] = from_cents(x["amount"])
    b["saved"] = {
        "adjust_output_tax": from_cents(row.adjust_output_tax) if row else 0,
        "adjust_input_tax": from_cents(row.adjust_input_tax) if row else 0,
        "adjust_note": row.adjust_note if row else "",
        "paid_tax": from_cents(row.paid_tax) if row else 0,
        "confirmed": bool(row.confirmed) if row else False,
    }
    return b


class VatAdjustIn(BaseModel):
    adjust_output_tax: float = 0
    adjust_input_tax: float = 0
    adjust_note: str = ""
    paid_tax: float = 0
    confirmed: bool = False


@router.put("/vat/book")
def save_vat_book(period: str, body: VatAdjustIn, db=Depends(get_db), user=Depends(current_user)):
    row = _vat_row(db, period)
    if not row:
        row = VatLedger(period=period)
        db.add(row)
    row.adjust_output_tax = to_cents(body.adjust_output_tax)
    row.adjust_input_tax = to_cents(body.adjust_input_tax)
    row.adjust_note = body.adjust_note
    row.paid_tax = to_cents(body.paid_tax)
    row.confirmed = body.confirmed
    if body.confirmed:
        row.confirmed_at = datetime.now()
    log(db, user, "vat.save", period)
    db.commit()
    return {"ok": True}


@router.get("/vat/table")
def vat_table(year: int, db=Depends(get_db), user=Depends(current_user)):
    t = vat_engine.table(db, year)
    for r in t["rows"]:
        for f in ("sales", "output", "input", "transfer_out", "payable", "closing_credit", "paid", "unpaid"):
            r[f] = from_cents(r[f])
    t["total"] = {k: from_cents(v) for k, v in t["total"].items()}
    return t


@router.get("/vat/declaration")
def vat_declaration(period: str, db=Depends(get_db), user=Depends(current_user)):
    row = _vat_row(db, period)
    d = vat_engine.declaration(db, period)
    if row:
        # 手工调整已并入 book()，这里只补展示
        pass
    for m in d["main"]:
        m["value"] = from_cents(m["value"])
    for x in d["sheet1"]:
        x["sales"] = from_cents(x["sales"])
        x["tax"] = from_cents(x["tax"])
    for x in d["sheet2"]["detail"]:
        x["amount"] = from_cents(x["amount"])
    d["sheet2"]["transfer_out"] = from_cents(d["sheet2"]["transfer_out"])
    d["sheet2"]["total"] = from_cents(d["sheet2"]["total"])
    for x in d["surtax"]:
        x["amount"] = from_cents(x["amount"])
    d["summary"] = {k: from_cents(v) for k, v in d["summary"].items()}
    return d


@router.get("/vat/check")
def vat_check(period: str, db=Depends(get_db), user=Depends(current_user)):
    return vat_engine.check(db, period)


class SurtaxConfirmIn(BaseModel):
    period: str
    source: str = "transfer"
    manual_base: float | None = None
    small_micro: bool = False
    city_rate: int = 700
    education_rate: int = 300
    local_education_rate: int = 200
    note: str = ""


def _surtax_account_snapshot(
    db,
    period: str,
    source: str = "transfer",
    manual_base: float | None = None,
    small_micro: bool = False,
    city_rate: int = 700,
    education_rate: int = 300,
    local_education_rate: int = 200,
):
    sources = vat_engine.surtax_source_amounts(db, period)
    selected = next((x for x in sources if x["value"] == source), None)
    if manual_base is not None:
        chosen = {
            "value": "manual",
            "label": "手工调整计税依据",
            "account_code": "",
            "amount": to_cents(manual_base),
            "available": True,
            "description": "按人工核对后的金额计算",
        }
    else:
        if source not in ("transfer", "vat_book", "auto"):
            raise HTTPException(400, "附加税计税依据来源无效")
        if source == "auto":
            chosen = next((x for x in sources if x["available"]), sources[0])
        else:
            if selected is None:
                selected = next((x for x in sources if x["value"] == source), sources[0])
            chosen = selected
    if not (0 <= city_rate <= 10000 and 0 <= education_rate <= 10000 and 0 <= local_education_rate <= 10000):
        raise HTTPException(400, "附加税税率必须在 0% 到 100% 之间")
    result = vat_engine.calculate_surtax(
        chosen["amount"],
        small_micro=small_micro,
        city_rate=city_rate,
        education_rate=education_rate,
        local_education_rate=local_education_rate,
    )
    return sources, chosen, result


def _surtax_preview_out(db, period: str, sources, chosen, result):
    existing_voucher = db.scalar(
        select(Voucher).where(Voucher.period == period, Voucher.source == "surtax", Voucher.status != "void")
    )
    saved = db.scalar(select(SurtaxCalc).where(SurtaxCalc.period == period))
    period_row = db.scalar(select(Period).where(Period.code == period))
    source_rows = []
    for x in sources:
        source_rows.append({**x, "amount": from_cents(x["amount"])})
    items = [{**x, "rate": x["rate"], "gross": from_cents(x["gross"]),
              "reduction": from_cents(x["reduction"]), "net": from_cents(x["net"])}
             for x in result["items"]]
    return {
        "period": period,
        "period_status": period_row.status if period_row else "missing",
        "sources": source_rows,
        "selected_source": chosen["value"],
        "selected_source_label": chosen["label"],
        "selected_account_code": chosen.get("account_code", ""),
        "base_amount": from_cents(result["base"]),
        "small_micro": result["small_micro"],
        "reduction_rate": result["reduction_rate"],
        "items": items,
        "gross_total": from_cents(result["gross_total"]),
        "reduction_total": from_cents(result["reduction_total"]),
        "net_total": from_cents(result["net_total"]),
        "existing_voucher": (
            {"id": existing_voucher.id, "voucher_no": f"{existing_voucher.word}-{existing_voucher.seq:04d}"}
            if existing_voucher else None
        ),
        "saved_confirmed": bool(saved and saved.confirmed),
        "saved_calc": (
            {
                "id": saved.id,
                "base_amount": from_cents(saved.base_amount),
                "small_micro": saved.small_micro,
                "net_amount": from_cents(saved.net_amount),
                "voucher_id": saved.voucher_id,
            }
            if saved else None
        ),
        "can_generate": bool(result["net_total"] > 0 and not existing_voucher and period_row and period_row.status == "open"),
    }


@router.get("/vat/surtax/preview")
def surtax_preview(
    period: str,
    source: str = "transfer",
    manual_base: float | None = None,
    small_micro: bool = False,
    city_rate: int = 700,
    education_rate: int = 300,
    local_education_rate: int = 200,
    db=Depends(get_db),
    user=Depends(current_user),
):
    sources, chosen, result = _surtax_account_snapshot(
        db, period, source, manual_base, small_micro, city_rate, education_rate, local_education_rate
    )
    return _surtax_preview_out(db, period, sources, chosen, result)


@router.post("/vat/surtax/confirm")
def confirm_surtax(body: SurtaxConfirmIn, db=Depends(get_db), user=Depends(current_user)):
    check_period_open(db, body.period)
    existing = db.scalar(
        select(Voucher).where(
            Voucher.period == body.period, Voucher.source == "surtax", Voucher.status != "void"
        )
    )
    if existing:
        raise HTTPException(400, "本期已生成过附加税费凭证，不能重新确认")
    sources, chosen, result = _surtax_account_snapshot(
        db, body.period, body.source, body.manual_base, body.small_micro,
        body.city_rate, body.education_rate, body.local_education_rate,
    )
    if result["base"] <= 0 or result["net_total"] <= 0:
        raise HTTPException(400, "本期计税依据为零，无需确认附加税")
    row = db.scalar(select(SurtaxCalc).where(SurtaxCalc.period == body.period))
    if row is None:
        row = SurtaxCalc(period=body.period)
        db.add(row)
    row.source = chosen["value"]
    row.source_account_code = chosen.get("account_code", "")
    row.base_amount = result["base"]
    row.small_micro = body.small_micro
    row.city_rate = body.city_rate
    row.education_rate = body.education_rate
    row.local_education_rate = body.local_education_rate
    row.reduction_rate = result["reduction_rate"]
    row.gross_amount = result["gross_total"]
    row.reduction_amount = result["reduction_total"]
    row.net_amount = result["net_total"]
    row.note = body.note[:200]
    row.confirmed = True
    row.confirmed_by = user["username"]
    row.confirmed_at = datetime.now()
    row.voucher_id = None
    log(db, user, "vat.surtax.confirm", body.period,
        f"计税依据 {from_cents(row.base_amount):,.2f}，实际附加税 {from_cents(row.net_amount):,.2f}")
    db.commit()
    return _surtax_preview_out(db, body.period, sources, chosen, result)


def _generate_surtax_voucher(db, period: str, user: dict):
    check_period_open(db, period)
    existing = db.scalar(
        select(Voucher).where(Voucher.period == period, Voucher.source == "surtax", Voucher.status != "void")
    )
    if existing:
        raise HTTPException(400, "本期已生成过附加税费凭证")
    row = db.scalar(select(SurtaxCalc).where(SurtaxCalc.period == period))
    if row is None or not row.confirmed:
        raise HTTPException(400, "请先确认附加税测算结果")
    result = vat_engine.calculate_surtax(
        row.base_amount,
        small_micro=row.small_micro,
        city_rate=row.city_rate,
        education_rate=row.education_rate,
        local_education_rate=row.local_education_rate,
        reduction_rate=row.reduction_rate,
    )
    if result["net_total"] != row.net_amount:
        raise HTTPException(409, "附加税测算快照与当前金额不一致，请重新确认")
    lines = []
    for item in result["items"]:
        if item["net"] <= 0:
            continue
        expense = db.scalar(select(Account).where(Account.code == item["expense_code"]))
        payable = db.scalar(select(Account).where(Account.code == item["account_code"]))
        if not expense or not payable:
            raise HTTPException(500, f"缺少附加税科目 {item['expense_code']} / {item['account_code']}")
        suffix = "（小微减半）" if row.small_micro else ""
        lines.append({"account_id": expense.id, "debit": item["net"], "credit": 0,
                      "summary": f"计提{item['name']}{suffix}"})
        lines.append({"account_id": payable.id, "debit": 0, "credit": item["net"],
                      "summary": f"计提{item['name']}{suffix}"})
    ok, msg = calc.validate_voucher_lines(lines)
    if not ok:
        raise HTTPException(400, msg)
    v = _system_voucher(
        db, period, date.fromisoformat(period + "-01"),
        f"计提 {period} 附加税费" + ("（小微减半）" if row.small_micro else ""),
        "surtax", user["username"], lines,
    )
    row.voucher_id = v.id
    log(db, user, "vat.surtax", period,
        f"法定 {from_cents(row.gross_amount):,.2f}，减免 {from_cents(row.reduction_amount):,.2f}，"
        f"实际 {from_cents(row.net_amount):,.2f}")
    db.commit()
    return {"voucher_id": v.id, "voucher_no": f"{v.word}-{v.seq:04d}",
            "gross_total": from_cents(row.gross_amount),
            "reduction_total": from_cents(row.reduction_amount),
            "total": from_cents(row.net_amount)}


@router.post("/vat/surtax/voucher")
def create_surtax_voucher(period: str, db=Depends(get_db), user=Depends(current_user)):
    return _generate_surtax_voucher(db, period, user)


@router.post("/vat/surtax")
def post_surtax(period: str, db=Depends(get_db), user=Depends(current_user)):
    """兼容旧入口，但同样要求先完成测算确认。"""
    return _generate_surtax_voucher(db, period, user)
