import React, { useEffect, useState } from 'react'
import { Button, Card, Input, Space, Table, Tag, App as AntApp } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errMsg } from '../api'

const ACTION_LABEL = [
  ['user.password', '修改密码'], ['user.create', '新增账号'], ['user.update', '修改账号'],
  ['user.reset_password', '重置密码'], ['voucher.create', '新增凭证'], ['voucher.update', '修改凭证'],
  ['voucher.audit', '审核凭证'], ['voucher.unaudit', '反审核'], ['voucher.post', '记账'],
  ['voucher.unpost', '反记账'], ['voucher.delete', '删除凭证'], ['voucher.copy', '复制凭证'],
  ['period.', '期末处理'], ['partner.', '往来单位'], ['asset.', '固定资产'],
  ['vat.', '增值税'], ['account.', '科目'], ['login', '登录'],
]

function actionLabel(value) {
  const hit = ACTION_LABEL.find(([prefix]) => value === prefix || value.startsWith(prefix))
  return hit ? hit[1] : value
}

export default function AuditLogs() {
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(100)
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  const load = async (nextPage = page, nextSize = size) => {
    setLoading(true)
    try {
      const data = await api.auditLogs({ page: nextPage, size: nextSize, q: q || undefined })
      setRows(data.rows)
      setTotal(data.total)
      setPage(data.page)
      setSize(data.size)
    } catch (e) {
      message.error(errMsg(e, '加载操作日志失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(1, 100) }, [])

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">操作日志</div>
          <div className="page-desc">记录登录、凭证、往来、资产、增值税和账号管理操作，仅管理员可见。</div>
        </div>
        <Space>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onPressEnter={() => load(1, size)}
            placeholder="搜索用户 / 对象 / 详情"
            allowClear
            style={{ width: 260 }}
          />
          <Button icon={<SearchOutlined />} onClick={() => load(1, size)}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={() => load()} loading={loading}>刷新</Button>
        </Space>
      </div>

      <Card size="small" bordered={false}>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        scroll={{ x: 900 }}
        columns={[
          { title: '时间', dataIndex: 'time', width: 170, render: (v) => dayjs(v).format('YYYY-MM-DD HH:mm:ss') },
          { title: '用户', dataIndex: 'user', width: 110 },
          { title: '操作', dataIndex: 'action', width: 120, render: (v) => <Tag>{actionLabel(v)}</Tag> },
          { title: '对象', dataIndex: 'target', width: 170, ellipsis: true },
          { title: '详情', dataIndex: 'detail', ellipsis: true },
        ]}
        pagination={{
          current: page,
          pageSize: size,
          total,
          showSizeChanger: true,
          pageSizeOptions: [50, 100, 200, 300],
          showTotal: (n) => `共 ${n} 条`,
          onChange: (p, s) => load(p, s),
        }}
      />
      </Card>
    </div>
  )
}
