import React from 'react'
import { DatePicker, Segmented, Select, Space } from 'antd'
import dayjs from 'dayjs'

export const quarterOf = (m) => Math.floor((m - 1) / 3) + 1
export const quarterMonths = (q) => [1 + (q - 1) * 3, 2 + (q - 1) * 3, 3 + (q - 1) * 3]

/** 报表取数参数：月报 { period }，季报 { period: 季末月, period_from: 季首月 } */
export function reportParams(sel) {
  if (sel.kind === 'quarter') {
    const q = quarterOf(Number(sel.period.slice(5, 7)))
    const y = sel.period.slice(0, 4)
    return { period: sel.period, period_from: `${y}-${String(quarterMonths(q)[0]).padStart(2, '0')}` }
  }
  return { period: sel.period }
}

/** 页面描述文字 */
export function reportLabel(sel) {
  if (sel.kind === 'quarter') {
    const q = quarterOf(Number(sel.period.slice(5, 7)))
    const m = quarterMonths(q)
    return `${sel.period.slice(0, 4)} 年第 ${q} 季度（${m[0]}-${m[2]} 月）`
  }
  return `期间 ${sel.period}`
}

/** 导出文件名用 */
export function reportTag(sel) {
  return sel.kind === 'quarter'
    ? `${sel.period.slice(0, 4)}Q${quarterOf(Number(sel.period.slice(5, 7)))}`
    : sel.period
}

/** 报表期间选择器：月报（选月份）/ 季报（选年份+季度，取数到季末） */
export default function ReportPeriod({ value, onChange }) {
  const d = dayjs(`${value.period}-01`)
  const years = Array.from({ length: 6 }, (_, i) => d.year() - 3 + i)

  const setKind = (k) => {
    if (k === 'quarter') {
      onChange({ kind: k, period: `${d.year()}-${String(quarterMonths(quarterOf(d.month() + 1))[2]).padStart(2, '0')}` })
    } else {
      onChange({ kind: k, period: d.format('YYYY-MM') })
    }
  }
  const setYear = (y) => onChange({ ...value, period: `${y}-${d.format('MM')}` })
  const setQuarter = (q) => onChange({ ...value, period: `${d.year()}-${String(quarterMonths(q)[2]).padStart(2, '0')}` })

  return (
    <Space size={8} wrap>
      <Segmented
        size="small"
        value={value.kind}
        onChange={setKind}
        options={[
          { label: '月报', value: 'month' },
          { label: '季报', value: 'quarter' },
        ]}
      />
      {value.kind === 'month' ? (
        <DatePicker
          picker="month"
          value={d}
          allowClear={false}
          onChange={(v) => v && onChange({ ...value, period: v.format('YYYY-MM') })}
          style={{ width: 130 }}
        />
      ) : (
        <>
          <Select value={d.year()} onChange={setYear} style={{ width: 90 }} options={years.map((y) => ({ value: y, label: `${y} 年` }))} />
          <Select
            value={quarterOf(d.month() + 1)}
            onChange={setQuarter}
            style={{ width: 110 }}
            options={[1, 2, 3, 4].map((q) => ({ value: q, label: `第 ${q} 季度` }))}
          />
        </>
      )}
    </Space>
  )
}
