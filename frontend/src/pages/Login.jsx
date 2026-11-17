import React, { useState } from 'react'
import { App as AntApp, Button, Form, Input } from 'antd'
import {
  AccountBookOutlined,
  LockOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { api, errMsg } from '../api'

export default function Login({ onOk }) {
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  const submit = async (v) => {
    setLoading(true)
    try {
      const data = await api.login(v.username, v.password)
      message.success('登录成功')
      onOk(data)
    } catch (e) {
      message.error(errMsg(e, '登录失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-shell">
      <section className="login-story">
        <div className="login-story-content">
          <div className="login-brand">
            <span className="brand-mark"><AccountBookOutlined /></span>
            AccuCount
          </div>
          <div className="login-eyebrow">小企业财务核算</div>
          <h1 className="login-title">准确入账，<br />让经营更清晰。</h1>
          <p className="login-description">
            面向中国小微企业的轻量财务核算引擎。从复式记账到财务报表，
            把每一笔业务沉淀为可查、可核、可追溯的账务数据。
          </p>
          <div className="login-ledger" aria-label="复式记账平衡示意">
            <div className="login-ledger-head">
              <span>业务事实</span>
              <span>会计表达</span>
            </div>
            <div className="login-ledger-row">
              <span>凭证、账簿与报表</span>
              <strong>逐笔对应</strong>
            </div>
            <div className="login-ledger-row">
              <span>借方发生额</span>
              <strong>1,437,000.00</strong>
            </div>
            <div className="login-ledger-row">
              <span>贷方发生额</span>
              <strong>1,437,000.00</strong>
            </div>
            <div className="login-ledger-balance">
              <span>借贷平衡</span>
              <span>差额 0.00</span>
            </div>
          </div>
        </div>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <div className="login-card-head">
            <div className="login-card-logo"><AccountBookOutlined /></div>
            <div className="brand-title">登录 AccuCount</div>
            <div className="brand-sub">使用你的账套账号进入财务工作台</div>
          </div>

          <Form layout="vertical" onFinish={submit} initialValues={{ username: 'admin' }}>
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input size="large" prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password size="large" prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={loading} style={{ marginTop: 6, height: 44 }}>
              登 录
            </Button>
          </Form>

          <div className="login-hint">
            初始账号 admin / admin123。首次登录后请立即在右上角“修改密码”中更换密码。
          </div>
        </div>
      </section>
    </div>
  )
}
