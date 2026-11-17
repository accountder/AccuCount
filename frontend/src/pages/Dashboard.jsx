import React, { useContext, useEffect, useState } from 'react'
import { App as AntApp, Button, Card, Empty, Table, Tag } from 'antd'
import {
  AccountBookOutlined,
  BankOutlined,
  CheckCircleFilled,
  ExclamationCircleFilled,
  LineChartOutlined,
  PlusOutlined,
  RightOutlined,
  WalletOutlined,
  PercentageOutlined,
} from '@ant-design/icons'
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
    return () => { alive = false }
  }, [period, message])

  const st = stats?.counts || {}
  const cards = [
    {
      title: '资产总计',
      value: stats?.total_assets,
      to: '/reports/bs',
      icon: <BankOutlined />,
      hint: '资产与负债权益联动核对',
      tone: 'brand',
    },
    {
      title: '本期净利润',
      value: stats?.net_profit,
      to: '/reports/pl',
      icon: <LineChartOutlined />,
      hint: '本期损益结果',
      tone: (stats?.net_profit || 0) >= 0 ? 'success' : 'danger',
    },
    {
      title: '货币资金',
      value: stats?.cash,
      to: '/reports/tb',
      icon: <WalletOutlined />,
      hint: '现金、银行存款及其他货币资金',
      tone: 'brand',
    },
    {
      title: stats?.vat_credit > 0 ? '增值税留抵' : '应纳增值税',
      value: stats?.vat_credit > 0 ? stats?.vat_credit : stats?.vat_payable,
      to: '/vat',
      icon: <PercentageOutlined />,
      hint: stats?.vat_credit > 0 ? '可用于下期继续抵扣' : '本期增值税测算结果',
      tone: stats?.vat_credit > 0 ? 'warning' : 'danger',
    },
  ]

  const metrics = [
    { label: '应收账款', value: stats?.ar, to: '/aging', hint: '客户欠款' },
    { label: '应付账款', value: stats?.ap, to: '/aging', hint: '欠供应商' },
    { label: '存货', value: stats?.inventory, to: '/reports/tb', hint: '原料、商品及周转材料' },
    { label: '本期费用', value: stats?.expense, to: '/reports/pl', hint: '税金及附加和期间费用' },
    { label: '本期收入', value: stats?.revenue, to: '/reports/pl', hint: '主营及其他业务收入' },
  ]

  const statusOk = chk && chk.ledger_balanced && chk.sheet_balanced && chk.unposted_count === 0
  const statusDetail = !chk
    ? '正在检查账务状态'
    : statusOk
      ? `累计借贷发生额各 ${fmt(chk.debit_total)} 元，资产负债表平衡。`
      : [
          !chk.ledger_balanced ? `借贷差额 ${fmt(chk.debit_total - chk.credit_total)} 元` : '',
          !chk.sheet_balanced ? `报表差额 ${fmt(chk.sheet_diff)} 元` : '',
          chk.unposted_count ? `${chk.unposted_count} 张凭证尚未记账` : '',
        ].filter(Boolean).join('；')

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">概览</div>
          <div className="page-desc">{meta.company.name} · {period} · {meta.company.standard}</div>
        </div>
        <div className="ant-space ant-space-horizontal" style={{ gap: 8 }}>
          <Button onClick={() => nav('/vouchers')}>全部凭证</Button>
          {!readOnly ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => nav('/vouchers/new')}>
              录入凭证
            </Button>
          ) : null}
        </div>
      </div>

      {chk ? (
        <div className={`status-bar ${statusOk ? 'is-ok' : 'is-warn'}`}>
          <span className="status-icon">
            {statusOk ? <CheckCircleFilled /> : <ExclamationCircleFilled />}
          </span>
          <div>
            <strong>{statusOk ? '账务状态正常' : '账务存在待处理事项'}</strong>
            <span className="status-detail">　{statusDetail}</span>
          </div>
        </div>
      ) : null}

      <div className="kpi-grid">
        {cards.map((card) => (
          <div key={card.title} className={`kpi-card tone-${card.tone}`} onClick={() => nav(card.to)}>
            <div className="kpi-top">
              <span className="kpi-icon">{card.icon}</span>
              <span className="kpi-label">{card.title}</span>
            </div>
            <div className="kpi-value">
              {fmt(card.value || 0)}
              <span className="kpi-unit">元</span>
            </div>
            <div className="kpi-foot">{card.hint}</div>
          </div>
        ))}
      </div>

      <div className="dashboard-grid">
        <Card
          className="dashboard-panel"
          title="最近凭证"
          extra={<span className="panel-header-extra" onClick={() => nav('/vouchers')}>全部凭证 <RightOutlined style={{ fontSize: 9 }} /></span>}
        >
          {recent.length ? (
            <Table
              rowKey="id"
              pagination={false}
              dataSource={recent}
              onRow={(r) => ({ onClick: () => nav(`/vouchers/${r.id}`), style: { cursor: 'pointer' } })}
              columns={[
                { title: '日期', dataIndex: 'date', width: 96 },
                {
                  title: '凭证号', dataIndex: 'no', width: 92,
                  render: (v) => <span className="mono-num">{v}</span>,
                },
                { title: '摘要', dataIndex: 'summary', ellipsis: true },
                {
                  title: '金额', dataIndex: 'amount', width: 118, className: 'num',
                  render: (v) => fmt(v),
                },
                {
                  title: '状态', dataIndex: 'status', width: 84,
                  render: (v) => <Tag color={STATUS[v]?.color}>{STATUS[v]?.label || v}</Tag>,
                },
              ]}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本期还没有凭证" />
          )}
        </Card>

        <Card className="dashboard-panel" title={`${period} 关键指标`}>
          <div className="metric-list">
            {metrics.map((metric) => (
              <div key={metric.label} className="metric-row" onClick={() => nav(metric.to)}>
                <span className="metric-label">
                  {metric.label}<span className="metric-hint">{metric.hint}</span>
                </span>
                <span className="metric-value num">{fmt(metric.value || 0)}</span>
              </div>
            ))}
          </div>

          <div className="voucher-count-row">
            <span className="count-pill">未审核 <b>{st.draft ?? 0}</b></span>
            <span className="count-pill">已审核 <b>{st.audited ?? 0}</b></span>
            <span className="count-pill">已记账 <b>{st.posted ?? 0}</b></span>
          </div>

          <div className="quick-actions">
            <Button size="small" icon={<AccountBookOutlined />} onClick={() => nav('/reports/tb')}>余额表</Button>
            <Button size="small" onClick={() => nav('/reports/bs')}>资产负债表</Button>
            <Button size="small" onClick={() => nav('/reports/pl')}>利润表</Button>
            <Button size="small" onClick={() => nav('/periods')}>期末处理</Button>
          </div>
        </Card>
      </div>
    </div>
  )
}
