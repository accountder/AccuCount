import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Avatar, Dropdown, Empty, Layout, Menu, Select, Spin, Tag, App as AntApp } from 'antd'
import {
  AccountBookOutlined,
  AuditOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  KeyOutlined,
  LogoutOutlined,
  PercentageOutlined,
  ProfileOutlined,
  SettingOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, downloadBlob, errMsg, TOKEN_KEY, USER_KEY } from './api'
import { AppCtx } from './context'
import ChangePasswordModal from './components/ChangePasswordModal'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Accounts from './pages/Accounts'
import Vouchers from './pages/Vouchers'
import VoucherEdit from './pages/VoucherEdit'
import TrialBalance from './pages/TrialBalance'
import BalanceSheet from './pages/BalanceSheet'
import Profit from './pages/Profit'
import Periods from './pages/Periods'
import Partners from './pages/Partners'
import Aging from './pages/Aging'
import Assets from './pages/Assets'
import Vat from './pages/Vat'
import Users from './pages/Users'
import AuditLogs from './pages/AuditLogs'

const { Header, Sider, Content } = Layout

const BASE_MENU = [
  { key: '/', icon: <DashboardOutlined />, label: '概览' },
  { key: '/vouchers', icon: <FileTextOutlined />, label: '凭证' },
  { key: '/accounts', icon: <UnorderedListOutlined />, label: '科目表' },
  { type: 'divider' },
  { key: '/partners', icon: <TeamOutlined />, label: '往来单位' },
  { key: '/aging', icon: <ClockCircleOutlined />, label: '账龄分析' },
  { key: '/assets', icon: <ProfileOutlined />, label: '固定资产' },
  { key: '/vat', icon: <PercentageOutlined />, label: '增值税' },
  { type: 'divider' },
  { key: '/reports/tb', icon: <AccountBookOutlined />, label: '科目余额表' },
  { key: '/reports/bs', icon: <BarChartOutlined />, label: '资产负债表' },
  { key: '/reports/pl', icon: <BarChartOutlined />, label: '利润表' },
  { type: 'divider' },
  { key: '/periods', icon: <SettingOutlined />, label: '期末处理' },
]

const ADMIN_MENU = [
  { type: 'divider' },
  { key: '/users', icon: <UserOutlined />, label: '账号与权限' },
  { key: '/audit-logs', icon: <AuditOutlined />, label: '操作日志' },
]

const ROLE_TAG = {
  admin: { color: 'red', label: '管理员' },
  accountant: { color: 'blue', label: '会计' },
  viewer: { color: 'default', label: '只读' },
}

function pickDefaultPeriod(meta) {
  const today = dayjs().format('YYYY-MM')
  const open = meta.periods.filter((p) => p.status === 'open').map((p) => p.code)
  if (!open.length) return meta.company?.start_period || today
  const past = open.filter((c) => c <= today)
  return past.length ? past[past.length - 1] : open[0]
}

