import React, { useContext, useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select, Space, Switch, Table, Tag, App as AntApp } from 'antd'
import { KeyOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { AppCtx } from '../context'
import { api, errMsg } from '../api'

const ROLE = {
  admin: { label: '管理员', color: 'red', desc: '全部权限，可管理账号、备份和结账' },
  accountant: { label: '会计', color: 'blue', desc: '可录入、审核、记账及维护业务档案' },
  viewer: { label: '只读', color: 'default', desc: '只能查看账表，不能修改任何业务数据' },
}

export default function Users() {
  const { user: currentUser, onUserUpdated } = useContext(AppCtx)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState(null)
  const [form] = Form.useForm()
  const [pwForm] = Form.useForm()
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setRows(await api.users())
    } catch (e) {
      message.error(errMsg(e, '加载账号失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openCreate = () => {
    form.resetFields()
    form.setFieldsValue({ role: 'accountant', enabled: true })
    setCreating(true)
  }

  const openEdit = (r) => {
    form.resetFields()
    form.setFieldsValue({ name: r.name, role: r.role, enabled: r.enabled })
    setEditing(r)
  }

  const save = async () => {
    const v = await form.validateFields()
    try {
      if (creating) {
        await api.createUser(v)
        message.success('账号已创建')
      } else {
        const result = await api.updateUser(editing.id, v)
        message.success('账号已更新')
        if (editing.id === currentUser?.id) onUserUpdated?.(result)
      }
      setCreating(false)
      setEditing(null)
      load()
    } catch (e) {
      message.error(errMsg(e, '保存失败'))
    }
  }

  const resetPassword = async () => {
    const v = await pwForm.validateFields()
    try {
      await api.resetUserPassword(resetting.id, { new_password: v.new_password })
      message.success(`已重置 ${resetting.username} 的密码，旧登录已失效`)
      setResetting(null)
      pwForm.resetFields()
    } catch (e) {
      message.error(errMsg(e, '重置密码失败'))
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">账号与权限</div>
          <div className="page-desc">管理员管理登录账号；会计可做账；只读账号只能查看账表。</div>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增账号</Button>
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="登录密码最少 8 位。修改密码、停用账号或调整角色后，该账号已有的登录令牌会立即失效。"
      />

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={[
          { title: '用户名', dataIndex: 'username', width: 160 },
          { title: '姓名', dataIndex: 'name', width: 140, render: (v) => v || '—' },
          {
            title: '角色', dataIndex: 'role', width: 100,
            render: (v) => <Tag color={ROLE[v]?.color}>{ROLE[v]?.label || v}</Tag>,
          },
          {
            title: '权限说明', dataIndex: 'role',
            render: (v) => <span style={{ color: '#6b7280' }}>{ROLE[v]?.desc}</span>,
          },
          {
            title: '状态', dataIndex: 'enabled', width: 90,
            render: (v) => v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
          },
          {
            title: '操作', width: 180,
            render: (_, r) => (
              <Space size="middle">
                <a onClick={() => openEdit(r)}>编辑</a>
                {r.id !== currentUser?.id ? (
                  <a onClick={() => { setResetting(r); pwForm.resetFields() }}>
                    <KeyOutlined /> 重置密码
                  </a>
                ) : <span style={{ color: '#9ca3af' }}>本人密码请右上角修改</span>}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        open={creating || !!editing}
        title={creating ? '新增账号' : `编辑账号 · ${editing?.username}`}
        onCancel={() => { setCreating(false); setEditing(null); form.resetFields() }}
        onOk={save}
        okText={<><SaveOutlined /> 保存</>}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 14 }}>
          {creating && (
            <Form.Item name="username" label="用户名" rules={[{ required: true }, { min: 3, message: '至少 3 个字符' }]}>
              <Input autoComplete="off" />
            </Form.Item>
          )}
          {creating && (
            <Form.Item name="password" label="初始密码" rules={[{ required: true }, { min: 8, message: '至少 8 个字符' }]}>
              <Input.Password autoComplete="new-password" />
            </Form.Item>
          )}
          <Form.Item name="name" label="姓名"><Input maxLength={40} /></Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={Object.entries(ROLE).map(([value, v]) => ({ value, label: `${v.label} · ${v.desc}` }))} />
          </Form.Item>
          <Form.Item name="enabled" label="启用账号" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" disabled={editing?.id === currentUser?.id} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={!!resetting}
        title={`重置密码 · ${resetting?.username || ''}`}
        onCancel={() => setResetting(null)}
        onOk={resetPassword}
        okText="确认重置"
        destroyOnClose
      >
        <Alert type="warning" showIcon message="重置后该账号必须使用新密码重新登录。" style={{ marginBottom: 14 }} />
        <Form form={pwForm} layout="vertical">
          <Form.Item name="new_password" label="新密码" rules={[{ required: true }, { min: 8, message: '至少 8 个字符' }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
