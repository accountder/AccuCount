import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Drawer, Empty, Popconfirm, Progress, Space, Spin,
  Tag, Tooltip,
} from 'antd'
import {
  CarryOutOutlined, PercentageOutlined, PlusOutlined, ProfileOutlined,
  ReloadOutlined, SettingOutlined, SwapOutlined, UnlockOutlined,
} from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'
import SurtaxCalculator from '../components/SurtaxCalculator'
import DepreciationCalculator from '../components/DepreciationCalculator'
import IncomeTaxCalculator from '../components/IncomeTaxCalculator'

const CARD_META = {
  vat_transfer: {
    icon: <CarryOutOutlined />,
    tone: 'blue',
    desc: '转出未交增值税',
    action: '生成凭证',
  },
  income_tax: {
    icon: <PercentageOutlined />,
    tone: 'amber',
    desc: '季度企业所得税预提',
    action: '测算确认',
  },
  surtax: {
    icon: <PercentageOutlined />,
    tone: 'cyan',
    desc: '增值税及附加测算',
    action: '测算确认',
  },
  depreciation: {
    icon: <ProfileOutlined />,
    tone: 'indigo',
    desc: '固定资产折旧',
    action: '生成凭证',
  },
  settle: {
    icon: <SwapOutlined />,
    tone: 'teal',
    desc: 'FIFO 往来核销',
    action: '执行核销',
  },
  year_close: {
    icon: <CarryOutOutlined />,
    tone: 'navy',
    desc: '本年利润转入利润分配',
    action: '自动结转',
  },
  carryover: {
    icon: <CarryOutOutlined />,
    tone: 'navy',
    desc: '结转到下期时自动完成',
    action: '自动结转',
  },
}

function cardStatusColor(status) {
  if (status === 'audited' || status === 'posted') return 'green'
  if (status === 'draft') return 'orange'
  if (status === 'ready' || status === 'confirmed') return 'blue'
  return 'default'
}

