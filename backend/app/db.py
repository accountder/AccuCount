"""数据库连接与基类。

金额一律以「分」为单位存整数，避免浮点误差导致借贷不平。
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ACCUCOUNT_DATA") or os.environ.get("CNERP_DATA") or BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cnerp.db"
DB_URL = os.environ.get("ACCUCOUNT_DB_URL") or os.environ.get("CNERP_DB_URL") or f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, _rec):
    if not DB_URL.startswith("sqlite"):
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=8000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
