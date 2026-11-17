"""账号、权限、审计日志与备份接口的真实 HTTP 自测。"""
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18093"
TOKEN = None
FAIL = []


def call(method, path, body=None, params=None, token=None, raw=False):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    use_token = TOKEN if token is None else token
    if use_token:
        req.add_header("Authorization", f"Bearer {use_token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read()
            if raw:
                return r.status, content
            return r.status, (json.loads(content.decode()) if content else None)
    except urllib.error.HTTPError as e:
        content = e.read()
        if raw:
            return e.code, content
        try:
            return e.code, json.loads(content.decode())
        except Exception:
            return e.code, content.decode(errors="replace")


def chk(name, cond, detail=""):
    print(f"  {'✓' if cond else '✗'} {name}{'  — ' + str(detail) if detail else ''}", flush=True)
    if not cond:
        FAIL.append(name)


def login(username, password):
    return call("POST", "/api/login", {"username": username, "password": password}, token="")


print(f"目标 {BASE}", flush=True)
code, data = login("admin", "admin123")
chk("初始管理员可登录", code == 200, code)
old_admin = data["token"] if code == 200 else ""

code, data = call("POST", "/api/me/password", {
    "old_password": "admin123", "new_password": "AdminPass123"
}, token=old_admin)
chk("管理员可修改自己的密码", code == 200, code)
new_admin = data.get("token") if isinstance(data, dict) else None
chk("修改密码返回新令牌", bool(new_admin))

code, _ = call("GET", "/api/me", token=old_admin)
chk("修改密码后旧令牌立即失效", code == 401, code)

TOKEN = new_admin
code, me = call("GET", "/api/me")
chk("新令牌可继续使用", code == 200 and me.get("role") == "admin", me)

code, _ = call("POST", "/api/users", {
    "username": "accountant1", "password": "Account123",
    "name": "测试会计", "role": "accountant", "enabled": True,
})
chk("管理员可创建会计", code == 200, code)

code, _ = call("POST", "/api/users", {
    "username": "viewer1", "password": "Viewer123",
    "name": "测试只读", "role": "viewer", "enabled": True,
})
chk("管理员可创建只读账号", code == 200, code)

code, data = login("accountant1", "Account123")
acct_token = data.get("token") if isinstance(data, dict) else None
chk("会计可登录", code == 200, code)

code, _ = call("GET", "/api/accounts", token=acct_token)
chk("会计可查看科目", code == 200, code)
code, _ = call("POST", "/api/accounts", {
    "code": "9999", "name": "权限测试科目", "cat": "asset", "direction": "dr"
}, token=acct_token)
chk("会计可新增业务数据", code == 200, code)
code, _ = call("POST", "/api/users", {
    "username": "hacker", "password": "Hacker123", "role": "admin"
}, token=acct_token)
chk("会计不能管理账号", code == 403, code)

code, data = login("viewer1", "Viewer123")
viewer_token = data.get("token") if isinstance(data, dict) else None
chk("只读账号可登录", code == 200, code)
code, _ = call("GET", "/api/accounts", token=viewer_token)
chk("只读账号可查看科目", code == 200, code)
code, _ = call("POST", "/api/accounts", {
    "code": "9998", "name": "只读越权", "cat": "asset", "direction": "dr"
}, token=viewer_token)
chk("只读账号写接口返回 403", code == 403, code)

code, data = call("POST", "/api/me/password", {
    "old_password": "Viewer123", "new_password": "Viewer456"
}, token=viewer_token)
chk("只读账号可修改自己的密码", code == 200, code)
viewer_new = data.get("token") if isinstance(data, dict) else None
code, _ = call("GET", "/api/me", token=viewer_token)
chk("只读账号改密后旧令牌失效", code == 401, code)
code, _ = call("GET", "/api/me", token=viewer_new)
chk("只读账号改密后新令牌有效", code == 200, code)

code, users = call("GET", "/api/users")
chk("管理员可查看账号列表", code == 200 and len(users) >= 3, len(users) if isinstance(users, list) else users)
admin_id = next((u["id"] for u in users if u["username"] == "admin"), None) if isinstance(users, list) else None
code, _ = call("PUT", f"/api/users/{admin_id}", {"role": "viewer"})
chk("管理员不能降级当前登录账号", code == 400, code)

code, logs = call("GET", "/api/audit-logs", params={"size": 20})
chk("管理员可查看操作日志", code == 200 and logs.get("total", 0) > 0, logs.get("total") if isinstance(logs, dict) else logs)

code, blob = call("GET", "/api/admin/backup", raw=True)
ok_zip = False
if code == 200:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = set(zf.namelist())
            ok_zip = {"accucount.db", "manifest.json", "RESTORE.txt"}.issubset(names)
            if "accucount.db" in names:
                ok_zip = ok_zip and len(zf.read("accucount.db")) > 0
    except Exception:
        ok_zip = False
chk("管理员可下载一致性备份", code == 200 and ok_zip, code)

print("\n结果：", "全部通过" if not FAIL else f"{len(FAIL)} 项失败", flush=True)
if FAIL:
    raise SystemExit(1)
