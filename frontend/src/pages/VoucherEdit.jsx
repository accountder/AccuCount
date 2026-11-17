import React, { useContext, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Card, DatePicker, Input, InputNumber, Modal, Select, Space, Tag, Tooltip, Popconfirm,
  App as AntApp,
} from 'antd'
import {
  ArrowLeftOutlined, CalculatorOutlined, CopyOutlined, DeleteOutlined, PlusOutlined, SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const STATUS = {
  draft: { label: '未审核', color: 'default' },
  audited: { label: '已审核', color: 'blue' },
  posted: { label: '已记账', color: 'green' },
  void: { label: '作废', color: 'red' },
}

const emptyLine = () => ({ summary: '', account_id: null, debit: null, credit: null, tax_rate: 0 })

const RATES = [
  { value: 1300, label: '13%' },
  { value: 900, label: '9%' },
  { value: 600, label: '6%' },
  { value: 500, label: '5%' },
  { value: 300, label: '3%' },
  { value: 100, label: '1%' },
]

// ------------------------------------------------------------- 快捷键面板（复刻金蝶）
const SHORTCUT_GROUPS = [
  {
    name: '凭证',
    rows: [
      ['Ctrl+S', '保存'],
      ['F12（+小键盘 +）', '保存并新增'],
    ],
  },
  {
    name: '凭证分录',
    rows: [
      ['“/”（或小键盘 /）', '复制第一条分录的摘要'],
      ['“..”（连续两个句号）', '复制上一条分录的摘要'],
      ['Enter（借贷方金额后）', '开始录入第二条分录'],
      ['=', '自动平衡借贷方金额'],
      ['Ctrl+F7', '调用计算器'],
      ['空格键', '借贷方金额互换'],
      ['Y', '携带科目余额'],
    ],
  },
  {
    name: '焦点跳转',
    rows: [
      ['Tab / Enter', '光标跳转到下一个输入框'],
      ['Shift+Tab / Shift+Enter', '光标跳转到上一个输入框'],
    ],
  },
]

function KKey({ x, y, w = 18, h = 16, label = '', hl = false, fs = 7 }) {
  return (
    <g>
      <rect
        x={x} y={y} width={w} height={h} rx={2}
        fill={hl ? '#dbeafe' : '#f8fafc'}
        stroke={hl ? '#2563eb' : '#cbd5e1'}
      />
      {label ? (
        <text
          x={x + w / 2} y={y + h / 2 + fs * 0.36}
          textAnchor="middle" fontSize={fs} fill="#475569"
        >
          {label}
        </text>
      ) : null}
    </g>
  )
}

/** 迷你键盘示意图，高亮凭证录入用到的键 */
function MiniKeyboard() {
  const g = 2
  const row = (y, keys) => {
    let x = 0
    return keys.map((k, i) => {
      const w = k.w || 18
      const el = <KKey key={i} x={x} y={y} w={w} h={16} label={k.l} hl={k.hl} />
      x += w + g
      return el
    })
  }
  const npad = (y, keys, x0 = 396) => {
    let x = x0
    return keys.map((k, i) => {
      const w = k.w || 18
      const el = <KKey key={`n${i}`} x={x} y={y} w={w} h={16} label={k.l} hl={k.hl} />
      x += w + g
      return el
    })
  }
  const K = (l, hl, w) => ({ l, hl, w })
  return (
    <svg viewBox="0 0 452 106" style={{ display: 'block', margin: '0 auto 6px', maxWidth: 440, width: '100%' }}>
      {/* 功能键行 */}
      {row(0, [K(''), K(''), K(''), K(''), K(''), K('F7', true), K(''), K(''), K(''), K(''), K(''), K('F12', true, 24)])}
      {/* 数字行 */}
      {row(18, [K(''), K(''), K(''), K(''), K(''), K(''), K(''), K(''), K(''), K(''), K('', false, 24), K('')])}
      {npad(0, [K('=', true), K('')])}
      {npad(18, [K('')])}
      {/* Tab 行 */}
      {row(36, [K('Tab', true, 30), K('Q'), K('W'), K('E'), K('R'), K('T'), K('Y', true), K('U'), K('I'), K('O'), K('P', false, 22)])}
      {npad(36, [K('*')])}
      {/* Home 行 */}
      {row(54, [K('', false, 24), K('A'), K('S', true), K('D'), K('F'), K('G'), K('H'), K('J'), K('K'), K('L', true), K('Enter', true, 40)])}
      {/* Shift 行 */}
      {row(72, [K('Shift', true, 34), K('Z'), K('X'), K('C'), K('V'), K('B'), K('N'), K('M'), K('Shift', false, 34)])}
      {/* 底行 */}
      {row(90, [K('Ctrl', true, 26), K('', false, 20), K('', false, 20), K('空格键', true, 104), K('', false, 20), K('', false, 20), K('Ctrl', false, 26)])}
      {npad(54, [K('')])}
      {npad(72, [K('')])}
      {npad(90, [K('En')])}
    </svg>
  )
}

function ShortcutPanel() {
  return (
    <div>
      <MiniKeyboard />
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 4 }}>
        {SHORTCUT_GROUPS.map((grp, gi) => (
          <div key={grp.name} style={{ display: 'flex', borderBottom: gi < SHORTCUT_GROUPS.length - 1 ? '1px solid #e5e7eb' : 'none' }}>
            <div
              style={{
                width: 34, background: '#f9fafb', display: 'flex', alignItems: 'center', justifyContent: 'center',
                borderRight: '1px solid #e5e7eb', flexShrink: 0,
              }}
            >
              <span style={{ writingMode: 'vertical-rl', letterSpacing: 6, fontSize: 13, fontWeight: 600, color: '#374151' }}>
                {grp.name}
              </span>
            </div>
            <div style={{ flex: 1 }}>
              {grp.rows.map(([k, d]) => (
                <div key={k + d} style={{ display: 'flex', borderBottom: '1px solid #f3f4f6' }}>
                  <div style={{ width: '46%', padding: '7px 14px', fontSize: 13, color: '#111827', whiteSpace: 'nowrap' }}>{k}</div>
                  <div style={{ flex: 1, padding: '7px 14px', fontSize: 13, color: '#374151' }}>{d}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ------------------------------------------------------------- 简易计算器
const CALC_KEYS = ['7', '8', '9', '÷', '4', '5', '6', '×', '1', '2', '3', '−', '0', '.', '=', '+']

function CalculatorModal({ open, onClose }) {
  const [expr, setExpr] = useState('')
  const { message } = AntApp.useApp()
  const push = (k) => {
    if (k === '=') {
      try {
        const e = expr.replace(/×/g, '*').replace(/÷/g, '/').replace(/−/g, '-')
        if (!e || !/^[\d+\-*/.() ]+$/.test(e)) throw new Error('bad')
        // eslint-disable-next-line no-new-func
        const r = Function(`"use strict";return (${e})`)()
        if (!isFinite(r)) throw new Error('bad')
        setExpr(String(Math.round(r * 100) / 100))
      } catch {
        message.warning('表达式有误')
      }
      return
    }
    setExpr((v) => v + k)
  }
  return (
    <Modal title="计算器" open={open} onCancel={onClose} footer={null} width={260} destroyOnClose>
      <Input
        value={expr}
        onChange={(e) => setExpr(e.target.value)}
        onPressEnter={() => push('=')}
        style={{ marginBottom: 10, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 16 }}
        placeholder="0"
      />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, marginBottom: 8 }}>
        {CALC_KEYS.map((k) => (
          <Button key={k} onClick={() => push(k)} style={{ fontWeight: 600 }}>
            {k}
          </Button>
        ))}
      </div>
      <Space>
        <Button
          onClick={() => setExpr('')}
          style={{ flex: 1 }}
        >
          清空
        </Button>
        <Button onClick={() => setExpr((v) => v.slice(0, -1))}>退格</Button>
      </Space>
    </Modal>
  )
}


/** 人民币大写 */
function toChinese(n) {
  if (!n) return '零元整'
  const digits = '零壹贰叁肆伍陆柒捌玖'
  const units = ['', '拾', '佰', '仟']
  const big = ['', '万', '亿', '兆']
  const neg = n < 0
  let x = Math.round(Math.abs(n) * 100)
  const fen = x % 10
  const jiao = Math.floor(x / 10) % 10
  let intPart = Math.floor(x / 100)

  let s = ''
  let grp = 0
  if (intPart === 0) s = '零'
  while (intPart > 0) {
    const g = intPart % 10000
    let gs = ''
    let zero = false
    let t = g
    let pos = 0
    while (t > 0) {
      const d = t % 10
      if (d === 0) {
        if (!zero && gs) {
          gs = '零' + gs
          zero = true
        }
      } else {
        gs = digits[d] + units[pos] + gs
        zero = false
      }
      t = Math.floor(t / 10)
      pos += 1
    }
    s = gs + big[grp] + s
    intPart = Math.floor(intPart / 10000)
    grp += 1
  }
  s = (neg ? '负' : '') + s.replace(/零+/g, '零').replace(/零$/, '') + '元'
  if (jiao === 0 && fen === 0) return s + '整'
  if (jiao === 0) return s + '零' + digits[fen] + '分'
  if (fen === 0) return s + digits[jiao] + '角'
  return s + digits[jiao] + '角' + digits[fen] + '分'
}

export default function VoucherEdit() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const insertAfter = params.get('after')
  const nav = useNavigate()
  const { period, meta, user } = useContext(AppCtx)
  const canWrite = user?.role !== 'viewer'
  const { message } = AntApp.useApp()

  const [accounts, setAccounts] = useState([])
  const [voucher, setVoucher] = useState(null)
  const [date, setDate] = useState(() => {
    // 新建时不能落在已结账期间：当前期间关了就用「当前可记账期间」
    const cur = meta.periods.find((p) => p.code === period)
    const base = cur?.status === 'closed' ? meta.open_period || period : period
    return dayjs(`${base}-01`)
  })
  const [word, setWord] = useState('记')
  const [attachments, setAttachments] = useState(0)
  const [note, setNote] = useState('')
  const [lines, setLines] = useState([emptyLine(), emptyLine()])
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(!!id)
  const [partners, setPartners] = useState([])

  const readOnly = !canWrite || (voucher ? voucher.status !== 'draft' : false)
  const periodOfDate = date.format('YYYY-MM')
  const closedPeriod = meta.periods.find((p) => p.code === periodOfDate)?.status === 'closed'

  useEffect(() => {
    api.accounts({ only_enabled: true }).then((list) => {
      setAccounts(list.filter((a) => a.is_leaf && a.enabled))
    })
    api.partnerOptions().then(setPartners).catch(() => {})
  }, [])

  useEffect(() => {
    if (!id) return
    setLoading(true)
    api
      .voucher(id)
      .then((v) => {
        setVoucher(v)
        setDate(dayjs(v.date))
        setWord(v.word)
        setAttachments(v.attachments)
        setNote(v.note)
        setLines(
          v.lines.length
            ? v.lines.map((l) => ({
                summary: l.summary,
                account_id: l.account_id,
                debit: l.debit || null,
                credit: l.credit || null,
                aux_type: l.aux_type,
                aux_name: l.aux_name,
                tax_rate: l.tax_rate || 0,
              }))
            : [emptyLine(), emptyLine()],
        )
      })
      .catch((e) => message.error(errMsg(e)))
      .finally(() => setLoading(false))
  }, [id])

  const options = useMemo(
    () => accounts.map((a) => ({ value: a.id, label: `${a.code}  ${a.name}`, code: a.code, name: a.name })),
    [accounts],
  )
  const accMap = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts])
  const partnerOpts = useMemo(() => {
    const m = { customer: [], supplier: [], employee: [], dept: [], project: [] }
    for (const p of partners) {
      ;(m[p.ptype] = m[p.ptype] || []).push({ value: p.name, label: p.name })
    }
    return m
  }, [partners])

  const auxTypeOf = (acc) => ((acc?.aux_types || '').split(',')[0] || '').trim()
  const isTaxAcc = (acc) => /^2221\.01\./.test(acc?.code || '')

  const totals = useMemo(() => {
    const dr = lines.reduce((s, l) => s + (Number(l.debit) || 0), 0)
    const cr = lines.reduce((s, l) => s + (Number(l.credit) || 0), 0)
    return { dr, cr, diff: dr - cr }
  }, [lines])

  const setLine = (i, patch) => {
    setLines((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)))
  }

  const addLine = () => setLines((prev) => [...prev, emptyLine()])

  const removeLine = (i) => {
    setLines((prev) => (prev.length <= 1 ? prev : prev.filter((_, idx) => idx !== i)))
  }

  // ---------------- 金蝶式快捷键 ----------------
  const [keysOpen, setKeysOpen] = useState(false)
  const [calcOpen, setCalcOpen] = useState(false)
  const rowRefs = useRef({})
  const setRef = (i, field) => (el) => {
    if (!el) return
    if (!rowRefs.current[i]) rowRefs.current[i] = {}
    rowRefs.current[i][field] = el
  }
  const round2 = (v) => Math.round((Number(v) || 0) * 100) / 100

  const focusCell = (i, field) => {
    const el = rowRefs.current[i]?.[field]
    if (el?.focus) {
      el.focus()
      // 输入框聚焦后全选，方便直接改数
      if (el.select && el.input) setTimeout(() => el.select?.(), 30)
    }
  }

  const FIELDS = ['summary', 'account', 'debit', 'credit']
  const jump = (i, field, back = false) => {
    let ri = i
    let fi = FIELDS.indexOf(field)
    for (let step = 0; step < 64; step += 1) {
      fi += back ? -1 : 1
      if (fi >= FIELDS.length) { ri += 1; fi = 0 }
      if (fi < 0) { ri -= 1; fi = FIELDS.length - 1 }
      if (ri < 0 || ri >= lines.length) return
      if (rowRefs.current[ri]?.[FIELDS[fi]]) return focusCell(ri, FIELDS[fi])
    }
  }

  const copyFirstSummary = (i) => {
    const s = lines.find((l) => l.summary)?.summary
    if (!s) return message.warning('第一条分录还没有摘要')
    setLine(i, { summary: s })
  }
  const copyPrevSummary = (i) => {
    if (i === 0) return message.warning('已经是第一行')
    let s = ''
    for (let j = i - 1; j >= 0; j -= 1) {
      if (lines[j].summary) { s = lines[j].summary; break }
    }
    if (!s) return message.warning('上一行没有摘要')
    setLine(i, { summary: s })
  }

  /** 按 Y 携带科目余额：借方科目带入借方，贷方科目带入贷方 */
  const carryBalance = async (i) => {
    const accId = lines[i]?.account_id
    if (!accId) return message.warning('请先选择科目，再按 Y 携带余额')
    try {
      const b = await api.accountBalance(accId, periodOfDate)
      const v = round2(Math.abs(b.closing))
      if (!v) return message.info(`${b.code} ${b.name} 当前余额为 0`)
      if (b.direction === 'dr') setLine(i, { debit: v, credit: null })
      else setLine(i, { credit: v, debit: null })
      message.info(`${b.code} ${b.name} ${periodOfDate} 期末余额 ${fmt(v)} 元，已带入${b.direction === 'dr' ? '借' : '贷'}方`)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  /** 金额框按键：= 自动平衡、空格借贷互换、Enter/Shift+Enter 跳焦点 */
  const amountKeyDown = (i, field) => (e) => {
    if (e.key === '=' && !e.ctrlKey && !e.altKey) {
      e.preventDefault()
      if (field === 'debit') {
        const other = totals.dr - (Number(lines[i].debit) || 0)
        const v = round2(totals.cr - other)
        if (v > 0) setLine(i, { debit: v })
      } else {
        const other = totals.cr - (Number(lines[i].credit) || 0)
        const v = round2(totals.dr - other)
        if (v > 0) setLine(i, { credit: v })
      }
      return
    }
    if (e.key === ' ' && !e.ctrlKey && !e.altKey) {
      e.preventDefault()
      const l = lines[i]
      if (l.debit || l.credit) setLine(i, { debit: l.credit, credit: l.debit })
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (e.shiftKey) return jump(i, field, true)
      if (field === 'credit' && i === lines.length - 1) {
        addLine()
        setTimeout(() => focusCell(i + 1, 'summary'), 60)
        return
      }
      jump(i, field)
    }
  }

  /** 摘要框按键：Enter/Shift+Enter 跳焦点（“/”和“..”在 onChange 里检测） */
  const summaryKeyDown = (i) => (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      jump(i, 'summary', e.shiftKey)
    }
  }

  // 全局快捷键：Ctrl+S 保存、F12 保存并新增、Ctrl+F7 计算器
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault()
        if (canEdit) save(false, false)
      } else if (e.key === 'F12') {
        e.preventDefault()
        if (canEdit && !id) save(false, true)
      } else if ((e.ctrlKey || e.metaKey) && e.key === 'F7') {
        e.preventDefault()
        setCalcOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const payload = () => {
    const clean = lines.filter(
      (l) => l.account_id && (Number(l.debit) || Number(l.credit)),
    )
    return {
      date: date.format('YYYY-MM-DD'),
      word,
      attachments: Number(attachments) || 0,
      note,
      lines: clean.map((l) => ({
        summary: l.summary || '',
        account_id: l.account_id,
        debit: Number(l.debit) || 0,
        credit: Number(l.credit) || 0,
        aux_type: l.aux_type || '',
        aux_name: l.aux_name || '',
        tax_rate: l.tax_rate || 0,
      })),
    }
  }

  const save = async (thenAudit = false, thenNew = false) => {
    const p = payload()
    if (!p.lines.length) return message.warning('请至少录入一行有效分录')
    if (totals.diff !== 0) {
      return message.error(`借贷不平衡，差额 ${fmt(totals.diff)} 元，无法保存`)
    }
    setSaving(true)
    try {
      let saved
      if (id) saved = await api.updateVoucher(Number(id), p)
      else
        saved = await api.createVoucher(
          p,
          insertAfter ? { insert_after: Number(insertAfter) } : undefined,
        )
      if (thenAudit) {
        await api.voucherAction(saved.id, 'audit')
        message.success(`${saved.no} 已保存并审核`)
      } else {
        message.success(thenNew ? `${saved.no} 已保存，继续录入下一张` : '已保存')
      }
      if (thenNew) {
        setVoucher(null)
        setLines([emptyLine(), emptyLine()])
        setNote('')
        setAttachments(0)
        nav('/vouchers/new', { replace: true })
      } else {
        nav(`/vouchers/${saved.id}`, { replace: true })
        if (id) {
          const v = await api.voucher(saved.id)
          setVoucher(v)
        }
      }
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  const doAct = async (a, ok) => {
    try {
      await api.voucherAction(Number(id), a)
      message.success(ok)
      const v = await api.voucher(Number(id))
      setVoucher(v)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const remove = async () => {
    try {
      await api.deleteVoucher(Number(id))
      message.success('已删除')
      nav('/vouchers')
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doCopy = async () => {
    const target = meta.open_period ? `${meta.open_period}-01` : meta.today
    try {
      const v = await api.copyVoucher(Number(id), { date: target })
      message.success(`已复制为 ${v.no}（${v.period}）`)
      nav(`/vouchers/${v.id}`, { replace: true })
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const canEdit = !readOnly && !closedPeriod
  const showCopy = !!voucher && !canEdit && canWrite

  const cellStyle = { padding: '2px 4px', verticalAlign: 'middle' }
  const numInput = {
    style: { width: '100%', textAlign: 'right', fontVariantNumeric: 'tabular-nums' },
    precision: 2,
    min: 0,
    controls: false,
    placeholder: '0.00',
  }

  return (
    <div>
      <div className="page-head">
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => nav('/vouchers')}>
            返回
          </Button>
          <div>
            <div className="page-title">
              {voucher ? `记账凭证 ${voucher.no}` : '新增记账凭证'}
              {voucher ? (
                <Tag color={STATUS[voucher.status]?.color} style={{ marginLeft: 8 }}>
                  {STATUS[voucher.status]?.label}
                </Tag>
              ) : null}
            </div>
            <div className="page-desc">
              {voucher
                ? `期间 ${voucher.period} · 制单 ${voucher.maker || '—'}`
                : `会计期间 ${periodOfDate}${periodOfDate !== period ? ` · ${period} 已结账，自动记到 ${periodOfDate}` : ''}`}
            </div>
          </div>
        </Space>
        <Space>
          {canEdit ? (
            <>
              <Button icon={<SaveOutlined />} loading={saving} onClick={() => save(false, false)}>
                保存
              </Button>
              {!id ? (
                <Button loading={saving} onClick={() => save(false, true)}>
                  保存并新增
                </Button>
              ) : null}
              <Button type="primary" loading={saving} onClick={() => save(true, false)}>
                保存并审核
              </Button>
            </>
          ) : null}
          {canWrite && voucher?.status === 'draft' && !closedPeriod ? (
            <Button onClick={() => doAct('audit', '已审核')}>审核</Button>
          ) : null}
          {canWrite && voucher?.status === 'audited' && !closedPeriod ? (
            <>
              <Button type="primary" onClick={() => doAct('post', '已记账')}>记账</Button>
              <Button onClick={() => doAct('unaudit', '已反审核')}>反审核</Button>
            </>
          ) : null}
          {showCopy ? (
            <Tooltip title={`把这张凭证复制成 ${meta.open_period || '本期'} 的新凭证，原凭证不动`}>
              <Button icon={<CopyOutlined />} onClick={doCopy}>
                复制为新凭证
              </Button>
            </Tooltip>
          ) : null}
          {canWrite && voucher && (voucher.status === 'draft' || voucher.status === 'void') && !closedPeriod ? (
            <Popconfirm title="确认删除该凭证？" onConfirm={remove} okButtonProps={{ danger: true }}>
              <Button danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          ) : null}
        </Space>
      </div>

      {closedPeriod ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`${periodOfDate} 已结账，本凭证已锁定`}
          description={
            <span style={{ fontSize: 12 }}>
              不能直接修改。需要照这个业务再做一张的话，点右上角「复制为新凭证」，
              会复制到 {meta.open_period || '—'}；要真正改这张，请到「期末处理」反结账。
            </span>
          }
        />
      ) : null}

      <Card size="small" bordered={false} loading={loading} bodyStyle={{ padding: 0 }}>
        <div className="voucher-paper">
          <div className="voucher-title-row">
            <span className="t">记账凭证</span>
            <span className="p">{periodOfDate.replace('-', ' 年 ')} 月</span>
          </div>
          <Space size={20} wrap style={{ marginBottom: 10 }}>
            <span>
              <span style={{ color: '#6b7280', marginRight: 6, fontSize: 12 }}>日期</span>
              <DatePicker
                value={date}
                onChange={(d) => d && setDate(d)}
                allowClear={false}
                disabled={readOnly || closedPeriod}
                style={{ width: 140 }}
                format="YYYY-MM-DD"
                disabledDate={(d) => {
                  const p = meta.periods.find((x) => x.code === d.format('YYYY-MM'))
                  return !p || p.status === 'closed'
                }}
              />
            </span>
            <span>
              <span style={{ color: '#6b7280', marginRight: 6, fontSize: 12 }}>凭证字</span>
              <Select
                value={word}
                onChange={setWord}
                disabled={readOnly || closedPeriod}
                style={{ width: 78 }}
                options={['记', '收', '付', '转'].map((v) => ({ value: v, label: v }))}
              />
            </span>
            <span>
              <span style={{ color: '#6b7280', marginRight: 6, fontSize: 12 }}>附单据</span>
              <InputNumber
                value={attachments}
                onChange={setAttachments}
                disabled={readOnly || closedPeriod}
                min={0}
                controls={false}
                style={{ width: 70, textAlign: 'right' }}
              />
              <span style={{ color: '#6b7280', marginLeft: 6, fontSize: 12 }}>张</span>
            </span>
            <span style={{ flex: 1 }} />
          </Space>

          <table className="fin" style={{ tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: 38 }} />
              <col style={{ width: 226 }} />
              <col />
              <col style={{ width: 168 }} />
              <col style={{ width: 140 }} />
              <col style={{ width: 140 }} />
              <col style={{ width: 44 }} />
            </colgroup>
            <thead>
              <tr>
                <th>#</th>
                <th>摘要</th>
                <th>会计科目</th>
                <th>往来单位 / 税率</th>
                <th>借方金额</th>
                <th>贷方金额</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => {
                const acc = accMap.get(l.account_id)
                return (
                  <tr key={i}>
                    <td style={{ textAlign: 'center', color: '#9ca3af' }}>{i + 1}</td>
                    <td style={cellStyle}>
                      <Input
                        ref={setRef(i, 'summary')}
                        value={l.summary}
                        onChange={(e) => {
                          const v = e.target.value
                          if (v === '/') return copyFirstSummary(i)
                          if (v === '..') return copyPrevSummary(i)
                          setLine(i, { summary: v })
                        }}
                        onKeyDown={summaryKeyDown(i)}
                        disabled={readOnly || closedPeriod}
                        placeholder="业务说明"
                        variant="borderless"
                      />
                    </td>
                    <td style={cellStyle}>
                      <Select
                        ref={setRef(i, 'account')}
                        showSearch
                        value={l.account_id}
                        onChange={(v) => {
                          const a = accMap.get(v)
                          setLine(i, { account_id: v, aux_type: auxTypeOf(a), aux_name: '' })
                        }}
                        disabled={readOnly || closedPeriod}
                        placeholder="搜索编码或名称"
                        style={{ width: '100%' }}
                        variant="borderless"
                        options={options}
                        filterOption={(input, opt) =>
                          opt.code.includes(input) || opt.name.includes(input)
                        }
                        optionRender={(opt) => (
                          <div style={{ display: 'flex', gap: 8 }}>
                            <span className="mono-num" style={{ color: '#6b7280' }}>{opt.data.code}</span>
                            <span>{opt.data.name}</span>
                          </div>
                        )}
                        onKeyDown={(e) => {
                          // 按 Y 携带该科目的期末余额（金蝶式）
                          if ((e.key === 'y' || e.key === 'Y') && !e.ctrlKey && !e.altKey && !e.metaKey) {
                            e.preventDefault()
                            carryBalance(i)
                          }
                        }}
                      />
                    </td>
                    <td style={cellStyle}>
                      {auxTypeOf(acc) ? (
                        <Select
                          allowClear
                          showSearch
                          value={l.aux_name || undefined}
                          onChange={(v) => setLine(i, { aux_name: v || '', aux_type: auxTypeOf(acc) })}
                          disabled={readOnly || closedPeriod}
                          placeholder={auxTypeOf(acc) === 'customer' ? '选客户' : '选供应商'}
                          style={{ width: '100%' }}
                          variant="borderless"
                          options={partnerOpts[auxTypeOf(acc)] || []}
                          optionFilterProp="label"
                          notFoundContent={<span style={{ fontSize: 12 }}>先在「往来单位」建档</span>}
                        />
                      ) : isTaxAcc(acc) ? (
                        <Select
                          allowClear
                          value={l.tax_rate || undefined}
                          onChange={(v) => setLine(i, { tax_rate: v || 0 })}
                          disabled={readOnly || closedPeriod}
                          placeholder="选税率"
                          style={{ width: '100%' }}
                          variant="borderless"
                          options={RATES}
                        />
                      ) : null}
                    </td>
                    <td style={cellStyle}>
                      <InputNumber
                        {...numInput}
                        ref={setRef(i, 'debit')}
                        value={l.debit}
                        disabled={readOnly || closedPeriod}
                        onChange={(v) => setLine(i, { debit: v, credit: v ? null : l.credit })}
                        onKeyDown={amountKeyDown(i, 'debit')}
                      />
                    </td>
                    <td style={cellStyle}>
                      <InputNumber
                        {...numInput}
                        ref={setRef(i, 'credit')}
                        value={l.credit}
                        disabled={readOnly || closedPeriod}
                        onChange={(v) => setLine(i, { credit: v, debit: v ? null : l.debit })}
                        onKeyDown={amountKeyDown(i, 'credit')}
                      />
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      {!readOnly && !closedPeriod && lines.length > 1 ? (
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => removeLine(i)}
                        />
                      ) : null}
                    </td>
                  </tr>
                )
              })}
              <tr className="bold">
                <td colSpan={4} style={{ textAlign: 'right' }}>
                  {totals.dr > 0 ? `合计：${toChinese(totals.dr)}` : '合计'}
                </td>
                <td className="amt">{totals.dr ? fmt(totals.dr) : ''}</td>
                <td className="amt">{totals.cr ? fmt(totals.cr) : ''}</td>
                <td />
              </tr>
            </tbody>
          </table>

          <Space style={{ marginTop: 10 }} wrap>
            {!readOnly && !closedPeriod ? (
              <Button size="small" icon={<PlusOutlined />} onClick={addLine}>
                加一行
              </Button>
            ) : null}
            <Button size="small" icon={<ThunderboltOutlined />} onClick={() => setKeysOpen(true)}>
              快捷键
            </Button>
            <Button size="small" icon={<CalculatorOutlined />} onClick={() => setCalcOpen(true)}>
              计算器
            </Button>
            <span
              className={`balance-hint ${
                totals.dr === 0 && totals.cr === 0
                  ? 'balance-idle'
                  : totals.diff === 0
                    ? 'balance-ok'
                    : 'balance-bad'
              }`}
            >
              {totals.dr === 0 && totals.cr === 0
                ? '尚未录入金额'
                : totals.diff === 0
                  ? `借贷平衡 ${fmt(totals.dr)} 元`
                  : `借贷不平衡，差额 ${fmt(totals.diff)} 元`}
            </span>
            {closedPeriod ? <Tag color="orange">本期已结账，不可修改</Tag> : null}
          </Space>

          <div style={{ marginTop: 12 }}>
            <Input.TextArea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={readOnly || closedPeriod}
              placeholder="备注（可选）"
              autoSize={{ minRows: 1, maxRows: 3 }}
              style={{ fontSize: 12 }}
            />
          </div>

          <div className="voucher-sign">
            <span>制单人：{voucher?.maker || user?.name || user?.username || ''}</span>
            <span>审核人：{voucher?.auditor || ''}</span>
            <span>记账人：{voucher?.poster || ''}</span>
          </div>

          <div className="voucher-tip">
            注：结转损益请用「期末处理 → 结转损益」自动生成，不要手工录入，
            否则利润表取数会受影响。已结账期间的凭证不能修改，需要重做请用「复制为新凭证」。
          </div>
        </div>
      </Card>

      <Modal
        title="录入凭证快捷键"
        open={keysOpen}
        onCancel={() => setKeysOpen(false)}
        footer={null}
        width={560}
      >
        <ShortcutPanel />
      </Modal>

      <CalculatorModal open={calcOpen} onClose={() => setCalcOpen(false)} />
    </div>
  )
}
