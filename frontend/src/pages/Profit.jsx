import React, { useContext, useEffect, useState } from 'react'
import { Button, Card, Space, App as AntApp } from 'antd'
import { ExportOutlined, PrinterOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, downloadBlob, errMsg, fmt } from '../api'
import { AppCtx } from '../context'
import ReportPeriod, { reportLabel, reportParams, reportTag } from '../components/ReportPeriod'

export default function Profit() {
  const { period, meta } = useContext(AppCtx)
  const [sel, setSel] = useState({ kind: 'month', period })
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setData(await api.profit(reportParams(sel)))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [sel])

  const doExport = async () => {
    setExporting(true)
    try {
      const blob = await api.exportProfit(reportParams(sel))
      downloadBlob(blob, `利润表_${meta.company.name}_${reportTag(sel)}.xlsx`)
    } catch (e) {
      message.error(errMsg(e, '导出失败'))
    } finally {
      setExporting(false)
    }
  }

  const isQuarter = sel.kind === 'quarter'
  const curCol = isQuarter ? '本季金额' : '本月金额'

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">利润表</div>
          <div className="page-desc">会小企 02 表 · {reportLabel(sel)}</div>
        </div>
        <Space>
          <ReportPeriod value={sel} onChange={setSel} />
          <Button icon={<ReloadOutlined />} onClick={load}>重新计算</Button>
          <Button icon={<ExportOutlined />} loading={exporting} onClick={doExport}>
            导出 Excel
          </Button>
          <Button icon={<PrinterOutlined />} onClick={() => window.print()}>打印</Button>
        </Space>
      </div>

      <Card size="small" bordered={false} loading={loading} bodyStyle={{ padding: 0 }}>
        <div className="sheet">
          <h1>利润表</h1>
          <div className="sub">{data?.subtitle || ''}</div>
          <div className="sheet-meta">
            <span>编制单位：{meta.company.name}</span>
            <span>{reportLabel(sel).replace('期间 ', '')}</span>
            <span>单位：元</span>
          </div>

          <div className="report-scroll">
          <table className="fin">
            <thead>
              <tr>
                <th>项目</th>
                <th style={{ width: 62 }}>行次</th>
                <th style={{ width: 158 }}>本年累计金额</th>
                <th style={{ width: 158 }}>{curCol}</th>
              </tr>
            </thead>
            <tbody>
              {(data?.rows || []).map((r) => (
                <tr key={r.key} className={r.bold ? 'bold' : ''}>
                  <td style={{ paddingLeft: 10 + (r.indent || 0) * 16 }}>{r.label}</td>
                  <td style={{ textAlign: 'center', color: '#6b7280', fontSize: 12 }}>{r.no ?? ''}</td>
                  <td className="amt">{r.value_ytd ? fmt(r.value_ytd) : ''}</td>
                  <td className="amt">{r.value ? fmt(r.value) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>

          <div style={{ marginTop: 10, fontSize: 11, color: '#9ca3af' }}>
            空白表示本期无发生额。{isQuarter ? '季报的「本季金额」为该季度三个月合计，「本年累计」为年初至季末。' : ''}
          </div>
        </div>
      </Card>
    </div>
  )
}
