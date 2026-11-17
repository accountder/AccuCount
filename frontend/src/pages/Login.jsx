import React, { useState } from 'react'
import { Button, Form, Input, App as AntApp } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
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
    <div className="login-wrap">
      <div className="login-card">
        <div className="brand-title">AccuCount</div>
        <div className="brand-sub">准确入账，清晰经营 · 小企业会计准则账务引擎</div>
        <Form layout="vertical" onFinish={submit} style={{ marginTop: 26 }} initialValues={{ username: 'admin' }}>
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input size="large" prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password size="large" prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={loading} style={{ marginTop: 6 }}>
            登 录
          </Button>
        </Form>
        <div style={{ marginTop: 18, fontSize: 12, color: '#9ca3af', lineHeight: 1.7 }}>
          初始账号 admin / admin123。首次登录后请立即在右上角「修改密码」中更换密码。
        </div>
      </div>
    </div>
  )
}
