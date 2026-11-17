import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  App as AntApp,
  Avatar,
  Button,
  Drawer,
  Dropdown,
  Empty,
  Grid,
  Layout,
  Menu,
  Select,
  Spin,
  Tag,
} from 'antd'
import {
  AccountBookOutlined,
  AuditOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
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
  {
    type: 'group',
    label: '工作台',
    children: [
      { key: '/', icon: <DashboardOutlined />, label: '概览' },
      { key: '/vouchers', icon: <FileTextOutlined />, label: '凭证' },
    ],
  },
  {
    type: 'group',
    label: '业务核算',
    children: [
      { key: '/partners', icon: <TeamOutlined />, label: '往来单位' },
      { key: '/aging', icon: <ClockCircleOutlined />, label: '账龄分析' },
      { key: '/assets', icon: <ProfileOutlined />, label: '固定资产' },
      { key: '/vat', icon: <PercentageOutlined />, label: '增值税' },
    ],
  },
  {
    type: 'group',
    label: '账簿与报表',
    children: [
      { key: '/accounts', icon: <UnorderedListOutlined />, label: '科目表' },
      { key: '/reports/tb', icon: <AccountBookOutlined />, label: '科目余额表' },
      { key: '/reports/bs', icon: <BarChartOutlined />, label: '资产负债表' },
      { key: '/reports/pl', icon: <BarChartOutlined />, label: '利润表' },
    ],
  },
  {
    type: 'group',
    label: '期末与系统',
    children: [
      { key: '/periods', icon: <SettingOutlined />, label: '期末处理' },
    ],
  },
]

const ADMIN_MENU = [
  {
    type: 'group',
    label: '系统管理',
    children: [
      { key: '/users', icon: <UserOutlined />, label: '账号与权限' },
      { key: '/audit-logs', icon: <AuditOutlined />, label: '操作日志' },
    ],
  },
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

function Brand({ collapsed = false, onClose }) {
  return (
    <div className={`brand ${collapsed ? 'is-collapsed' : ''}`}>
      <div className="brand-mark"><AccountBookOutlined /></div>
      {!collapsed ? (
        <div className="brand-copy">
          <strong>AccuCount</strong>
          <span>财务工作台</span>
        </div>
      ) : null}
      {onClose ? (
        <Button className="drawer-close" type="text" icon={<MenuFoldOutlined />} onClick={onClose} />
      ) : null}
    </div>
  )
}

function SideNav({ menuItems, selected, collapsed = false, onNavigate, onClose }) {
  return (
    <div className="side-nav">
      <Brand collapsed={collapsed} onClose={onClose} />
      <Menu
        className="app-menu"
        mode="inline"
        theme="light"
        selectedKeys={[selected]}
        items={menuItems}
        inlineCollapsed={collapsed}
        onClick={onNavigate}
      />
      {!collapsed ? (
        <div className="side-footer">
          <span>小企业会计准则</span>
          <span>v0.2.1</span>
        </div>
      ) : null}
    </div>
  )
}

function Shell({ meta, reloadMeta, user, onLogout, onUserUpdated }) {
  const nav = useNavigate()
  const loc = useLocation()
  const screens = Grid.useBreakpoint()
  const { message } = AntApp.useApp()
  const [pwOpen, setPwOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const isMobile = screens.lg === false

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

  useEffect(() => {
    setDrawerOpen(false)
  }, [loc.pathname])

  useEffect(() => {
    if (screens.xl === false) setCollapsed(true)
    if (screens.xl === true) setCollapsed(false)
  }, [screens.xl])

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
    const keys = menuItems
      .flatMap((g) => g.children || [])
      .map((item) => item.key)
      .filter(Boolean)
      .sort((a, b) => b.length - a.length)
    return keys.find((key) => loc.pathname.startsWith(key)) || '/'
  }, [loc.pathname, menuItems])

  const cur = meta.periods.find((p) => p.code === period)
  const role = ROLE_TAG[user?.role] || { color: 'default', label: user?.role || '用户' }

  const onNavigate = ({ key }) => {
    nav(key)
    setDrawerOpen(false)
  }

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
      <Layout className="app-layout">
        {!isMobile ? (
          <Sider
            className="app-sider"
            width={236}
            collapsedWidth={76}
            collapsed={collapsed}
            trigger={null}
          >
            <SideNav
              menuItems={menuItems}
              selected={selected}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          </Sider>
        ) : null}

        <Layout className="app-main">
          <Header className="app-header">
            <div className="header-left">
              <Button
                className="header-icon-button"
                type="text"
                icon={isMobile ? <MenuOutlined /> : (collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />)}
                onClick={() => (isMobile ? setDrawerOpen(true) : setCollapsed((v) => !v))}
              />
              <div className="company-block">
                <strong>{meta.company?.name || '未建账'}</strong>
                <span className="company-tax">{meta.company?.standard || '财务核算'}{meta.company?.tax_no ? ` · ${meta.company.tax_no}` : ''}</span>
              </div>
            </div>

            <div className="header-actions">
              <div className="period-control">
                <span>会计期间</span>
                <Select
                  value={period}
                  onChange={setPeriod}
                  popupMatchSelectWidth={false}
                  options={meta.periods.map((p) => ({
                    value: p.code,
                    label: (
                      <span className="period-option">
                        {p.code}
                        {p.status === 'closed' ? <Tag>已结</Tag> : null}
                      </span>
                    ),
                  }))}
                />
              </div>
              {cur?.status === 'closed' ? <Tag color="orange" className="header-status">本期已结账</Tag> : null}
              <Dropdown
                placement="bottomRight"
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
                <button type="button" className="user-chip">
                  <Avatar size={30} style={{ background: '#1f4f7a' }}>
                    {(user?.name || user?.username || 'U').slice(0, 1)}
                  </Avatar>
                  <span className="user-copy">
                    <strong>{user?.name || user?.username}</strong>
                    <span>@{user?.username}</span>
                  </span>
                  <Tag color={role.color}>{role.label}</Tag>
                </button>
              </Dropdown>
            </div>
          </Header>

          <Content className="app-content">
            <div className="page-container">
              {user?.role === 'viewer' ? (
                <div className="readonly-banner">
                  <InfoCircleOutlined />
                  <span>当前为只读账号，所有写入操作都会被系统拒绝。</span>
                </div>
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
            </div>
          </Content>
        </Layout>

        <Drawer
          className="mobile-nav-drawer"
          open={isMobile && drawerOpen}
          onClose={() => setDrawerOpen(false)}
          placement="left"
          width={280}
          closable={false}
          styles={{ body: { padding: 0 }, header: { display: 'none' } }}
        >
          <SideNav
            menuItems={menuItems}
            selected={selected}
            onNavigate={onNavigate}
            onClose={() => setDrawerOpen(false)}
          />
        </Drawer>

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
      <div className="loading-screen">
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
