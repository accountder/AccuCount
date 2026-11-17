"""账套备份：生成可下载的一致性强备份。

SQLite 直接复制正在使用的 .db 文件可能漏掉 WAL 中的数据，因此这里使用
sqlite3 的在线 backup API 生成快照，再把数据库与签名密钥一起打包。
"""
import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .db import DATA_DIR, DB_PATH, DB_URL


def _sqlite_path() -> Path:
    if not DB_URL.startswith("sqlite:///"):
        raise ValueError("当前部署不是 SQLite，请使用数据库自带备份工具")
    return DB_PATH


def create_backup_zip() -> bytes:
    db_path = _sqlite_path()
    if not db_path.exists():
        raise FileNotFoundError("账套数据库不存在")

    now = datetime.now()
    with tempfile.TemporaryDirectory(prefix="accucount-backup-") as td:
        snapshot = Path(td) / "accucount.db"
        source = sqlite3.connect(str(db_path))
        try:
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()

        manifest = {
            "product": "AccuCount",
            "version": "0.2.2",
            "created_at": now.isoformat(timespec="seconds"),
            "database": "accucount.db",
            "note": "恢复时请先停止服务并备份现有 data 目录，再用本文件替换 cnerp.db。",
        }
        readme = """AccuCount 账套备份

1. 备份包内 accucount.db 是生成时刻的一致性数据库快照。
2. .secret 用于登录令牌签名，恢复时应与新数据库一起放回 data 目录。
3. 恢复前请停止 AccuCount 服务，并先备份现有 data 目录。
4. 将 accucount.db 重命名为 cnerp.db，替换 data 目录中的旧文件。
5. 启动服务后检查登录、凭证数量与报表是否正常。
"""

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, "accucount.db")
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("RESTORE.txt", readme)
            secret = DATA_DIR / ".secret"
            if secret.exists():
                zf.write(secret, ".secret")
        return out.getvalue()
