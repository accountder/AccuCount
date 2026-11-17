import React, { useContext, useEffect, useState } from 'react'
import { Alert, Button, Card, Popconfirm, Space, Table, Tag, App as AntApp } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { api, errMsg } from '../api'
import { AppCtx } from '../context'

export default function Periods() {
  const { period, setPeriod, reloadMeta, user, readOnly } = useContext(AppCtx)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setRows(await api.periods())
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const run = async (fn, ok, ...args) => {
    try {
      const r = await fn(...args)
      message.success(typeof ok === 'function' ? ok(r) : ok)
      load()
      reloadMeta()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns = [
    { title: '会计期间', dataIndex: 'code', width: 120, render: (v) => <span className="mono-num">{v}</span> },
    { title: '已记账凭证', dataIndex: 'posted', width: 110, className: 'num' },
    {
      title: '未记账',
      dataIndex: 'pending',
      width: 100,
      className: 'num',
      render: (v) => (v ? <Tag color="orange">{v}</Tag> : '—'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v) => (v === 'closed' ? <Tag color="default">已结账</Tag> : <Tag color="green">开放</Tag>),
    },
    {
      title: '操作',
      render: (_, r) => (
        <Space size={6}>
          <Button size="small" onClick={() => setPeriod(r.code)}>
            切换到此期
          </Button>
          {!readOnly ? (
            <Popconfirm
              title="生成结转本期损益凭证？"
              description="将本期损益类科目余额转入 3103 本年利润，生成一张待审核凭证。"
              onConfirm={() => run(api.carryover, (res) => `已生成凭证 ${res.no}，本期利润 ${res.profit}`.concat('', ''), r.code)}
              disabled={r.status === 'closed'}
            >
              <Button size="small" disabled={r.status === 'closed'}>
                结转损益
              </Button>
            </Popconfirm>
          ) : null}
          {!readOnly && user?.role === 'admin' && r.status === 'open' ? (
            <Popconfirm
              title={`确认结账 ${r.code}？`}
              description="结账后本期凭证不可再修改。需先全部记账并完成损益结转。"
              onConfirm={() => run(api.closePeriod, '已结账', r.code)}
            >
              <Button size="small" type="primary" danger>
                结账
              </Button>
            </Popconfirm>
          ) : null}
          {!readOnly && user?.role === 'admin' && r.status === 'closed' ? (
            <Popconfirm title={`反结账 ${r.code}？`} onConfirm={() => run(api.reopenPeriod, '已反结账', r.code)}>
              <Button size="small">反结账</Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">期末处理</div>
          <div className="page-desc">结转损益 → 全部记账 → 结账。当前会计期间 {period}</div>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="结账顺序"
        description={
          <div style={{ fontSize: 12, lineHeight: 1.9 }}>
            1. 确认本期所有凭证已<b>记账</b>；2. 点「结转损益」生成结转凭证，审核并记账；3. 点「结账」锁定本期。
            结账后如需修改，先「反结账」。
          </div>
        }
      />

      <Card size="small" bordered={false}>
        <Table
          rowKey="code"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 760 }}
        />
      </Card>
    </div>
  )
}
