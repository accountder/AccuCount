"""凭证快速新增客户 / 供应商明细科目自测。"""
import os
import tempfile

os.environ["ACCUCOUNT_DATA"] = tempfile.mkdtemp(prefix="accucount-quick-account-")

from app.api import QuickChildAccountIn, create_quick_child_account  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.seed import seed  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
seed(db)
user = {"id": 1, "username": "admin"}

c1 = create_quick_child_account(QuickChildAccountIn(parent_code="1122", name="杭州测试客户"), db, user)
c2 = create_quick_child_account(QuickChildAccountIn(parent_code="1122", name="宁波测试客户"), db, user)
s1 = create_quick_child_account(QuickChildAccountIn(parent_code="2202", name="苏州测试供应商", tax_rate=900), db, user)

assert c1["account"]["code"] == "1122.01"
assert c2["account"]["code"] == "1122.02"
assert s1["account"]["code"] == "2202.01"
assert c1["partner"]["ptype"] == "customer"
assert s1["partner"]["ptype"] == "supplier"
assert s1["partner"]["tax_rate"] == 900

try:
    create_quick_child_account(QuickChildAccountIn(parent_code="1122", name="杭州测试客户"), db, user)
    raise AssertionError("重复名称未被拦截")
except Exception as exc:
    assert exc.__class__.__name__ == "HTTPException"

db.close()
print("快速新增往来科目测试全部通过")
