# AccuCount

![Build and Publish](https://github.com/accountder/AccuCount/actions/workflows/docker-publish.yml/badge.svg)

**AccuCount 是一套面向中国小微企业的轻量财务软件，按《小企业会计准则》完成日常记账、账簿查询、财务报表和期末处理。**

系统适合单公司、单账套使用，覆盖从期初建账、凭证录入到期末结账的完整流程，支持管理员、会计和只读账号协作。

## 主要功能

- **凭证管理**：录入、修改、审核、反审核、作废、删除、复制、批量审核和期间查询；支持往来辅助核算、税率、人民币大写和借贷平衡校验。
- **期初余额**：按资产、负债、所有者权益、成本和损益分类录入；支持客户/供应商明细、负数余额、试算平衡、确认和反确认。
- **账簿与报表**：科目余额表、明细账、资产负债表和利润表；支持 Excel 导出。
- **往来管理**：客户/供应商档案、应收应付台账、FIFO 自动核销、账龄分析和往来对账单。
- **固定资产**：资产卡片、多种折旧方法、批量计提折旧、处置、折旧计划及总账勾稽。
- **税务处理**：增值税销项/进项台账、应纳税额和留抵计算、附加税费、小型微利企业减半政策、季度企业所得税预提。
- **期末处理**：结转未交增值税、计提税金和折旧、往来冲销、结转损益、年度结转及期间锁定。
- **系统管理**：角色权限、操作日志、SQLite 一致性备份、已结账期间保护。

## 快速部署

### 方式一：使用预构建镜像（推荐）

只需要安装 Docker，不需要克隆源码或安装 Python、Node.js。

```bash
curl -LO https://raw.githubusercontent.com/accountder/AccuCount/main/docker-compose.release.yml
docker compose -f docker-compose.release.yml up -d
```

Windows PowerShell：

```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/accountder/AccuCount/main/docker-compose.release.yml" -OutFile "docker-compose.release.yml"
docker compose -f docker-compose.release.yml up -d
```

打开：

```text
http://localhost:8070
```

默认账号：

```text
admin / admin123
```

首次登录后请立即修改密码。

数据保存在当前目录的 `data/`，升级或重建容器时不会丢失。建议定期备份整个 `data/` 目录，或在系统内下载一致性备份 ZIP。

镜像地址：`ghcr.io/accountder/accucount:latest`，当前发布架构为 `linux/amd64`。

### 方式二：从源码构建

```bash
git clone https://github.com/accountder/AccuCount.git
cd AccuCount
docker compose up -d --build
```

## 本地开发

后端：

```bash
cd backend
pip install -r requirements.txt
python run.py
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端开发服务器默认运行在 `http://127.0.0.1:5173`，`/api` 会代理到后端 `8090` 端口。

生产构建：

```bash
cd frontend
npm run build
```

后端启动后会直接托管 `frontend/dist`。

## 测试

```bash
cd backend
python test_engine.py
python test_phase2.py
python test_calculators.py
python test_period_closing.py
python test_income_tax.py
python test_report_netting.py
python test_year_end.py
python test_asset_reconciliation.py
python test_opening_balance.py
```

前端构建验证：

```bash
cd frontend
npm run build
```

## 技术栈

- 后端：FastAPI、SQLAlchemy 2.0、SQLite（WAL）
- 前端：React、Ant Design、Vite
- 部署：Docker、Docker Compose、GitHub Actions、GHCR

## 使用范围

AccuCount 面向中国境内小微企业的日常财务核算，不替代审计、税务鉴证或金税系统。当前版本未内置现金流量表、工资个税、存货成本核算和数电票直连；正式生产使用前，请根据企业会计政策完成科目、税率和期初期末数据核对。
