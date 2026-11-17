import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Button, Card, Form, Input, Modal, Select, Space, Switch, Table, Tag, Tooltip, App as AntApp,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api'
import { AppCtx } from '../context'

const CAT = {
  asset: { label: '资产', color: 'blue' },
  liability: { label: '负债', color: 'volcano' },
  equity: { label: '权益', color: 'purple' },
  cost: { label: '成本', color: 'gold' },
  pl: { label: '损益', color: 'green' },
}

const AUX = { customer: '客户', supplier: '供应商', dept: '部门', employee: '员工', project: '项目' }

export default function Accounts() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()
  const { readOnly } = useContext(AppCtx)
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setRows(await api.accounts())
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const tree = useMemo(() => {
    const map = new Map()
    rows.forEach((r) => map.set(r.id, { ...r, children: [] }))
    const roots = []
    map.forEach((r) => {
      if (r.parent_id && map.has(r.parent_id)) map.get(r.parent_id).children.push(r)
      else roots.push(r)
    })
    const sortRec = (list) => {
      list.sort((a, b) => a.code.localeCompare(b.code))
      list.forEach((x) => sortRec(x.children))
    }
    sortRec(roots)
    return roots
  }, [rows])

  const openNew = (record) => {
    setEditing(null)
    const nextCode = record ? `${record.code}.` : ''
    form.resetFields()
    form.setFieldsValue({
      code: nextCode,
      cat: record?.cat || 'asset',
      direction: record?.direction || 'dr',
    })
    setOpen(true)
  }

  const openEdit = (record) => {
    setEditing(record)
    form.setFieldsValue({
      code: record.code,
      name: record.name,
      cat: record.cat,
      direction: record.direction,
      aux_types: record.aux_types ? record.aux_types.split(',') : [],
      remark: record.remark,
      enabled: record.enabled,
    })
    setOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) {
        await api.updateAccount(editing.id, {
          name: v.name,
          aux_types: (v.aux_types || []).join(','),
          remark: v.remark,
          enabled: v.enabled,
        })
        if (v.code !== editing.code) {
          await api.updateAccount(editing.id, { code: v.code })
        }
        message.success('已保存')
      } else {
        await api.createAccount({
          code: v.code,
          name: v.name,
          cat: v.cat,
          direction: v.direction,
          aux_types: (v.aux_types || []).join(','),
          remark: v.remark,
        })
        message.success('科目已新增')
      }
      setOpen(false)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const remove = (record) => {
    Modal.confirm({
      title: `删除科目 ${record.code} ${record.name}？`,
      content: '有分录或有下级的科目不能删除，可改为停用。',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.deleteAccount(record.id)
          message.success('已删除')
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const columns = [
    {
      title: '科目编码',
      dataIndex: 'code',
      width: 150,
      render: (v) => <span className="mono-num">{v}</span>,
    },
    {
      title: '科目名称',
      dataIndex: 'name',
      render: (v, r) => (
        <span style={{ fontWeight: r.level === 1 ? 600 : 400 }}>
          {v}
          {!r.is_leaf ? <Tag style={{ marginLeft: 6, transform: 'scale(0.82)' }}>汇总</Tag> : null}
        </span>
      ),
    },
    {
      title: '类别',
      dataIndex: 'cat',
      width: 80,
      render: (v) => <Tag color={CAT[v]?.color}>{CAT[v]?.label || v}</Tag>,
    },
    {
      title: '余额方向',
      dataIndex: 'direction',
      width: 90,
      render: (v) => (v === 'dr' ? '借方' : '贷方'),
    },
    {
      title: '辅助核算',
      dataIndex: 'aux_types',
      width: 130,
      render: (v) =>
        v
          ? v
              .split(',')
              .filter(Boolean)
              .map((t) => (
                <Tag key={t} color="geekblue" style={{ transform: 'scale(0.9)' }}>
                  {AUX[t] || t}
                </Tag>
              ))
          : '—',
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 80,
      render: (v) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="default">停用</Tag>,
    },
    {
      title: '操作',
      width: 170,
      render: (_, r) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => openNew(r)}>
            加下级
          </Button>
          <Button type="link" size="small" onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Button type="link" size="small" danger onClick={() => remove(r)}>
            删除
          </Button>
        </Space>
      ),
    },
  ]

  const visibleColumns = readOnly ? columns.filter((c) => c.title !== '操作') : columns

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">会计科目表</div>
          <div className="page-desc">
            共 {rows.length} 个科目 · 按《小企业会计准则》预置，可自行增设明细。只有明细科目能用于分录。
          </div>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>
            刷新
          </Button>
          {!readOnly ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openNew(null)}>
              新增科目
            </Button>
          ) : null}
        </Space>
      </div>

      <Card size="small" bordered={false}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={visibleColumns}
          dataSource={tree}
          pagination={false}
          expandable={{ defaultExpandAllRows: true, indentSize: 16 }}
          scroll={{ y: 'calc(100vh - 260px)' }}
        />
      </Card>

      <Modal
        title={editing ? `编辑科目 ${editing.code}` : '新增科目'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={submit}
        okText="保存"
        destroyOnClose
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            label="科目编码"
            name="code"
            rules={[
              { required: true, message: '请输入编码' },
              {
                pattern: /^\d{4}(\.\d{1,3})*$/,
                message: '格式如 1002 或 1002.01，用点号分层',
              },
            ]}
            extra={editing ? '已有分录的科目不能改编码，只能改名' : '下级科目用点号分层，如 1002.03'}
          >
            <Input placeholder="1002.03" className="mono-num" />
          </Form.Item>
          <Form.Item label="科目名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="银行存款-招商银行" />
          </Form.Item>
          <Space size={12} style={{ display: 'flex' }}>
            <Form.Item label="科目类别" name="cat" style={{ flex: 1 }} rules={[{ required: true }]}>
              <Select
                disabled={!!editing}
                options={Object.entries(CAT).map(([k, v]) => ({ value: k, label: v.label }))}
              />
            </Form.Item>
            <Form.Item label="余额方向" name="direction" style={{ flex: 1 }} rules={[{ required: true }]}>
              <Select
                disabled={!!editing}
                options={[
                  { value: 'dr', label: '借方' },
                  { value: 'cr', label: '贷方' },
                ]}
              />
            </Form.Item>
          </Space>
          <Form.Item label="辅助核算" name="aux_types" extra="需要按客户、供应商、部门等维度查账时勾选">
            <Select
              mode="multiple"
              allowClear
              options={Object.entries(AUX).map(([k, v]) => ({ value: k, label: v }))}
            />
          </Form.Item>
          <Form.Item label="备注" name="remark">
            <Input.TextArea rows={2} />
          </Form.Item>
          {editing ? (
            <Form.Item label="启用状态" name="enabled" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>
    </div>
  )
}
