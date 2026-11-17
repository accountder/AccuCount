import React, { useContext, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Empty, Row, Space, Statistic, Table, Tag, App as AntApp,
} from 'antd'
import { PlusOutlined, RightOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const STATUS = {
  draft: { label: '未审核', color: 'default' },
  audited: { label: '已审核', color: 'blue' },
  posted: { label: '已记账', color: 'green' },
  void: { label: '作废', color: 'red' },
}

export default function Dashboard() {
  const { period, meta, readOnly } = useContext(AppCtx)
  const [stats, setStats] = useState(null)
  const [chk, setChk] = useState(null)
  const [recent, setRecent] = useState([])
  const nav = useNavigate()
  const { message } = AntApp.useApp()

  useEffect(() => {
    let alive = true
    Promise.all([
      api.stats({ period }),
      api.check({ period }),
      api.vouchers({ size: 6, order: 'desc' }),
    ])
      .then(([s, c, v]) => {
        if (!alive) return
        setStats(s)
        setChk(c)
        setRecent(v.rows)
      })
      .catch((e) => message.error(errMsg(e, '加载失败')))
    return () => {
      alive = false
    }
  }, [period, message])

  const st = stats?.counts || {}
  const cards = [
    { title: '资产总计', value: stats?.total_assets, color: '#1f2328', to: '/reports/bs' },
    {
      title: '本期净利润',
      value: stats?.net_profit,
      color: (stats?.net_profit || 0) >= 0 ? '#1a7f45' : '#c0392b',
      to: '/reports/pl',
    },
    { title: '货币资金', value: stats?.cash, color: '#1b57b0', to: '/reports/tb' },
    {
      title: stats?.vat_credit > 0 ? '增值税留抵' : '应纳增值税',
      value: stats?.vat_credit > 0 ? stats?.vat_credit : stats?.vat_payable,
      color: stats?.vat_credit > 0 ? '#e08a00' : '#c0392b',
      to: '/vat',
    },
  ]

  const metrics = [
    { label: '应收账款', value: stats?.ar, to: '/aging', hint: '客户欠款' },
    { label: '应付账款', value: stats?.ap, to: '/aging', hint: '欠供应商' },
    { label: '存货', value: stats?.inventory, to: '/reports/tb', hint: '原料+商品+周转材料' },
    { label: '本期费用', value: stats?.expense, to: '/reports/pl', hint: '税金及附加+三项费用' },
    { label: '本期收入', value: stats?.revenue, to: '/reports/pl', hint: '主营业务+其他业务' },
  ]

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">概览</div>
          <div className="page-desc">
            {meta.company.name} · {period} · {meta.company.standard}
          </div>
        </div>
        <Space>
          <Button size="small" onClick={() => nav('/vouchers')}>
            全部凭证
          </Button>
          {!readOnly ? (
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => nav('/vouchers/new')}>
              录入凭证
            </Button>
          ) : null}
        </Space>
      </div>

      {chk && (!chk.ledger_balanced || !chk.sheet_balanced || chk.unposted_count > 0) ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="账务存在待处理事项"
          description={
            <div style={{ fontSize: 12 }}>
              {!chk.ledger_balanced ? `借贷发生额不相等，差额 ${fmt(chk.debit_total - chk.credit_total)} 元；` : ''}
              {!chk.sheet_balanced ? `资产负债表不平，差额 ${fmt(chk.sheet_diff)} 元；` : ''}
              {chk.unposted_count ? `本期有 ${chk.unposted_count} 张凭证未记账，报表不含其数据；` : ''}
            </div>
          }
        />
      ) : (
        <Alert
          type="success"
          showIcon
          style={{ marginBottom: 12 }}
          message="账务自检通过"
          description={
            <span style={{ fontSize: 12 }}>
              累计借贷发生额各 {fmt(chk?.debit_total || 0)} 元，资产负债表平衡。
            </span>
          }
        />
      )}

      <Row gutter={12}>
        {cards.map((c) => (
          <Col span={6} key={c.title}>
            <Card
              size="small"
              bordered={false}
              hoverable
              onClick={() => nav(c.to)}
              style={{ cursor: 'pointer' }}
            >
              <Statistic
                title={c.title}
                value={c.value ?? 0}
                precision={2}
                valueStyle={{ color: c.color, fontSize: 21 }}
                suffix="元"
              />
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={12} style={{ marginTop: 12 }}>
        <Col span={14}>
          <Card
            size="small"
            bordered={false}
            title="最近凭证"
            extra={<a onClick={() => nav('/vouchers')}>全部 <RightOutlined style={{ fontSize: 10 }} /></a>}
          >
            {recent.length ? (
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={recent}
                onRow={(r) => ({ onClick: () => nav(`/vouchers/${r.id}`), style: { cursor: 'pointer' } })}
                columns={[
                  { title: '日期', dataIndex: 'date', width: 100 },
                  {
                    title: '凭证号',
                    dataIndex: 'no',
                    width: 88,
                    render: (v) => <span className="mono-num">{v}</span>,
                  },
                  { title: '摘要', dataIndex: 'summary', ellipsis: true },
                  {
                    title: '金额',
                    dataIndex: 'amount',
                    width: 116,
                    className: 'num',
                    render: (v) => fmt(v),
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 80,
                    render: (v) => <Tag color={STATUS[v]?.color}>{STATUS[v]?.label || v}</Tag>,
                  },
                ]}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本期还没有凭证" />
            )}
          </Card>
        </Col>
        <Col span={10}>
          <Card size="small" bordered={false} title={`${period} 关键指标`}>
            {metrics.map((m) => (
              <div
                key={m.label}
                onClick={() => nav(m.to)}
                style={{
                  display: 'flex',
                  alignItems: 'baseline',
                  justifyContent: 'space-between',
                  padding: '6px 0',
                  borderBottom: '1px dashed #eef0f3',
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontSize: 13 }}>
                  {m.label}
                  <span style={{ color: '#9ca3af', fontSize: 11, marginLeft: 6 }}>{m.hint}</span>
                </span>
                <span className="num" style={{ fontWeight: 500 }}>
                  {fmt(m.value || 0)}
                </span>
              </div>
            ))}
            <div style={{ marginTop: 10, fontSize: 12, color: '#6b7280' }}>
              本期凭证：未审核 {st.draft ?? 0} · 已审核 {st.audited ?? 0} · 已记账 {st.posted ?? 0}
            </div>
            <Space style={{ marginTop: 10 }} wrap>
              <Button size="small" onClick={() => nav('/reports/tb')}>科目余额表</Button>
              <Button size="small" onClick={() => nav('/reports/bs')}>资产负债表</Button>
              <Button size="small" onClick={() => nav('/reports/pl')}>利润表</Button>
              <Button size="small" onClick={() => nav('/periods')}>期末处理</Button>
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
