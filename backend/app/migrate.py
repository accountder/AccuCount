"""在线结构升级。

生产库里已有账，不能重建表。这里只做两件事：
1. create_all 建出新增的表（对已存在的表是幂等的）；
2. 用 PRAGMA table_info 找缺失的列，补 ALTER TABLE。

每次改模型都要在这里留一条「幂等」的补列语句，这样容器原地重启就能升级。
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import Base, engine
from .models import AgingBand

# (表名, 列名, 列定义) —— 只加列不改列，SQLite 的 ALTER 限制最少
ADD_COLUMNS = [
    ("voucher_line", "tax_rate", "INTEGER DEFAULT 0"),
    ("user", "token_version", "INTEGER DEFAULT 0"),
    ("opening_line", "manual_override", "BOOLEAN DEFAULT 0"),
]

# 账龄区间默认口径（小企业实务常用）
DEFAULT_BANDS = [
    (1, "1 个月以内", 0, 30),
    (2, "1-3 个月", 31, 90),
    (3, "3-6 个月", 91, 180),
    (4, "6-12 个月", 181, 365),
    (5, "1-2 年", 366, 730),
    (6, "2 年以上", 731, 0),
]


def ensure_schema() -> dict:
    Base.metadata.create_all(engine)
    added = []
    with engine.begin() as conn:
        for table, col, ddl in ADD_COLUMNS:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            if existing and col not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                added.append(f"{table}.{col}")
    return {"added_columns": added}


def ensure_defaults(db: Session) -> None:
    """补充系统默认数据：账龄区间。已有则跳过。"""
    if db.query(AgingBand).count() == 0:
        for seq, label, f, t in DEFAULT_BANDS:
            db.add(AgingBand(seq=seq, label=label, days_from=f, days_to=t))
        db.commit()
