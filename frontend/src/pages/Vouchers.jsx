import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Dropdown, Empty, Input, Modal, Select, Space, Switch, Table, Tag,
  Tooltip, App as AntApp,
} from 'antd'
import {
  CheckCircleOutlined, CopyOutlined, DeleteOutlined, DownOutlined, ExportOutlined,
  MoreOutlined, OrderedListOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const STATUS = {
  draft: { label: '未审核', color: 'default' },
  audited: { label: '已审核入账', color: 'green' },
  posted: { label: '已入账（旧）', color: 'green' },
  void: { label: '作废', color: 'red' },
}

const EXPAND_KEY = 'accucount_expand_lines_v2'

/** 生成 CSV 并触发下载。加 BOM 让 Excel 正确识别 UTF-8 中文。 */
function downloadCsv(filename, rows) {
  const esc = (x) => {
    const s = x === null || x === undefined ? '' : String(x)
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const csv = '\ufeff' + rows.map((r) => r.map(esc).join(',')).join('\r\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** 展开行里的分录表，按金蝶的列序：摘要 / 科目 / 借方 / 贷方，辅助信息挂在科目下方 */
function LinePanel({ v }) {
  const dr = v.lines.reduce((s, l) => s + (l.debit || 0), 0)
  const cr = v.lines.reduce((s, l) => s + (l.credit || 0), 0)
  return (
    <table className="fin sub-lines">
      <thead>
        <tr>
          <th>摘要</th>
          <th style={{ width: 320 }}>会计科目</th>
          <th style={{ width: 128 }}>借方金额</th>
          <th style={{ width: 128 }}>贷方金额</th>
        </tr>
      </thead>
      <tbody>
        {v.lines.map((l, i) => (
          <tr key={l.id || i}>
            <td>
              <span style={{ color: '#b0b6bf', marginRight: 6, fontSize: 11 }}>{i + 1}</span>
              {l.summary}
            </td>
            <td>
              <span className="mono-num" style={{ color: '#6b7280', marginRight: 6 }}>{l.account_code}</span>
              {l.account_name}
              {l.aux_name || l.tax_rate ? (
                <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 8 }}>
                  {l.aux_name}
                  {l.tax_rate ? ` · ${l.tax_rate / 100}%` : ''}
                </span>
              ) : null}
            </td>
            <td className="amt">{l.debit ? fmt(l.debit) : ''}</td>
            <td className="amt">{l.credit ? fmt(l.credit) : ''}</td>
          </tr>
        ))}
        <tr className="bold">
          <td colSpan={2} style={{ textAlign: 'right' }}>合计</td>
          <td className="amt">{fmt(dr)}</td>
          <td className="amt">{fmt(cr)}</td>
        </tr>
      </tbody>
    </table>
  )
}

export default function Vouchers() {
  const { period, meta, readOnly } = useContext(AppCtx)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState()
  const [q, setQ] = useState('')
  const [pf, setPf] = useState(period)
  const [pt, setPt] = useState(period)
  const [quickPeriod, setQuickPeriod] = useState(period)
  // 展开分录是用户的浏览偏好，存本地，切页面回来不该被重置
  const [expandAll, setExpandAll] = useState(() => localStorage.getItem(EXPAND_KEY) === '1')
  const [expandedKeys, setExpandedKeys] = useState([])
  const [selected, setSelected] = useState([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [copyOpen, setCopyOpen] = useState(false)
  const [copyTarget, setCopyTarget] = useState(null)
  const nav = useNavigate()
  const { message, modal } = AntApp.useApp()

  useEffect(() => {
    localStorage.setItem(EXPAND_KEY, expandAll ? '1' : '0')
  }, [expandAll])

  const isAll = !pf && !pt

  const load = async () => {
    setLoading(true)
    try {
      const d = await api.vouchers({
        period_from: pf || undefined,
        period_to: pt || undefined,
        status,
        q: q || undefined,
        page,
        size: 50,
        with_lines: expandAll,
      })
      setRows(d.rows)
      setTotal(d.total)
      setSelected([])
      if (!expandAll) setExpandedKeys([])
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  // 系统当前待结期间变化时，默认回到该单期；不影响用户后续跨期查询
  useEffect(() => {
    setPf(period)
    setPt(period)
    setQuickPeriod(period)
    setPage(1)
  }, [period])

  useEffect(() => {
    load()
  }, [pf, pt, status, page, expandAll])

  const pickPeriod = (which, v) => {
    let nextFrom = pf
    let nextTo = pt
    if (which === 'from') {
      nextFrom = v
      if (v && pt && v > pt) nextTo = v
    } else {
      nextTo = v
      if (v && pf && v < pf) nextFrom = v
    }
    setPf(nextFrom)
    setPt(nextTo)
    setQuickPeriod(nextFrom && nextTo && nextFrom === nextTo ? nextFrom : null)
    setPage(1)
  }

  const pickQuickPeriod = (v) => {
    setQuickPeriod(v || null)
    setPf(v || null)
    setPt(v || null)
    setPage(1)
  }

  const periodOptions = meta.periods.map((p) => ({
    value: p.code,
    label: `${p.code}${p.status === 'closed' ? '（已结）' : ''}`,
  }))

  const act = async (id, a, okMsg) => {
    try {
      await api.voucherAction(id, a)
      message.success(okMsg)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const del = (r) => {
    Modal.confirm({
      title: `删除凭证 ${r.no}？`,
      content: '删除后不可恢复。已审核凭证需先反审核。',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.deleteVoucher(r.id)
          message.success('已删除')
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const batch = (action) => {
    const label = action === 'audit' ? '审核' : '删除'
    modal.confirm({
      title: `批量${label} ${selected.length} 张凭证`,
      content:
        action === 'audit'
          ? '只有未审核的凭证会被审核，其余自动跳过并列出原因。'
          : '只有未审核或作废的凭证会被删除，其余自动跳过。删除不可恢复。',
      okButtonProps: action === 'delete' ? { danger: true } : undefined,
      onOk: async () => {
        setBusy(true)
        try {
          const r = await api.batchVouchers({ ids: selected, action })
          if (r.skipped.length) {
            message.warning(`完成 ${r.done} 张，跳过 ${r.skipped.length} 张：${r.skipped.slice(0, 3).map((s) => `${s.no || s.id} ${s.reason}`).join('；')}`)
          } else {
            message.success(`已${label} ${r.done} 张`)
          }
          load()
        } catch (e) {
          message.error(errMsg(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  const doBatchCopy = async () => {
    if (!copyTarget) return message.warning('请选择要复制到的会计期间')
    setBusy(true)
    try {
      const r = await api.batchVouchers({
        ids: selected,
        action: 'copy',
        target_period: copyTarget,
      })
      message.success(
        `已复制 ${r.done} 张到 ${copyTarget}：${r.nos.slice(0, 4).join('、')}${r.nos.length > 4 ? ' 等' : ''}`,
      )
      setCopyOpen(false)
      setCopyTarget(null)
      load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const doRenumber = () => {
    if (!pf || pf !== pt) {
      message.warning('请先把期间筛选收敛到一个期间，再整理凭证号')
      return
    }
    modal.confirm({
      title: `整理 ${pf} 的凭证号`,
      content: (
        <div style={{ fontSize: 13, lineHeight: 1.9 }}>
          按凭证日期重排为 记-0001、记-0002… 的连续编号。
          <div style={{ color: '#6b7280', fontSize: 12, marginTop: 4 }}>
            作废凭证仍占号（保留痕迹），只有真正删掉的号会被补上。
          </div>
        </div>
      ),
      okText: '整理',
      onOk: async () => {
        try {
          const r = await api.renumberVouchers({ period: pf, word: '记' })
          if (r.unchanged) {
            message.info(`凭证号已经连续（共 ${r.count} 张），无需整理`)
          } else {
            message.success(`已整理 ${r.updated} 张，现为 记-0001 ~ 记-${String(r.count).padStart(4, '0')}`)
          }
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const doExport = async () => {
    setBusy(true)
    try {
      const d = await api.vouchers({
        period_from: pf || undefined,
        period_to: pt || undefined,
        status,
        q: q || undefined,
        page: 1,
        size: 5000,
        with_lines: true,
      })
      const rows = [
        ['日期', '凭证字号', '摘要', '科目编码', '科目名称', '借方金额', '贷方金额',
          '往来单位', '税率', '状态', '制单人', '附单据'],
      ]
      for (const v of d.rows) {
        for (const l of v.lines || []) {
          rows.push([
            v.date, v.no, l.summary, l.account_code, l.account_name,
            l.debit ? Number(l.debit).toFixed(2) : '',
            l.credit ? Number(l.credit).toFixed(2) : '',
            l.aux_name || '',
            l.tax_rate ? `${l.tax_rate / 100}%` : '',
            STATUS[v.status]?.label || v.status,
            v.maker || '',
            v.attachments || '',
          ])
        }
      }
      const label = isAll ? '全部期间' : pf === pt ? pf : `${pf || '最早'}-${pt || '最新'}`
      downloadCsv(`凭证_${label}.csv`, rows)
      message.success(`已导出 ${d.rows.length} 张凭证、${rows.length - 1} 行分录`)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const moreMenu = {
    items: [
      { key: 'export', icon: <ExportOutlined />, label: '导出为 Excel（CSV）' },
      ...(!readOnly ? [{ key: 'renumber', icon: <OrderedListOutlined />, label: '整理本期凭证号' }] : []),
    ],
    onClick: ({ key }) => (key === 'export' ? doExport() : doRenumber()),
  }

  const isClosed = (code) => meta.periods.find((p) => p.code === code)?.status === 'closed'
  const openPeriod = meta.open_period
  const curClosed = !!pf && pf === pt && isClosed(pf)

  const doCopy = (r) => {
    const target = openPeriod ? `${openPeriod}-01` : meta.today
    Modal.confirm({
      title: `复制凭证 ${r.no}`,
      width: 460,
      content: (
        <div style={{ fontSize: 13, lineHeight: 1.9 }}>
          <div>
            把这张凭证的分录（含往来单位、税率）原样复制成一张 <b>{target.slice(0, 7)}</b> 的新凭证，
            状态为「未审核」，复制后可以改日期和金额。
          </div>
          {isClosed(r.period) ? (
            <div style={{ color: '#6b7280', fontSize: 12, marginTop: 6 }}>
              原凭证所在的 {r.period} 已结账，不能直接修改，只能这样在新期间重做。
            </div>
          ) : null}
        </div>
      ),
      okText: '复制',
      onOk: async () => {
        try {
          const v = await api.copyVoucher(r.id, { date: target })
          message.success(`已复制为 ${v.no}`)
          nav(`/vouchers/${v.id}`)
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const loadLines = async (id) => {
    try {
      const v = await api.voucher(id)
      setRows((prev) => prev.map((r) => (r.id === id ? { ...r, lines: v.lines } : r)))
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const actionMenuFor = (r, editable, closed) => {
    const items = []
    if (!closed && r.status === 'audited') items.push({ key: 'unaudit', label: '反审核' })
    if (!closed && r.status !== 'void') items.push({ key: 'insert', label: '插入凭证' })
    if (!editable) items.push({ key: 'copy', label: '复制凭证' })
    if (!closed && (r.status === 'draft' || r.status === 'void')) {
      if (items.length) items.push({ type: 'divider' })
      items.push({ key: 'delete', label: '删除凭证', danger: true })
    }
    return {
      items,
      onClick: ({ key }) => {
        if (key === 'unaudit') act(r.id, 'unaudit', '已反审核')
        if (key === 'insert') nav(`/vouchers/new?after=${r.id}`)
        if (key === 'copy') doCopy(r)
        if (key === 'delete') del(r)
      },
    }
  }

  const columns = [
    {
      title: '日期',
      dataIndex: 'date',
      width: 94,
      render: (v, r) =>
        isAll || pf !== pt ? (
          <div>
            {v}
            <div style={{ fontSize: 11, color: '#9ca3af' }}>{r.period}</div>
          </div>
        ) : (
          v
        ),
    },
    {
      title: '凭证字号 / 状态',
      dataIndex: 'no',
      width: 138,
      render: (v, r) => (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 3 }}>
          <a className="mono-num" onClick={() => nav(`/vouchers/${r.id}`)}>{v}</a>
          <Tag color={STATUS[r.status]?.color} style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '17px' }}>
            {STATUS[r.status]?.label || r.status}
          </Tag>
        </div>
      ),
    },
    {
      title: '附单据', dataIndex: 'attachments', width: 58, align: 'right',
      render: (v) => (v ? `${v} 张` : '—'),
    },
    { title: '摘要', dataIndex: 'summary', ellipsis: true },
    {
      title: '金额',
      dataIndex: 'amount',
      width: 110,
      className: 'num',
      fixed: 'right',
      render: (v) => fmt(v),
    },
    {
      title: '',
      width: 118,
      fixed: 'right',
      align: 'right',
      render: (_, r) => {
        const closed = isClosed(r.period)
        const editable = !closed && r.status === 'draft'
        const menu = actionMenuFor(r, editable, closed)
        return (
          <Space size={10} wrap={false}>
            <a onClick={() => nav(`/vouchers/${r.id}`)}>{editable ? '修改' : '查看'}</a>
            {!closed && r.status === 'draft' ? (
              <a onClick={() => act(r.id, 'audit', '已审核')}>审核</a>
            ) : null}
            {menu.items.length ? (
              <Dropdown menu={menu} trigger={['click']} placement="bottomRight">
                <Button type="text" size="small" icon={<MoreOutlined />} />
              </Dropdown>
            ) : null}
            {closed ? (
              <Tooltip title={`${r.period} 已结账，凭证锁定`}>
                <span style={{ color: '#98a2b3', fontSize: 12 }}>🔒</span>
              </Tooltip>
            ) : null}
          </Space>
        )
      },
    },
  ]

  const visibleColumns = readOnly ? columns.slice(0, -1) : columns
  const canBatchAudit = selected.length > 0
  const allExpanded = expandAll

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">凭证</div>
          <div className="page-desc">
            {isAll ? '全部期间' : pf === pt ? `会计期间 ${pf}` : `${pf || '最早'} 至 ${pt || '最新'}`}
            {' · '}审核后即进入账簿与报表 · 点行首箭头可展开分录
          </div>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={busy} />
          <Dropdown menu={moreMenu} trigger={['click']}>
            <Button icon={<DownOutlined />}>更多</Button>
          </Dropdown>
          {!readOnly ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => nav('/vouchers/new')}>
              新增凭证
            </Button>
          ) : null}
        </Space>
      </div>

      <div className="filter-bar">
        <span className="lb">会计期间</span>
        <Select
          value={pf}
          onChange={(v) => pickPeriod('from', v)}
          options={periodOptions}
          placeholder="不限"
          allowClear
          style={{ width: 136 }}
          size="small"
        />
        <span className="lb">至</span>
        <Select
          value={pt}
          onChange={(v) => pickPeriod('to', v)}
          options={periodOptions}
          placeholder="不限"
          allowClear
          style={{ width: 136 }}
          size="small"
        />
        <span className="lb">快捷查询</span>
        <Select
          value={quickPeriod}
          onChange={pickQuickPeriod}
          options={periodOptions}
          placeholder="选择单期"
          allowClear
          showSearch
          style={{ width: 150 }}
          size="small"
        />
        <Button size="small" onClick={() => { setQuickPeriod(null); setPf(null); setPt(null); setPage(1) }}>
          全部
        </Button>
        <span style={{ flex: 1 }} />
        <Select
          allowClear
          placeholder="状态"
          size="small"
          style={{ width: 104 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={Object.entries(STATUS).map(([k, v]) => ({ value: k, label: v.label }))}
        />
        <Input.Search
          placeholder="搜索摘要或往来单位"
          allowClear
          size="small"
          style={{ width: 200 }}
          onSearch={(v) => {
            setQ(v)
            setPage(1)
            setTimeout(load, 0)
          }}
        />
        <span className="lb">展开分录</span>
        <Switch size="small" checked={expandAll} onChange={setExpandAll} />
      </div>

      {curClosed ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`${pf} 已结账，本期凭证已锁定，只能查看或复制`}
          description={
            <span style={{ fontSize: 12 }}>
              新增凭证会记在 <b>{openPeriod || '—'}</b>。要真正修改 {pf} 的账，
              请到「期末处理」反结账。
            </span>
          }
        />
      ) : null}

      {selected.length ? (
        <div
          style={{
            marginBottom: 10,
            padding: '6px 12px',
            background: '#eef4fd',
            border: '1px solid #cfe0f7',
            borderRadius: 4,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            fontSize: 13,
          }}
        >
          <span>已选 <b>{selected.length}</b> 张</span>
          <Button size="small" icon={<CheckCircleOutlined />} loading={busy} onClick={() => batch('audit')}>
            批量审核
          </Button>
          <Button size="small" icon={<CopyOutlined />} onClick={() => setCopyOpen(true)}>
            批量复制
          </Button>
          <Button size="small" danger icon={<DeleteOutlined />} loading={busy} onClick={() => batch('delete')}>
            批量删除
          </Button>
          <a style={{ marginLeft: 'auto', fontSize: 12 }} onClick={() => setSelected([])}>取消选择</a>
        </div>
      ) : null}

      <Card size="small" bordered={false}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={visibleColumns}
          dataSource={rows}
          scroll={{ x: 760 }}
          rowSelection={readOnly ? undefined : { selectedRowKeys: selected, onChange: setSelected }}
          expandable={{
            expandedRowKeys: allExpanded ? rows.map((r) => r.id) : expandedKeys,
            onExpand: (expanded, r) => {
              setExpandedKeys((prev) => (expanded ? [...prev, r.id] : prev.filter((k) => k !== r.id)))
              if (expanded && !r.lines) loadLines(r.id)
            },
            expandedRowRender: (r) =>
              r.lines ? (
                <LinePanel v={r} />
              ) : (
                <div style={{ padding: '6px 0', color: '#9ca3af', fontSize: 12 }}>加载分录…</div>
              ),
          }}
          pagination={{
            current: page,
            pageSize: 50,
            total,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 张`,
            onChange: setPage,
          }}
        />
      </Card>

      <Modal
        title={`批量复制 ${selected.length} 张凭证`}
        open={copyOpen}
        onCancel={() => setCopyOpen(false)}
        onOk={doBatchCopy}
        okText="复制"
        confirmLoading={busy}
        width={460}
      >
        <div style={{ fontSize: 13, lineHeight: 1.9, marginBottom: 12 }}>
          分录（含往来单位、税率）原样复制到目标期间，日期保留原来的「日」，
          状态为「未审核」，复制后可以逐张改金额。
        </div>
        <Select
          style={{ width: '100%' }}
          placeholder="选择目标会计期间"
          value={copyTarget}
          onChange={setCopyTarget}
          options={meta.periods
            .filter((p) => p.status === 'open')
            .map((p) => ({ value: p.code, label: p.code }))}
        />
        <div style={{ color: '#9ca3af', fontSize: 12, marginTop: 8 }}>
          只列出未结账的期间。已结账的月份需要先反结账才能复制进去。
        </div>
      </Modal>
    </div>
  )
}