function Shell({ meta, reloadMeta, user, onLogout, onUserUpdated }) {
  const nav = useNavigate()
  const loc = useLocation()
  const { message } = AntApp.useApp()
  const [pwOpen, setPwOpen] = useState(false)

  const menuItems = useMemo(
    () => (user?.role === 'admin' ? [...BASE_MENU, ...ADMIN_MENU] : BASE_MENU),
    [user?.role],
  )

  const [period, setPeriod] = useState(() => {
    const saved = localStorage.getItem('accucount_period') || localStorage.getItem('cnerp_period')
    if (saved && meta.periods.some((p) => p.code === saved)) return saved
    return pickDefaultPeriod(meta)
  })

  useEffect(() => {
    localStorage.setItem('accucount_period', period)
  }, [period])

  const ctx = useMemo(
    () => ({
      meta,
      reloadMeta,
      period,
      setPeriod,
      user,
      readOnly: user?.role === 'viewer',
      onUserUpdated,
    }),
    [meta, reloadMeta, period, user, onUserUpdated],
  )

  const selected = useMemo(() => {
    if (loc.pathname === '/') return '/'
    const hit = menuItems.filter((m) => m.key && m.key !== '/' && loc.pathname.startsWith(m.key))
    return hit.length ? hit[hit.length - 1].key : '/'
  }, [loc.pathname, menuItems])

  const cur = meta.periods.find((p) => p.code === period)
  const role = ROLE_TAG[user?.role] || { color: 'default', label: user?.role || '用户' }

  const onAccountMenu = async ({ key }) => {
    if (key === 'logout') return onLogout()
    if (key === 'password') return setPwOpen(true)
    if (key === 'backup') {
      const hide = message.loading('正在生成账套备份…', 0)
      try {
        const blob = await api.downloadBackup()
        downloadBlob(blob, `accucount-backup-${dayjs().format('YYYYMMDD-HHmmss')}.zip`)
        message.success('备份已下载')
      } catch (e) {
        message.error(errMsg(e, '下载备份失败'))
      } finally {
        hide()
      }
    }
  }

  return (
    <AppCtx.Provider value={ctx}>
      <Layout style={{ minHeight: '100vh' }}>
        <Sider width={198} style={{ background: '#0f2f66' }}>
          <div className="sider-brand">
            <AccountBookOutlined />
            AccuCount
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selected]}
            items={menuItems}
            onClick={({ key }) => nav(key)}
            style={{ background: 'transparent', marginTop: 8, fontSize: 13 }}
          />
          <div className="sider-foot">AccuCount · 小企业会计准则 · v0.2</div>
        </Sider>

        <Layout>
          <Header
            style={{
              background: '#fff',
              height: 56,
              lineHeight: '56px',
              padding: '0 20px',
              display: 'flex',
              alignItems: 'center',
              gap: 16,
              borderBottom: '1px solid #e5e7eb',
            }}
          >
            <div style={{ fontWeight: 600 }}>{meta.company?.name || '未建账'}</div>
            {meta.company?.tax_no ? (
              <span style={{ color: '#9ca3af', fontSize: 12 }}>
                纳税人识别号 {meta.company.tax_no}
              </span>
            ) : null}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 12, color: '#6b7280' }}>会计期间</span>
              <Select
                size="small"
                style={{ width: 132 }}
                value={period}
                onChange={setPeriod}
                options={meta.periods.map((p) => ({
                  value: p.code,
                  label: (
                    <span>
                      {p.code}
                      {p.status === 'closed' ? (
                        <Tag color="default" style={{ marginLeft: 6, transform: 'scale(0.85)' }}>
                          已结
                        </Tag>
                      ) : null}
                    </span>
                  ),
                }))}
              />
              {cur?.status === 'closed' ? <Tag color="orange">本期已结账</Tag> : null}
              <Dropdown
                menu={{
                  items: [
                    { key: 'password', icon: <KeyOutlined />, label: '修改密码' },
                    ...(user?.role === 'admin'
                      ? [{ key: 'backup', icon: <DatabaseOutlined />, label: '下载账套备份' }]
                      : []),
                    { type: 'divider' },
                    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录' },
                  ],
                  onClick: onAccountMenu,
                }}
              >
                <span style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Avatar size={26} style={{ background: '#1b57b0' }}>
                    {(user?.name || user?.username || 'U').slice(0, 1)}
                  </Avatar>
                  <span style={{ fontSize: 13 }}>{user?.name || user?.username}</span>
                  <Tag color={role.color} style={{ marginInlineEnd: 0 }}>{role.label}</Tag>
                </span>
              </Dropdown>
            </div>
          </Header>

          <Content style={{ padding: 16 }}>
            {user?.role === 'viewer' ? (
              <Alert
                banner
                showIcon
                type="info"
                message="当前为只读账号：可以查看凭证、账簿和报表，所有写入操作都会被系统拒绝。"
                style={{ marginBottom: 12 }}
              />
            ) : null}
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/vouchers" element={<Vouchers />} />
              <Route path="/vouchers/new" element={user?.role === 'viewer' ? <Navigate to="/vouchers" replace /> : <VoucherEdit />} />
              <Route path="/vouchers/:id" element={<VoucherEdit />} />
              <Route path="/accounts" element={<Accounts />} />
              <Route path="/partners" element={<Partners />} />
              <Route path="/aging" element={<Aging />} />
              <Route path="/assets" element={<Assets />} />
              <Route path="/vat" element={<Vat />} />
              <Route path="/reports/tb" element={<TrialBalance />} />
              <Route path="/reports/bs" element={<BalanceSheet />} />
              <Route path="/reports/pl" element={<Profit />} />
              <Route path="/periods" element={<Periods />} />
              <Route path="/users" element={user?.role === 'admin' ? <Users /> : <Navigate to="/" replace />} />
              <Route path="/audit-logs" element={user?.role === 'admin' ? <AuditLogs /> : <Navigate to="/" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Content>
        </Layout>
        <ChangePasswordModal
          open={pwOpen}
          onClose={() => setPwOpen(false)}
          onChanged={(data) => onUserUpdated?.(data.user, data.token)}
        />
      </Layout>
    </AppCtx.Provider>
  )
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
    } catch {
      return null
    }
  })
  const [meta, setMeta] = useState(null)
  const [loading, setLoading] = useState(!!localStorage.getItem(TOKEN_KEY))
  const { message } = AntApp.useApp()

  const reloadMeta = useCallback(async () => {
    const m = await api.meta()
    setMeta(m)
    return m
  }, [])

  useEffect(() => {
    if (!token) {
      setLoading(false)
      return
    }
    reloadMeta()
      .catch((e) => {
        if (e.response?.status !== 401) message.error(errMsg(e, '加载基础信息失败'))
      })
      .finally(() => setLoading(false))
  }, [token, reloadMeta, message])

  const applyUser = useCallback((nextUser, nextToken) => {
    if (nextUser) {
      localStorage.setItem(USER_KEY, JSON.stringify(nextUser))
      setUser(nextUser)
    }
    if (nextToken) {
      localStorage.setItem(TOKEN_KEY, nextToken)
      setToken(nextToken)
    }
  }, [])

  const onLogout = () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    localStorage.removeItem('cnerp_token')
    localStorage.removeItem('cnerp_user')
    setToken(null)
    setUser(null)
    setMeta(null)
  }

  if (!token) {
    return (
      <Login
        onOk={(data) => {
          applyUser(data.user, data.token)
          setLoading(true)
        }}
      />
    )
  }

  if (loading || !meta) {
    return (
      <div className="login-wrap" style={{ background: '#f0f2f5' }}>
        <Spin size="large" tip="加载中…">
          <div style={{ width: 200, height: 60 }} />
        </Spin>
      </div>
    )
  }

  if (!meta.company) {
    return <Empty description="尚未建账" />
  }

  return (
    <BrowserRouter>
      <Shell
        meta={meta}
        reloadMeta={reloadMeta}
        user={user}
        onLogout={onLogout}
        onUserUpdated={applyUser}
      />
    </BrowserRouter>
  )
}
