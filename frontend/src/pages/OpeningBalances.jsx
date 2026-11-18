import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, DatePicker, Input, InputNumber, Popconfirm,
  Space, Statistic, Table, Tabs, Tag,
} from 'antd'
import {
  CheckCircleOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, UndoOutlined, UnlockOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const CATS = [
  { key: 'asset', label: '资产类' },
  { key: 'liability', label: '负债类' },
  { key: 'equity', label: '所有者权益类' },
  { key: 'cost', label: '成本类' },
  { key: 'pl', label: '损益类' },
]

const AMOUNT_FIELDS = ['opening_balance', 'ytd_debit', 'ytd_credit', 'year_opening', 'actual_pl']
const FIELD_LABELS = {
  opening_balance: '期初余额',
  ytd_debit: '本年累计借方',
  ytd_credit: '本年累计贷方',
  year_opening: '年初余额',
  actual_pl: '实际损益发生额',
}

const num = (value) => Number(value) || 0

const computedYearOpening = (row) => {
  if (row.cat === 'pl') return 0
  const opening = num(row.opening_balance)
  const dr = num(row.ytd_debit)
  const cr = num(row.ytd_credit)
  return row.direction === 'dr' ? opening - dr + cr : opening + dr - cr
}

const sumRows = (rows, field) => rows.reduce((total, row) => total + num(row[field]), 0)

const recalcOpeningRows = (input) => {
  const next = input.map((row) => ({ ...row }))
  next.forEach((row) => {
    if (row.is_leaf) row.year_opening = computedYearOpening(row)
  })

  // 从最深层向上汇总；手工父科目保留手工数，不被子科目覆盖。
  const parents = next
    .filter((row) => !row.is_leaf)
    .sort((a, b) => (b.level || 0) - (a.level || 0))
  for (const parent of parents) {
    if (!parent.manual_override) {
      const children = next.filter((row) => row.parent_id === parent.account_id)
      parent.opening_balance = sumRows(children, 'opening_balance')
      parent.ytd_debit = sumRows(children, 'ytd_debit')
      parent.ytd_credit = sumRows(children, 'ytd_credit')
      parent.actual_pl = sumRows(children, 'actual_pl')
    }
    parent.year_opening = computedYearOpening(parent)
  }

  // 下级合计始终按直接下级计算，供手工合计差异提示使用。
  for (const parent of parents) {
    const children = next.filter((row) => row.parent_id === parent.account_id)
    parent.child_opening_balance = sumRows(children, 'opening_balance')
    parent.child_ytd_debit = sumRows(children, 'ytd_debit')
    parent.child_ytd_credit = sumRows(children, 'ytd_credit')
    parent.child_year_opening = computedYearOpening({
      ...parent,
      opening_balance: parent.child_opening_balance,
      ytd_debit: parent.child_ytd_debit,
      ytd_credit: parent.child_ytd_credit,
    })
    parent.child_actual_pl = sumRows(children, 'actual_pl')
    parent.differences = {}
    for (const field of AMOUNT_FIELDS) {
      const childField = `child_${field}`
      parent.differences[field] = parent.manual_override
        ? num(parent[field]) - num(parent[childField])
        : 0
    }
  }
  return next
}

const cloneRow = (row, idx) => ({
  ...row,
  _key: `${row.account_id}-${Date.now()}-${idx}`,
  line_id: null,
})

const decorateRows = (source) => recalcOpeningRows((source || []).map((row, index) => ({
  ...row,
  aux_type: row.aux_type || (row.aux_types || '').split(',')[0] || '',
  _key: `${row.account_id}-${index}`,
})))

export default function OpeningBalances() {
  const { meta, reloadMeta, readOnly } = useContext(AppCtx)
  const { message, modal } = AntApp.useApp()
  const [startPeriod, setStartPeriod] = useState(meta.company?.start_period || meta.open_period || '2025-01')
  const [data, setData] = useState(null)
  const [rows, setRows] = useState([])
  const [tab, setTab] = useState('asset')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = async (period = startPeriod) => {
    setLoading(true)
    try {
      const result = await api.opening(period)
      setData(result)
      setRows(decorateRows(result.rows))
    } catch (error) {
      message.error(errMsg(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(startPeriod) }, [startPeriod])

  const confirmed = data?.batch?.status === 'confirmed'
  const visible = useMemo(() => rows.filter((row) => row.cat === tab), [rows, tab])

  const setCell = (key, field, value) => {
    setRows((previous) => recalcOpeningRows(previous.map((row) => {
      if (row._key !== key) return row
      const next = { ...row, [field]: value ?? 0 }
      if (!row.is_leaf) next.manual_override = true
      return next
    })))
  }

  const restoreAuto = (row) => {
    setRows((previous) => recalcOpeningRows(previous.map((item) => (
      item._key === row._key ? { ...item, manual_override: false } : item
    ))))
  }

  const addAuxRow = (row) => {
    if (!row.is_leaf) return
    const blank = cloneRow({
      ...row,
      aux_name: '',
      opening_balance: 0,
      ytd_debit: 0,
      ytd_credit: 0,
      year_opening: 0,
      actual_pl: 0,
    }, rows.length)
    setRows((previous) => recalcOpeningRows([...previous, blank]))
  }

  const payloadLines = () => rows
    .filter((row) => {
      if (!row.is_leaf && !row.manual_override) return false
      return row.manual_override || row.opening_balance || row.ytd_debit || row.ytd_credit
        || row.year_opening || row.actual_pl || row.aux_name
    })
    .map((row) => ({
      account_id: row.account_id,
      aux_type: row.aux_type || '',
      aux_name: row.aux_name || '',
      opening_balance: num(row.opening_balance),
      ytd_debit: num(row.ytd_debit),
      ytd_credit: num(row.ytd_credit),
      year_opening: num(row.year_opening),
      actual_pl: num(row.actual_pl),
      manual_override: Boolean(row.manual_override),
      remark: row.remark || '',
    }))

  const applySaved = (result) => {
    setData(result)
    setRows(decorateRows(result.rows))
  }

  const save = async () => {
    setBusy(true)
    try {
      const result = await api.saveOpening({
        start_period: startPeriod,
        note: data?.batch?.note || '',
        lines: payloadLines(),
      })
      applySaved(result)
      message.success('期初余额已保存')
      if (result.warnings?.length) {
        modal.warning({
          title: '上级科目手工合计与下级合计不一致',
          width: 680,
          content: (
            <div>
              <div style={{ marginBottom: 8 }}>已保存，不影响继续确认。请复核以下差异：</div>
              {result.warnings.map((warning) => (
                <div key={warning.account_id} style={{ marginTop: 6 }}>{warning.message}</div>
              ))}
            </div>
          ),
        })
      }
      return result
    } catch (error) {
      message.error(errMsg(error))
      return null
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    const saved = await save()
    if (!saved?.batch?.id) return
    setBusy(true)
    try {
      const result = await api.confirmOpening(saved.batch.id)
      applySaved(result)
      message.success(`期初已确认，期初凭证 ${result.voucher_no}`)
      reloadMeta()
    } catch (error) {
      message.error(errMsg(error))
    } finally {
      setBusy(false)
    }
  }

  const unconfirm = async () => {
    setBusy(true)
    try {
      await api.unconfirmOpening(data.batch.id)
      message.success('已反确认，可以修改期初')
      await load(startPeriod)
      reloadMeta()
    } catch (error) {
      message.error(errMsg(error))
    } finally {
      setBusy(false)
    }
  }

  const renderAmountInput = (row, field, disabled = false) => (
    <InputNumber
      value={row[field]}
      precision={2}
      disabled={readOnly || confirmed || disabled}
      style={{ width: '100%' }}
      onChange={(value) => setCell(row._key, field, value)}
    />
  )

  const columns = [
    { title: '科目编码', dataIndex: 'code', width: 118, render: (value) => <span className="mono-num">{value}</span> },
    {
      title: '科目名称', dataIndex: 'name', width: 250,
      render: (value, row) => (
        <div style={{ paddingLeft: Math.max((row.level || 1) - 1, 0) * 14 }}>
          <span style={{ fontWeight: row.is_leaf ? 400 : 650 }}>{value}</span>
          {!row.is_leaf ? (
            <Tag color={row.manual_override ? 'orange' : 'blue'} bordered={false} style={{ marginLeft: 8 }}>
              {row.manual_override ? '手工合计' : '自动汇总'}
            </Tag>
          ) : null}
        </div>
      ),
    },
    {
      title: '辅助核算', width: 180,
      render: (_, row) => row.is_leaf && row.aux_types ? (
        <Input
          value={row.aux_name}
          disabled={readOnly || confirmed}
          placeholder="客户 / 供应商名称"
          onChange={(event) => setCell(row._key, 'aux_name', event.target.value)}
        />
      ) : <span style={{ color: '#9ca3af' }}>—</span>,
    },
    { title: '方向', dataIndex: 'direction', width: 60, render: (value) => value === 'dr' ? '借' : '贷' },
    {
      title: '期初余额', width: 138,
      render: (_, row) => renderAmountInput(row, 'opening_balance', row.cat === 'pl'),
    },
    {
      title: '本年累计借方', width: 138,
      render: (_, row) => renderAmountInput(row, 'ytd_debit'),
    },
    {
      title: '本年累计贷方', width: 138,
      render: (_, row) => renderAmountInput(row, 'ytd_credit'),
    },
    {
      title: '年初余额（系统计算）', width: 145,
      render: (_, row) => (
        <span className="mono-num" style={{ display: 'block', textAlign: 'right', paddingRight: 8 }}>
          {fmt(row.year_opening)}
        </span>
      ),
    },
    {
      title: '实际损益发生额', width: 145,
      render: (_, row) => renderAmountInput(row, 'actual_pl', row.cat !== 'pl'),
    },
    {
      title: '', width: 104, fixed: 'right',
      render: (_, row) => {
        if (row.is_leaf && row.aux_types && !confirmed && !readOnly) {
          return <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => addAuxRow(row)} />
        }
        if (!row.is_leaf && row.manual_override && !confirmed && !readOnly) {
          return (
            <Button type="text" size="small" icon={<UndoOutlined />} onClick={() => restoreAuto(row)}>
              恢复汇总
            </Button>
          )
        }
        return null
      },
    },
  ]

  const totals = data?.totals || { debit: 0, credit: 0, diff: 0, balanced: true }
  const mismatchRows = rows.filter((row) => (
    !row.is_leaf && row.manual_override && Object.values(row.differences || {}).some((value) => Math.abs(num(value)) > 0.000001)
  ))
  const liveWarnings = mismatchRows.map((row) => {
    const fields = AMOUNT_FIELDS
      .filter((field) => Math.abs(num(row.differences?.[field])) > 0.000001)
      .map((field) => `${FIELD_LABELS[field]}差异 ${fmt(row.differences[field])}`)
    return `${row.code} ${row.name}：${fields.join('；')}`
  })

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">期初余额</div>
          <div className="page-desc">按科目大类录入启用日余额；上级科目可手工填写，也可恢复自动汇总</div>
        </div>
        <Space wrap>
          <DatePicker
            picker="month"
            value={dayjs(startPeriod + '-01')}
            disabled={confirmed}
            onChange={(value) => value && setStartPeriod(value.format('YYYY-MM'))}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(startPeriod)}>刷新</Button>
          {!readOnly ? (
            <Button icon={<SaveOutlined />} loading={busy} disabled={confirmed} onClick={save}>保存草稿</Button>
          ) : null}
          {!readOnly && !confirmed ? (
            <Popconfirm
              title="确认期初余额？"
              description="确认后将生成期初凭证；启用期间已有业务凭证时不能直接反确认。"
              onConfirm={confirm}
            >
              <Button type="primary" icon={<CheckCircleOutlined />} loading={busy}>确认期初</Button>
            </Popconfirm>
          ) : null}
          {!readOnly && confirmed ? <Button icon={<UnlockOutlined />} onClick={unconfirm}>反确认</Button> : null}
        </Space>
      </div>

      <div className="calc-kpis calc-kpis-four" style={{ marginBottom: 14 }}>
        <Card size="small" bordered={false}><Statistic title="期初借方" value={totals.debit} precision={2} /></Card>
        <Card size="small" bordered={false}><Statistic title="期初贷方" value={totals.credit} precision={2} /></Card>
        <Card size="small" bordered={false}>
          <Statistic
            title="差额"
            value={totals.diff}
            precision={2}
            valueStyle={{ color: totals.balanced ? '#1a7f45' : '#b42318' }}
          />
        </Card>
        <Card size="small" bordered={false}>
          <Statistic
            title="状态"
            value={confirmed ? '已确认' : '草稿'}
            valueStyle={{ color: confirmed ? '#1a7f45' : '#8a5a00', fontSize: 22 }}
          />
        </Card>
      </div>

      {!totals.balanced ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`期初借贷不平衡，差额 ${fmt(totals.diff)} 元`}
        />
      ) : null}
      {confirmed ? (
        <Alert type="success" showIcon style={{ marginBottom: 12 }} message="期初已确认，后续报表自动使用该期初数据" />
      ) : null}
      {liveWarnings.length ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`${liveWarnings.length} 个上级科目的手工合计与下级合计不一致（仅提示，不阻止保存）`}
          description={<div>{liveWarnings.map((warning) => <div key={warning}>{warning}</div>)}</div>}
        />
      ) : null}

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="上级科目支持自动汇总或手工覆盖"
        description="默认带出下级合计；直接修改上级科目即切换为手工合计，保存和确认时只提示与下级合计的差异，不强制阻止。期初凭证按“手工上级优先、跳过其下级”生成，避免重复。"
      />

      <Card size="small" bordered={false}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={CATS.map((category) => ({
            key: category.key,
            label: `${category.label} ${rows.filter((row) => row.is_leaf && row.cat === category.key && (row.opening_balance || row.ytd_debit || row.ytd_credit || row.actual_pl)).length || ''}`,
            children: (
              <>
                {category.key === 'pl' ? (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 10 }}
                    message="损益类不录入资产负债期初余额，只录入启用日前本年累计和实际损益发生额"
                  />
                ) : null}
                <Table
                  rowKey="_key"
                  size="small"
                  loading={loading}
                  dataSource={visible}
                  columns={columns}
                  pagination={false}
                  scroll={{ x: 1270, y: 'calc(100vh - 450px)' }}
                />
              </>
            ),
          }))}
        />
      </Card>
    </div>
  )
}
