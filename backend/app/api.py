"""HTTP 接口。金额进出边界处换算：库里是分，接口是元。"""
import calendar
from datetime import date, datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update

from . import calc
from . import asset as asset_engine
from . import export as xlsx
from . import partner as partner_engine
from . import vat as vat_engine
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
    Partner,
    Period,
    ReportTemplate,
    Settle,
    User,
    VatLedger,
    Voucher,
    VoucherLine,
)
from .util import from_cents, period_of, to_cents

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
    log(db, user, "voucher.delete", v.id, f"{v.word}-{v.seq:04d}")
    db.delete(v)
    db.commit()
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

    pending = db.scalar(
        select(func.count()).select_from(Voucher)
        .where(Voucher.period == code, Voucher.status == "draft", Voucher.source != "carryover")
    ) or 0
    if pending:
        raise HTTPException(400, f"本期还有 {pending} 张未审核凭证，不能结账")

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

    p.status = "closed"
    p.closed_at = datetime.now()
    log(db, user, "period.close", code, f"carryover={carryover_no or '-'}")
    db.commit()
    return {
        "ok": True,
        "code": code,
        "status": p.status,
        "carryover_no": carryover_no,
        "profit": profit,
    }


@router.post("/periods/{code}/reopen")
def reopen_period(code: str, db=Depends(get_db), user=Depends(require_role("admin"))):
    p = db.scalar(select(Period).where(Period.code == code))
    if not p:
        raise HTTPException(404, "期间不存在")
    if p.status != "closed":
        raise HTTPException(400, "该期间未结账")
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
    expense = book.side_amount("period", ["5403", "5601", "5602", "5603"])
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


@router.post("/vat/surtax")
def post_surtax(period: str, db=Depends(get_db), user=Depends(current_user)):
    check_period_open(db, period)
    exists = db.scalar(
        select(Voucher).where(Voucher.period == period, Voucher.source == "surtax", Voucher.status != "void")
    )
    if exists:
        raise HTTPException(400, "本期已生成过附加税费凭证")
    lines, total = vat_engine.surtax_voucher_lines(db, period)
    if not lines:
        raise HTTPException(400, "本期无应纳增值税，无需计提附加税费")
    ok, msg = calc.validate_voucher_lines(lines)
    if not ok:
        raise HTTPException(400, msg)
    v = _system_voucher(db, period, date.fromisoformat(period + "-01"),
                        f"计提 {period} 附加税费", "surtax", user["username"], lines)
    db.commit()
    log(db, user, "vat.surtax", period, f"{from_cents(total):,.2f}")
    return {"voucher_id": v.id, "voucher_no": f"{v.word}-{v.seq:04d}", "total": from_cents(total)}