export default function Periods() {
  const { meta, reloadMeta, user, readOnly } = useContext(AppCtx)
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busyKey, setBusyKey] = useState('')
  const [drawer, setDrawer] = useState(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const { message } = AntApp.useApp()
  const nav = useNavigate()
  const currentPeriod = meta.open_period
  const currentRow = useMemo(
    () => meta.periods.find((p) => p.code === currentPeriod),
    [meta.periods, currentPeriod],
  )

  const load = async () => {
    if (!currentPeriod) return
    setLoading(true)
    try {
      setSummary(await api.closingSummary(currentPeriod))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [currentPeriod])

  useEffect(() => {
    const card = searchParams.get('card')
    if (card === 'surtax' || card === 'depreciation') setDrawer(card)
  }, [])

  const closeDrawer = () => {
    setDrawer(null)
    if (searchParams.get('card')) setSearchParams({}, { replace: true })
    load()
  }

  const runAction = async (key, fn, ok) => {
    setBusyKey(key)
    try {
      const result = await fn()
      message.success(typeof ok === 'function' ? ok(result) : ok)
      await load()
      return result
    } catch (e) {
      message.error(errMsg(e))
      return null
    } finally {
      setBusyKey('')
    }
  }

  const generateVatTransfer = () => {
    runAction(
      'vat_transfer',
      () => api.transferVat(currentPeriod),
      (r) => `已生成结转未交增值税凭证 ${r.voucher_no}，金额 ${fmt(r.amount)} 元`,
    )
  }

  const generateDepreciation = () => {
    runAction(
      'depreciation',
      () => api.postDepreciation({ period: currentPeriod }),
      (r) => `已生成折旧凭证 ${r.voucher_no}，金额 ${fmt(r.total)} 元`,
    )
  }

  const runSettlement = () => {
    runAction(
      'settle',
      () => api.runSettle({ period: currentPeriod }),
      (r) => `已核销 ${r.created} 笔，金额 ${fmt(r.amount)} 元`,
    )
  }

  const generateSurtax = () => {
    runAction(
      'surtax',
      () => api.createSurtaxVoucher(currentPeriod),
      (r) => `已生成附加税凭证 ${r.voucher_no}，实际附加税 ${fmt(r.total)} 元`,
    )
  }

  const generateIncomeTax = () => {
    runAction(
      'income_tax',
      () => api.createIncomeTaxVoucher(currentPeriod),
      (r) => `已生成企业所得税凭证 ${r.voucher_no}，金额 ${fmt(r.amount)} 元`,
    )
  }

  const closePeriod = async () => {
    setBusyKey('close')
    try {
      const r = await api.closePeriod(currentPeriod)
      const yearText = r.year_close_no ? `，年度结转凭证 ${r.year_close_no}` : ''
      message.success(
        r.carryover_no
          ? `已结转到下期，结转凭证 ${r.carryover_no}${r.profit != null ? `，本期利润 ${fmt(r.profit)} 元` : ''}${yearText}`
          : `已结转到下期，本期无需结转损益${yearText}`,
      )
      await reloadMeta()
      await load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusyKey('')
    }
  }

  const reopenLast = async () => {
    if (!meta.last_closed_period) return
    setBusyKey('reopen')
    try {
      await api.reopenPeriod(meta.last_closed_period)
      message.success('已反结账最近期间')
      await reloadMeta()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusyKey('')
    }
  }

  const openVoucher = (voucher) => {
    if (voucher?.id) nav(`/vouchers/${voucher.id}`)
  }

  const cardAction = (card) => {
    if (card.voucher) {
      openVoucher(card.voucher)
      return
    }
    if (card.key === 'surtax') {
      if (card.status === 'confirmed') generateSurtax()
      else setDrawer('surtax')
      return
    }
    if (card.key === 'income_tax') {
      if (card.status === 'confirmed') generateIncomeTax()
      else setDrawer('income_tax')
      return
    }
    if (card.key === 'depreciation') {
      if (card.can_generate) generateDepreciation()
      else setDrawer('depreciation')
      return
    }
    if (card.key === 'vat_transfer' && card.can_generate) {
      generateVatTransfer()
      return
    }
    if (card.key === 'settle' && card.status === 'ready') {
      runSettlement()
    }
  }

  const actionLabel = (card) => {
    if (card.voucher) return '查看凭证'
    if (card.key === 'surtax') return card.status === 'confirmed' ? '生成凭证' : '测算确认'
    if (card.key === 'income_tax') return card.status === 'confirmed' ? (card.amount < 0 ? '生成冲回凭证' : '生成凭证') : '测算确认'
    if (card.key === 'depreciation') return card.can_generate ? '生成凭证' : '查看测算'
    if (card.key === 'settle') return card.status === 'ready' ? '执行核销' : '无需核销'
    if (card.key === 'year_close') return '自动结转'
    if (card.key === 'carryover') return '自动结转'
    return CARD_META[card.key]?.action || '处理'
  }

  const actionDisabled = (card) => {
    if (card.voucher) return false
    if (card.key === 'surtax') return false
    if (card.key === 'income_tax') return card.status !== 'confirmed' && Math.abs(card.amount || 0) === 0
    if (card.key === 'depreciation') return !card.can_generate && card.status_label === '待处理' && card.amount <= 0
    if (card.key === 'settle') return card.status !== 'ready'
    if (card.key === 'year_close') return true
    if (card.key === 'carryover') return true
    return !card.can_generate
  }

  if (!currentPeriod) {
    return (
      <div>
        <div className="page-head">
          <div>
            <div className="page-title">期末处理</div>
            <div className="page-desc">所有会计期间均已结账。</div>
          </div>
        </div>
        <Card size="small" bordered={false}>
          <Empty description="没有待结账期间" />
          {user?.role === 'admin' && meta.last_closed_period ? (
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <Button onClick={reopenLast} icon={<UnlockOutlined />}>反结账最近期间</Button>
            </div>
          ) : null}
        </Card>
      </div>
    )
  }

  const counts = summary?.counts || {}
  const requiredKeys = new Set(['vat_transfer', 'surtax', 'income_tax', 'depreciation'])
  const requiredPending = (summary?.cards || []).filter(
    (c) => requiredKeys.has(c.key) && Math.abs(c.amount || 0) > 0 && !c.voucher,
  )
  const assetOk = summary?.asset_reconciliation?.ok !== false
  const canClose = currentRow?.status === 'open' && !counts.draft && requiredPending.length === 0 && assetOk
  const completedCards = (summary?.cards || []).filter(
    (c) => ['audited', 'posted'].includes(c.status) || (c.key === 'carryover' && c.status === 'ready'),
  ).length
  const progress = summary?.cards?.length
    ? Math.round(completedCards * 100 / summary.cards.length)
    : 0

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">期末处理</div>
          <div className="page-desc">
            只处理当前待结账期间 · {currentPeriod} · 共录入凭证 {summary?.total_vouchers || 0} 张
          </div>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>测算金额</Button>
          {!readOnly ? (
            <Popconfirm
              title={`结转到下期 ${currentPeriod}？`}
              description="将自动结转损益并锁定当前期间。存在未审核凭证时不能结账。"
              okText="确认结转到下期"
              disabled={!canClose}
              onConfirm={closePeriod}
            >
              <Button
                type="primary"
                icon={<CarryOutOutlined />}
                disabled={!canClose}
                loading={busyKey === 'close'}
              >
                结转到下期
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      </div>

      <div className="period-close-hero">
        <div className="period-close-hero-main">
          <div className="period-close-kicker">当前待结账期间</div>
          <strong>{currentPeriod}</strong>
          <span>共录入凭证 {summary?.total_vouchers || 0} 张</span>
        </div>
        <div className="period-close-stats">
          <div><span>已入账</span><b>{(counts.audited || 0) + (counts.posted || 0)}</b></div>
          <div><span>待审核</span><b className={counts.draft ? 'warn' : ''}>{counts.draft || 0}</b></div>
          <div><span>作废</span><b>{counts.void || 0}</b></div>
          <div className="period-close-progress">
            <Progress type="circle" size={46} percent={progress} strokeColor="#1f5f9b" />
            <span>期末完成度</span>
          </div>
        </div>
      </div>

      {counts.draft ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 14 }}
          message={`本期还有 ${counts.draft} 张未审核凭证`}
          description="所有草稿凭证审核入账后才能结转到下期。"
        />
      ) : requiredPending.length ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 14 }}
          message="还有必做期末卡片未完成"
          description={`请先完成：${requiredPending.map((c) => c.title).join('、')}。`}
        />
      ) : !assetOk ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 14 }}
          message="固定资产卡片与总账不一致"
          description={`卡片原值 ${fmt(summary?.asset_reconciliation?.card_original)} / 总账 1601 ${fmt(summary?.asset_reconciliation?.gl_original)}；卡片累计折旧 ${fmt(summary?.asset_reconciliation?.card_accum)} / 总账 1602 ${fmt(summary?.asset_reconciliation?.gl_accum)}。`}
        />
      ) : null}

      {loading && !summary ? (
        <div className="period-loading"><Spin /></div>
      ) : (
        <div className="closing-card-grid">
          {(summary?.cards || []).map((card) => {
            const metaCard = CARD_META[card.key] || {}
            const tone = metaCard.tone || 'blue'
            return (
              <Card key={card.key} bordered={false} className={`closing-card closing-card-${tone}`}>
                <div className="closing-card-head">
                  <div className="closing-card-title">
                    {metaCard.icon}
                    <span>{card.title}</span>
                  </div>
                  {card.settings ? (
                    <Tooltip title="测算设置">
                      <Button
                        type="text"
                        size="small"
                        icon={<SettingOutlined />}
                        onClick={() => setDrawer(card.key)}
                      />
                    </Tooltip>
                  ) : null}
                </div>
                <div className="closing-card-body">
                  <span className="closing-card-label">金额</span>
                  <strong className="mono-num">{card.amount ? fmt(card.amount) : '---'}</strong>
                  <div className="closing-card-desc">{metaCard.desc}</div>
                  <Tag color={cardStatusColor(card.status)}>{card.status_label}</Tag>
                  {card.blocker ? <div className="closing-card-blocker">{card.blocker}</div> : null}
                </div>
                <div className="closing-card-foot">
                  <Button
                    size="small"
                    type={card.status === 'ready' || card.status === 'confirmed' ? 'primary' : 'default'}
                    disabled={actionDisabled(card) || (readOnly && !card.voucher)}
                    loading={busyKey === card.key}
                    onClick={() => cardAction(card)}
                  >
                    {actionLabel(card)}
                  </Button>
                </div>
              </Card>
            )
          })}

          <Card bordered={false} className="closing-card closing-card-add">
            <div className="closing-card-add-inner">
              <PlusOutlined />
              <strong>新增期末结转</strong>
              <span>后续支持自定义结转模板</span>
            </div>
          </Card>
        </div>
      )}

      <Drawer
        title="计提税金 · 增值税及附加测算"
        open={drawer === 'surtax'}
        width={1100}
        destroyOnClose
        onClose={closeDrawer}
      >
        <SurtaxCalculator onChanged={load} />
      </Drawer>

      <Drawer
        title="计提所得税 · 季度企业所得税预提"
        open={drawer === 'income_tax'}
        width={1100}
        destroyOnClose
        onClose={closeDrawer}
      >
        <IncomeTaxCalculator period={currentPeriod} onChanged={load} />
      </Drawer>

      <Drawer
        title="计提折旧 · 固定资产测算"
        open={drawer === 'depreciation'}
        width={1100}
        destroyOnClose
        onClose={closeDrawer}
      >
        <DepreciationCalculator />
      </Drawer>
    </div>
  )
}