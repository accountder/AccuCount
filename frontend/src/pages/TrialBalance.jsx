import React, { useContext, useEffect, useState } from 'react'
import { Button, Card, Drawer, Segmented, Space, Switch, Table, Tag, App as AntApp } from 'antd'
import { ExportOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, downloadBlob, errMsg, fmt } from '../api'
import { AppCtx } from '../context'
import ReportPeriod, { reportLabel, reportParams, reportTag } from '../components/ReportPeriod'

export default function TrialBalance() {
  const { period, meta } = useContext(AppCtx)
  const [sel, setSel] = useState({ kind: 'month', period })
  const [data, setData] = useState(null)
  const [level, setLevel] = useState(99)
  const [onlyUsed, setOnlyUsed] = useState(true)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [ledger, setLedger] = useState(null)
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setData(await api.trialBalance({ ...reportParams(sel), level, only_used: onlyUsed }))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [sel, level, onlyUsed])

  const doExport = async () => {
    setExporting(true)
    try {
      const blob = await api.exportTrialBalance({ ...reportParams(sel), level, only_used: onlyUsed })
      downloadBlob(blob, `科目余额表_${meta.company.name}_${reportTag(sel)}.xlsx`)
    } catch (e) {
      message.error(errMsg(e, '导出失败'))
    } finally {
      setExporting(false)
    }
  }

  const openLedger = async (row) => {
    try {
      const p = reportParams(sel)
      const d = await api.ledger({ account_id: row.id, period_from: p.period_from || p.period, period_to: p.period })
      setLedger(d)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const columns = [
    { title: '科目编码', dataIndex: 'code', width: 130, render: (v) => <span className="mono-num">{v}</span> },
    {
      title: '科目名称',
      dataIndex: 'name',
      render: (v, r) => (
        <a style={{ fontWeight: r.level === 1 ? 600 : 400 }} onClick={() => openLedger(r)}>
          {v}
        </a>
      ),
    },
    { title: '期初借方', dataIndex: 'open_dr', width: 120, className: 'num', align: 'right', render: (v) => (v ? fmt(v) : '') },
    { title: '期初贷方', dataIndex: 'open_cr', width: 120, className: 'num', align: 'right', render: (v) => (v ? fmt(v) : '') },
    { title: '本期借方', dataIndex: 'cur_dr', width: 120, className: 'num', align: 'right', render: (v) => (v ? fmt(v) : '') },
    { title: '本期贷方', dataIndex: 'cur_cr', width: 120, className: 'num', align: 'right', render: (v) => (v ? fmt(v) : '') },
    { title: '期末借方', dataIndex: 'close_dr', width: 120, className: 'num', align: 'right', render: (v) => (v ? fmt(v) : '') },
    { title: '期末贷方', dataIndex: 'close_cr', width: 120, className: 'num', align: 'right', render: (v) => (v ? fmt(v) : '') },
  ]

  const t = data?.totals || {}

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">科目余额表</div>
          <div className="page-desc">{reportLabel(sel)} · 点击科目名称可看明细账 · 数据仅含已记账凭证</div>
        </div>
        <Space>
          <ReportPeriod value={sel} onChange={setSel} />
          <span style={{ fontSize: 12, color: '#6b7280' }}>级次</span>
          <Segmented
            size="small"
            value={level}
            onChange={setLevel}
            options={[
              { label: '一级', value: 1 },
              { label: '二级', value: 2 },
              { label: '三级', value: 3 },
              { label: '全部', value: 99 },
            ]}
          />
          <span style={{ fontSize: 12, color: '#6b7280' }}>只看有发生</span>
          <Switch size="small" checked={onlyUsed} onChange={setOnlyUsed} />
          <Button size="small" icon={<ReloadOutlined />} onClick={load} />
          <Button size="small" type="primary" ghost icon={<ExportOutlined />} loading={exporting} onClick={doExport}>
            导出 Excel
          </Button>
        </Space>
      </div>

      <Card size="small" bordered={false}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={data?.rows || []}
          pagination={false}
          scroll={{ y: 'calc(100vh - 300px)', x: 980 }}
          summary={() => (
            <Table.Summary>
              <Table.Summary.Row style={{ fontWeight: 600, background: '#fafafa' }}>
                <Table.Summary.Cell index={0} colSpan={2}>
                  合计
                </Table.Summary.Cell>
                <Table.Summary.Cell index={2} className="num">{fmt(t.open_dr)}</Table.Summary.Cell>
                <Table.Summary.Cell index={3} className="num">{fmt(t.open_cr)}</Table.Summary.Cell>
                <Table.Summary.Cell index={4} className="num">{fmt(t.cur_dr)}</Table.Summary.Cell>
                <Table.Summary.Cell index={5} className="num">{fmt(t.cur_cr)}</Table.Summary.Cell>
                <Table.Summary.Cell index={6} className="num">{fmt(t.close_dr)}</Table.Summary.Cell>
                <Table.Summary.Cell index={7} className="num">{fmt(t.close_cr)}</Table.Summary.Cell>
              </Table.Summary.Row>
            </Table.Summary>
          )}
        />
      </Card>

      <Drawer
        title={ledger ? `明细账 · ${ledger.account.code} ${ledger.account.name}` : ''}
        open={!!ledger}
        onClose={() => setLedger(null)}
        width={860}
      >
        {ledger ? (
          <>
            <div style={{ marginBottom: 10, fontSize: 12, color: '#6b7280' }}>
              期初余额 <b className="mono-num">{fmt(ledger.opening)}</b> ·
              本期借方 <b className="mono-num">{fmt(ledger.total_debit)}</b> ·
              本期贷方 <b className="mono-num">{fmt(ledger.total_credit)}</b> ·
              期末余额 <b className="mono-num">{fmt(ledger.closing)}</b>
            </div>
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={ledger.lines}
              columns={[
                { title: '日期', dataIndex: 'date', width: 100 },
                { title: '凭证号', dataIndex: 'no', width: 90, render: (v) => <span className="mono-num">{v}</span> },
                { title: '摘要', dataIndex: 'summary', ellipsis: true },
                { title: '借方', dataIndex: 'debit', width: 110, className: 'num', render: (v) => (v ? fmt(v) : '') },
                { title: '贷方', dataIndex: 'credit', width: 110, className: 'num', render: (v) => (v ? fmt(v) : '') },
                { title: '余额', dataIndex: 'balance', width: 120, className: 'num', render: (v) => fmt(v) },
              ]}
              locale={{ emptyText: '本期无发生额' }}
            />
          </>
        ) : null}
      </Drawer>
    </div>
  )
}
