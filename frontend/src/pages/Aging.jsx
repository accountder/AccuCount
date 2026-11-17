import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, DatePicker, Drawer, Empty, Segmented, Space, Statistic,
  Table, Tabs, Tag, Tooltip, App as AntApp,
} from 'antd'
import { ClearOutlined, PrinterOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

export default function Aging() {
  const { period } = useContext(AppCtx)
  const [tab, setTab] = useState('aging')
  const [ptype, setPtype] = useState('customer')
  const [aging, setAging] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [stmt, setStmt] = useState(null)
  const [stmtName, setStmtName] = useState('')
  const [range, setRange] = useState([dayjs(period + '-01'), dayjs(period + '-01').endOf('month')])
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      const [ag, lg] = await Promise.all([
        api.partnerAging({ period, ptype }),
        api.partnerLedger({ period, ptype }),
      ])
      setAging(ag)
      setLedger(lg)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (tab !== 'statement') load()
  }, [period, ptype, tab])

  useEffect(() => {
    setRange([dayjs(period + '-01'), dayjs(period + '-01').endOf('month')])
  }, [period])

  const doSettle = async () => {
    setBusy(true)
    try {
      const r = await api.runSettle({ period, ptype })
      message.success(`已核销 ${r.created} 笔，金额 ${fmt(r.amount)}`)
      load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const doClear = async () => {
    setBusy(true)
    try {
      const r = await api.clearSettle({ period })
      message.success(`已清除 ${r.removed} 条自动核销记录`)
      load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const openStatement = async (name) => {
    setTab('statement')
    try {
      const d = await api.statement({
        name,
        period_from: range[0].format('YYYY-MM'),
        period_to: range[1].format('YYYY-MM'),
      })
      setStmt(d)
      setStmtName(name)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const bands = aging?.bands || []

  const agingCols = useMemo(
    () => [
      { title: '单位名称', dataIndex: 'name', width: 176, fixed: 'left', ellipsis: true,
        render: (v, r) => (
          <div>
            <div>{v}</div>
            {r.oldest_days ? (
              <div style={{ fontSize: 11, color: r.overdue ? '#c0392b' : '#9ca3af' }}>
                最长 {r.oldest_days} 天
              </div>
            ) : null}
          </div>
        ) },
      {
        title: '期末余额', dataIndex: 'balance', width: 122, className: 'num',
        render: (v) => <b>{fmt(v)}</b>,
      },
      ...bands.map((b) => ({
        title: b.label,
        dataIndex: ['cells', String(b.seq)],
        width: 92,
        className: 'num',
        render: (v) => (v ? fmt(v) : <span style={{ color: '#d1d5db' }}>—</span>),
      })),
      {
        title: '逾期', dataIndex: 'overdue', width: 108, className: 'num',
        render: (v) => (v ? <span style={{ color: '#c0392b', fontWeight: 600 }}>{fmt(v)}</span> : <span style={{ color: '#d1d5db' }}>—</span>),
      },
      {
        title: '', width: 52, align: 'right', fixed: 'right',
        render: (_, r) => <a onClick={() => openStatement(r.name)}>对账</a>,
      },
    ],
    [bands, range],
  )

  const ledgerCols = [
    { title: '日期', dataIndex: 'date', width: 100 },
    {
      title: '凭证号', dataIndex: 'voucher_no', width: 90,
      render: (v) => <span className="mono-num">{v}</span>,
    },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    { title: '科目', dataIndex: 'account_name', width: 130, ellipsis: true },
    {
      title: '挂账', dataIndex: 'open_amount', width: 110, className: 'num',
      render: (v) => (v ? fmt(v) : ''),
    },
    {
      title: '收回/付款', dataIndex: 'pay_amount', width: 110, className: 'num',
      render: (v) => (v ? <span style={{ color: '#1a7f45' }}>{fmt(v)}</span> : ''),
    },
    {
      title: '已核销', dataIndex: 'settled', width: 105, className: 'num',
      render: (v) => (v ? fmt(v) : ''),
    },
    {
      title: '未核销', dataIndex: 'remain', width: 110, className: 'num',
      render: (v) => (v ? <b>{fmt(v)}</b> : ''),
    },
    {
      title: '账龄', dataIndex: 'days', width: 80, align: 'right',
      render: (v, r) =>
        r.remain > 0 ? (
          <Tag color={v > 180 ? 'red' : v > 90 ? 'orange' : v > 30 ? 'gold' : 'green'}>{v} 天</Tag>
        ) : (
          <span style={{ color: '#d1d5db' }}>—</span>
        ),
    },
  ]

  const tot = aging?.totals
  const un = ledger?.unassigned

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">往来与账龄</div>
          <div className="page-desc">
            截止 {aging?.as_of || period} · 账龄按挂账凭证日期算 · 核销采用先欠先还（FIFO）
          </div>
        </div>
        <Space>
          <Segmented
            size="small"
            value={ptype}
            onChange={setPtype}
            options={[
              { label: '应收（客户）', value: 'customer' },
              { label: '应付（供应商）', value: 'supplier' },
            ]}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={load} />
          <Tooltip title="按 FIFO 自动把收回的钱冲抵最早的挂账">
            <Button size="small" type="primary" icon={<ThunderboltOutlined />} loading={busy} onClick={doSettle}>
              自动核销
            </Button>
          </Tooltip>
          <Tooltip title="清空所有自动核销记录，回到原始状态">
            <Button size="small" icon={<ClearOutlined />} loading={busy} onClick={doClear} />
          </Tooltip>
        </Space>
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic
            title={ptype === 'customer' ? '应收账款余额' : '应付账款余额'}
            value={tot?.balance || 0}
            precision={2}
            valueStyle={{ fontSize: 22, color: '#1b57b0' }}
          />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic
            title="其中逾期"
            value={tot?.overdue || 0}
            precision={2}
            valueStyle={{ fontSize: 22, color: (tot?.overdue || 0) > 0 ? '#c0392b' : '#6b7280' }}
          />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic
            title="往来单位数"
            value={(aging?.rows || []).filter((r) => r.balance).length}
            valueStyle={{ fontSize: 22 }}
          />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic
            title="未挂单位的往来金额"
            value={un?.total || 0}
            precision={2}
            valueStyle={{ fontSize: 22, color: (un?.total || 0) > 0 ? '#e08a00' : '#6b7280' }}
          />
        </Card>
      </div>

      {un?.total > 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`有 ${un.rows.length} 行往来科目凭证没填往来单位，合计 ${fmt(un.total)} 元，账龄表统计不到它们`}
          description={
            <div style={{ fontSize: 12 }}>
              {un.rows.slice(0, 4).map((r, i) => (
                <div key={i}>
                  {r.date} {r.voucher_no} {r.account_name} {r.summary} — {fmt(r.amount)}
                </div>
              ))}
              {un.rows.length > 4 ? <div>…另有 {un.rows.length - 4} 行</div> : null}
            </div>
          }
        />
      ) : null}

      <Card size="small" bordered={false}>
        <Tabs
          size="small"
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: 'aging',
              label: '账龄分析',
              children: (
                <Table
                  rowKey="name"
                  size="small"
                  loading={loading}
                  columns={agingCols}
                  dataSource={aging?.rows || []}
                  pagination={false}
                  scroll={{ x: 1010, y: 'calc(100vh - 430px)' }}
                  summary={() =>
                    tot ? (
                      <Table.Summary fixed>
                        <Table.Summary.Row style={{ fontWeight: 600, background: '#fafafa' }}>
                          <Table.Summary.Cell index={0}>合计</Table.Summary.Cell>
                          <Table.Summary.Cell index={1} className="num">{fmt(tot.balance)}</Table.Summary.Cell>
                          {bands.map((b, i) => (
                            <Table.Summary.Cell key={b.seq} index={2 + i} className="num">
                              {tot.cells[String(b.seq)] ? fmt(tot.cells[String(b.seq)]) : ''}
                            </Table.Summary.Cell>
                          ))}
                          <Table.Summary.Cell index={2 + bands.length} className="num">
                            {fmt(tot.overdue)}
                          </Table.Summary.Cell>
                          <Table.Summary.Cell index={3 + bands.length} />
                        </Table.Summary.Row>
                      </Table.Summary>
                    ) : null
                  }
                />
              ),
            },
            {
              key: 'ledger',
              label: '往来台账',
              children: (
                <Table
                  rowKey="name"
                  size="small"
                  loading={loading}
                  dataSource={ledger?.rows || []}
                  pagination={false}
                  scroll={{ x: 1000, y: 'calc(100vh - 430px)' }}
                  expandable={{
                    expandedRowRender: (g) => (
                      <Table
                        rowKey="line_id"
                        size="small"
                        pagination={false}
                        dataSource={g.items}
                        columns={ledgerCols}
                        style={{ margin: '-8px 0' }}
                      />
                    ),
                  }}
                  columns={[
                    { title: '单位名称', dataIndex: 'name', width: 240, ellipsis: true },
                    {
                      title: '期末余额', dataIndex: 'balance', width: 140, className: 'num',
                      render: (v) => <b>{fmt(v)}</b>,
                    },
                    {
                      title: '逾期', dataIndex: 'overdue', width: 130, className: 'num',
                      render: (v) => (v ? <span style={{ color: '#c0392b' }}>{fmt(v)}</span> : '—'),
                    },
                    {
                      title: '账龄分布', dataIndex: 'oldest_days',
                      render: (v, r) =>
                        bands
                          .filter((b) => r.bands[String(b.seq)])
                          .map((b) => (
                            <Tag key={b.seq} style={{ marginBottom: 2 }}>
                              {b.label} {fmt(r.bands[String(b.seq)])}
                            </Tag>
                          )),
                    },
                    {
                      title: '账期', dataIndex: 'settle_days', width: 80, align: 'right',
                      render: (v) => `${v} 天`,
                    },
                    {
                      title: '', width: 70, align: 'right',
                      render: (_, r) => <a onClick={() => openStatement(r.name)}>对账单</a>,
                    },
                  ]}
                />
              ),
            },
            {
              key: 'statement',
              label: '对账单',
              children: (
                <div>
                  <Space style={{ marginBottom: 12 }}>
                    <span style={{ fontSize: 12, color: '#6b7280' }}>期间</span>
                    <DatePicker.RangePicker
                      size="small"
                      picker="month"
                      value={range}
                      onChange={(v) => v && setRange(v)}
                      allowClear={false}
                    />
                    <span style={{ fontSize: 12, color: '#6b7280' }}>单位</span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{stmtName || '—'}</span>
                    {!stmt ? (
                      <span style={{ fontSize: 12, color: '#9ca3af' }}>
                        从「账龄分析」点单位右侧的「对账单」生成
                      </span>
                    ) : null}
                  </Space>
                  {stmt ? (
                    <div className="sheet">
                      <h1>往来对账单</h1>
                      <div className="sub">
                        {stmtName} · {stmt.period_from} 至 {stmt.period_to}
                      </div>
                      <div className="sheet-meta">
                        <span>截止日 {stmt.as_of}</span>
                        <span>单位：元</span>
                      </div>
                      <table className="fin">
                        <thead>
                          <tr>
                            <th style={{ width: 100 }}>日期</th>
                            <th style={{ width: 90 }}>凭证号</th>
                            <th>摘要</th>
                            <th style={{ width: 120 }}>挂账</th>
                            <th style={{ width: 120 }}>收回</th>
                            <th style={{ width: 130 }}>余额</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr className="bold">
                            <td colSpan={5}>期初余额</td>
                            <td className="amt">{fmt(stmt.opening)}</td>
                          </tr>
                          {stmt.lines.map((l, i) => (
                            <tr key={i}>
                              <td>{l.date}</td>
                              <td className="mono-num">{l.voucher_no}</td>
                              <td>{l.summary}</td>
                              <td className="amt">{l.open_amount ? fmt(l.open_amount) : ''}</td>
                              <td className="amt">{l.pay_amount ? fmt(l.pay_amount) : ''}</td>
                              <td className="amt">{fmt(l.balance)}</td>
                            </tr>
                          ))}
                          <tr className="bold">
                            <td colSpan={3}>本期合计</td>
                            <td className="amt">{fmt(stmt.total_open)}</td>
                            <td className="amt">{fmt(stmt.total_pay)}</td>
                            <td className="amt">{fmt(stmt.closing)}</td>
                          </tr>
                        </tbody>
                      </table>
                      <div style={{ marginTop: 14, fontSize: 12, color: '#6b7280' }}>
                        说明：挂账为本单位形成的债权（或债务），收回为客户付款（或我方付款）。如与贵司账面有差异，请及时联系对账。
                      </div>
                      <Space style={{ marginTop: 12 }}>
                        <Button size="small" icon={<PrinterOutlined />} onClick={() => window.print()}>
                          打印
                        </Button>
                      </Space>
                    </div>
                  ) : (
                    <Empty description="尚未选择单位" />
                  )}
                </div>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
