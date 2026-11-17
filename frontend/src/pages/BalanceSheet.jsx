import React, { useContext, useEffect, useRef, useState } from 'react'
import { Button, Card, Space, Alert, App as AntApp } from 'antd'
import { ExportOutlined, PrinterOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, downloadBlob, errMsg, fmt } from '../api'
import { AppCtx } from '../context'
import ReportPeriod, { reportLabel, reportParams, reportTag } from '../components/ReportPeriod'

export default function BalanceSheet() {
  const { period, meta } = useContext(AppCtx)
  const [sel, setSel] = useState({ kind: 'month', period })
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const { message, notification } = AntApp.useApp()
  const warnedKey = useRef('')

  const load = async () => {
    setLoading(true)
    try {
      setData(await api.balanceSheet(reportParams(sel)))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [sel])

  useEffect(() => {
    if (!data) return
    const key = `${sel.kind}-${sel.period}-${data.diff}-${data.unclosed_profit}`
    if (data.balanced) {
      warnedKey.current = ''
      return
    }
    if (warnedKey.current === key) return
    warnedKey.current = key
    notification.warning({
      message: '资产负债表不平衡',
      duration: 6,
      placement: 'topRight',
      description: (
        <div style={{ fontSize: 12, lineHeight: 1.8 }}>
          <div>资产总计：<b className="mono-num">{fmt(data.total_assets)}</b> 元</div>
          <div>负债和所有者权益总计：<b className="mono-num">{fmt(data.total_liab_equity)}</b> 元</div>
          <div>差额：<b className="mono-num" style={{ color: '#b42318' }}>{fmt(data.diff)}</b> 元</div>
          <div style={{ marginTop: 8, color: '#526579' }}>
            {data.diff_matches_unclosed_profit
              ? `差额与未结转损益 ${fmt(data.unclosed_profit)} 元一致，请执行“一键结账”。`
              : '差额与未结转损益不一致，请检查未审核凭证、科目类别或报表公式。'}
          </div>
        </div>
      ),
    })
  }, [data, sel, notification])

  const doExport = async () => {
    setExporting(true)
    try {
      const blob = await api.exportBalanceSheet(reportParams(sel))
      downloadBlob(blob, `资产负债表_${meta.company.name}_${reportTag(sel)}.xlsx`)
    } catch (e) {
      message.error(errMsg(e, '导出失败'))
    } finally {
      setExporting(false)
    }
  }

  const left = data?.left || []
  const right = data?.right || []
  const n = Math.max(left.length, right.length)
  const asOf = dayjs(`${sel.period}-01`).endOf('month').format('YYYY 年 M 月 D 日')

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">资产负债表</div>
          <div className="page-desc">会小企 01 表 · {reportLabel(sel)}</div>
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

      {data && !data.balanced ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={`报表不平：资产 ${fmt(data.total_assets)}，负债和所有者权益 ${fmt(data.total_liab_equity)}，差额 ${fmt(data.diff)}`}
          description={
            data.diff_matches_unclosed_profit
              ? `差额 ${fmt(data.diff)} 元与未结转损益 ${fmt(data.unclosed_profit)} 元一致，请执行“一键结账”结转损益。`
              : '差额与未结转损益不一致，请检查未审核凭证、科目类别、漏记科目或报表公式。'
          }
        />
      ) : null}

      <Card size="small" bordered={false} loading={loading} bodyStyle={{ padding: 0 }}>
        <div className="sheet">
          <h1>资产负债表</h1>
          <div className="sub">{data?.subtitle || ''}</div>
          <div className="sheet-meta">
            <span>编制单位：{meta.company.name}</span>
            <span>{asOf}</span>
            <span>单位：元</span>
          </div>

          <div className="report-scroll">
          <table className="fin">
            <thead>
              <tr>
                <th style={{ width: '25%' }}>资产</th>
                <th style={{ width: '5%' }}>行次</th>
                <th style={{ width: '10%' }}>期末余额</th>
                <th style={{ width: '10%' }}>年初余额</th>
                <th style={{ width: '25%' }}>负债和所有者权益</th>
                <th style={{ width: '5%' }}>行次</th>
                <th style={{ width: '10%' }}>期末余额</th>
                <th style={{ width: '10%' }}>年初余额</th>
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: n }).map((_, i) => {
                const L = left[i]
                const R = right[i]
                return (
                  <tr key={i} className={L?.bold || R?.bold ? 'bold' : ''}>
                    <td style={{ paddingLeft: 8 + (L?.indent || 0) * 12 }}>
                      {L?.top ? <b>{L.label}</b> : L?.label || ''}
                    </td>
                    <td style={{ textAlign: 'center', color: '#6b7280', fontSize: 12 }}>{L?.no ?? ''}</td>
                    <td className="amt">{L?.value ? fmt(L.value) : ''}</td>
                    <td className="amt">{L?.value_begin ? fmt(L.value_begin) : ''}</td>
                    <td style={{ paddingLeft: 8 + (R?.indent || 0) * 12 }}>
                      {R?.top ? <b>{R.label}</b> : R?.label || ''}
                    </td>
                    <td style={{ textAlign: 'center', color: '#6b7280', fontSize: 12 }}>{R?.no ?? ''}</td>
                    <td className="amt">{R?.value ? fmt(R.value) : ''}</td>
                    <td className="amt">{R?.value_begin ? fmt(R.value_begin) : ''}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>

          <div style={{ marginTop: 10, fontSize: 11, color: '#9ca3af' }}>空白表示无余额。季报取季末月期末时点数。</div>
        </div>
      </Card>
    </div>
  )
}
