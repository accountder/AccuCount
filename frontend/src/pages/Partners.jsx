import React, { useContext, useEffect, useState } from 'react'
import {
  Button, Card, Drawer, Form, Input, InputNumber, Popconfirm, Select, Space,
  Switch, Table, Tabs, Tag, App as AntApp,
} from 'antd'
import { PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const RATES = [
  { value: 1300, label: '13%' },
  { value: 900, label: '9%' },
  { value: 600, label: '6%' },
  { value: 500, label: '5%' },
  { value: 300, label: '3%' },
  { value: 100, label: '1%' },
  { value: 0, label: '不涉税' },
]

export default function Partners() {
  const { meta, readOnly } = useContext(AppCtx)
  const [tab, setTab] = useState('customer')
  const [rows, setRows] = useState([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setRows(await api.partners({ ptype: tab, q: q || undefined }))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [tab])

  const openNew = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ ptype: tab, tax_rate: 1300, settle_days: 30, enabled: true, credit_limit: 0 })
    setOpen(true)
  }

  const openEdit = (r) => {
    setEditing(r)
    form.setFieldsValue(r)
    setOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) await api.updatePartner(editing.id, v)
      else await api.createPartner(v)
      message.success('已保存')
      setOpen(false)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const remove = async (r) => {
    try {
      await api.deletePartner(r.id)
      message.success('已删除')
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns = [
    {
      title: '编码', dataIndex: 'code', width: 88,
      render: (v) => <span className="mono-num">{v}</span>,
    },
    {
      title: '名称', dataIndex: 'name', ellipsis: true,
      render: (v) => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    { title: '税号', dataIndex: 'tax_no', width: 170, render: (v) => <span className="mono-num" style={{ fontSize: 12 }}>{v || '—'}</span> },
    { title: '联系人', dataIndex: 'contact', width: 82, render: (v) => v || '—' },
    { title: '电话', dataIndex: 'phone', width: 114, render: (v) => <span style={{ fontSize: 12 }}>{v || '—'}</span> },
    {
      title: '税率', dataIndex: 'tax_rate', width: 72, align: 'center',
      render: (v) => <Tag color="blue">{RATES.find((x) => x.value === v)?.label || `${v / 100}%`}</Tag>,
    },
    { title: '账期', dataIndex: 'settle_days', width: 64, align: 'right', render: (v) => `${v} 天` },
    {
      title: '信用额度', dataIndex: 'credit_limit', width: 104, className: 'num',
      render: (v) => (v ? fmt(v) : '—'),
    },
    {
      title: '状态', dataIndex: 'enabled', width: 66, align: 'center',
      render: (v) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
    },
    {
      title: '', width: 94, align: 'right', fixed: 'right',
      render: (_, r) => (
        <Space size={6}>
          <a onClick={() => openEdit(r)}>编辑</a>
          <Popconfirm title="确定删除？有凭证引用的不能删" onConfirm={() => remove(r)}>
            <a style={{ color: '#c0392b' }}>删除</a>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const visibleColumns = readOnly ? columns.slice(0, -1) : columns

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">往来单位</div>
          <div className="page-desc">
            客户与供应商档案 · 名称会作为凭证辅助核算的取值，改了名称历史凭证不会自动跟着改
          </div>
        </div>
        <Space>
          <Input
            size="small"
            allowClear
            prefix={<SearchOutlined />}
            placeholder="名称 / 编码 / 税号"
            style={{ width: 200 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onPressEnter={load}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={load} />
          {!readOnly ? (
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openNew}>
              新增
            </Button>
          ) : null}
        </Space>
      </div>

      <Card size="small" bordered={false}>
        <Tabs
          size="small"
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: 'customer', label: '客户' },
            { key: 'supplier', label: '供应商' },
          ]}
        />
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={visibleColumns}
          dataSource={rows}
          pagination={{ pageSize: 30, size: 'small', showTotal: (t) => `共 ${t} 家` }}
          scroll={{ y: 'calc(100vh - 340px)' }}
        />
      </Card>

      <Drawer
        title={editing ? `编辑 · ${editing.name}` : '新增往来单位'}
        open={open}
        onClose={() => setOpen(false)}
        width={520}
        extra={<Button type="primary" size="small" onClick={submit}>保存</Button>}
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="ptype" label="类型" rules={[{ required: true }]}>
            <Select options={[{ value: 'customer', label: '客户' }, { value: 'supplier', label: '供应商' }]} />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="code" label="编码" style={{ width: '45%' }}>
              <Input placeholder="留空自动生成" />
            </Form.Item>
            <Form.Item name="name" label="名称" rules={[{ required: true, message: '必填' }]} style={{ width: '55%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="short_name" label="简称">
            <Input />
          </Form.Item>
          <Form.Item name="tax_no" label="纳税人识别号">
            <Input />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="contact" label="联系人" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
            <Form.Item name="phone" label="电话" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="address" label="地址">
            <Input />
          </Form.Item>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="bank" label="开户行" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
            <Form.Item name="bank_account" label="账号" style={{ width: '50%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="tax_rate" label="默认税率" style={{ width: '34%' }}>
              <Select options={RATES} />
            </Form.Item>
            <Form.Item name="settle_days" label="账期（天）" style={{ width: '33%' }}>
              <InputNumber min={0} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="credit_limit" label="信用额度" style={{ width: '33%' }}>
              <InputNumber min={0} precision={2} style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
        <div style={{ fontSize: 12, color: '#9ca3af', marginTop: -8 }}>
          当前账套：{meta?.company?.name}
        </div>
      </Drawer>
    </div>
  )
}
