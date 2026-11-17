import React, { useState } from 'react'
import { Form, Input, Modal, App as AntApp } from 'antd'
import { api, errMsg } from '../api'

export default function ChangePasswordModal({ open, onClose, onChanged }) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  const submit = async () => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      const data = await api.changePassword({
        old_password: values.old_password,
        new_password: values.new_password,
      })
      message.success('密码已修改，其他设备上的旧登录已失效')
      form.resetFields()
      onChanged?.(data)
      onClose()
    } catch (e) {
      message.error(errMsg(e, '修改密码失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open={open}
      title="修改登录密码"
      onCancel={() => { form.resetFields(); onClose() }}
      onOk={submit}
      okText="确认修改"
      cancelText="取消"
      confirmLoading={loading}
      destroyOnClose
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item name="old_password" label="原密码" rules={[{ required: true, message: '请输入原密码' }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="新密码"
          rules={[{ required: true, message: '请输入新密码' }, { min: 8, message: '新密码至少 8 个字符' }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label="确认新密码"
          dependencies={['new_password']}
          rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('new_password') === value) return Promise.resolve()
                return Promise.reject(new Error('两次输入的新密码不一致'))
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
