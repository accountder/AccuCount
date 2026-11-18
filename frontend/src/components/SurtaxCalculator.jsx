import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Checkbox, Col, Divider, Form, Input, InputNumber,
  Row, Select, Space, Statistic, Switch, Table, Tag, App as AntApp,
} from 'antd'
import {
  CalculatorOutlined, CheckCircleOutlined, FileTextOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const CITY_RATES = [
  { value: 700, label: '市区 7%' },
  { value: 500, label: '县城 / 镇 5%' },
  { value: 100, label: '其他地区 1%' },
]

export default function SurtaxCalculator({ onChanged }) {
  const { period, readOnly } = useContext(AppCtx)
  const { message, modal } = AntApp.useApp()
  const [source, setSource] = useState('auto')
  const [smallMicro, setSmallMicro] = useState(true)
  const [cityRate, setCityRate] = useState(700)
  const [manualMode, setManualMode] = useState(false)
  const [manualBase, setManualBase] = useState(0)
  const [note, setNote] = useState('')
  const [preview, setPreview] = useState(null)
  const [baseConfirmed, setBaseConfirmed] = useState(false)
  const [calculationConfirmed, setCalculationConfirmed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)

  const query = useMemo(() => ({
    period,
    source,
    manual_base: manualMode ? Number(manualBase) : undefined,
    small_micro: smallMicro,
    city_rate: cityRate,
    education_rate: 300,
    local_education_rate: 200,
  }), [period, source, manualMode, manualBase, smallMicro, cityRate])

  const invalidate = () => {
    setCalculationConfirmed(false)
    setBaseConfirmed(false)
    setPreview(null)
  }

  const load = async (nextQuery = query) => {
    setLoading(true)
    try {
      const data = await api.surtaxPreview(nextQuery)
      setPreview(data)
      if (nextQuery.source === 'auto' && data.selected_source) {
        setSource(data.selected_source)
      }
      setCalculationConfirmed(Boolean(
        data.saved_confirmed && !data.existing_voucher && data.saved_calc?.net_amount === data.net_total,
      ))
    } catch (e) {
      message.error(errMsg(e))
      setPreview(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setSource('auto')
    setManualMode(false)
    setManualBase(0)
    setNote('')
    setCalculationConfirmed(false)
    setBaseConfirmed(false)
    load({
      period,
      source: 'auto',
      small_micro: smallMicro,
      city_rate: cityRate,
      education_rate: 300,
      local_education_rate: 200,
    })
  }, [period])

  const sourceOptions = (preview?.sources || [
    { value: 'transfer', label: '2221.01.05 转出未交增值税', amount: 0, available: false },
    { value: 'vat_book', label: '应交增值税自动测算', amount: 0, available: false },
  ]).map((x) => ({
    value: x.value,
    label: `${x.label}${x.available ? ` · ${fmt(x.amount)}` : ' · 无可用金额'}`,
  }))

  const doCalculate = () => {
    if (manualMode && (!Number.isFinite(Number(manualBase)) || Number(manualBase) < 0)) {
      message.error('手工计税依据不能小于 0')
      return
    }
    setCalculationConfirmed(false)
    setBaseConfirmed(false)
    load(query)
  }

  const doConfirm = async () => {
    if (!preview || !baseConfirmed) return
    setBusy(true)
    try {
      const data = await api.confirmSurtax({
        period,
        source: preview.selected_source,
        manual_base: manualMode ? Number(manualBase) : null,
        small_micro: smallMicro,
        city_rate: cityRate,
        education_rate: 300,
        local_education_rate: 200,
        note,
      })
      setPreview(data)
      setCalculationConfirmed(true)
      message.success('附加税测算已确认，可以生成凭证')
      onChanged?.()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const doGenerate = () => {
    modal.confirm({
      title: `生成 ${period} 附加税凭证`,
      content: (
        <div style={{ lineHeight: 1.9 }}>
          <div>计税依据：<b>{fmt(preview?.base_amount)}</b> 元</div>
          <div>小微企业减半：<b>{smallMicro ? '是' : '否'}</b></div>
          <div>实际应缴附加税：<b>{fmt(preview?.net_total)}</b> 元</div>
          <div style={{ color: '#6b7280', fontSize: 12, marginTop: 6 }}>
            凭证按实际应缴净额生成，状态为未审核，审核后才进入账簿。
          </div>
        </div>
      ),
      okText: '生成凭证',
      onOk: async () => {
        setBusy(true)
        try {
          const r = await api.createSurtaxVoucher(period)
          message.success(`已生成凭证 ${r.voucher_no}，实际附加税 ${fmt(r.total)} 元`)
          setCalculationConfirmed(false)
          setBaseConfirmed(false)
          await load({ ...query, source: preview?.selected_source || source })
          onChanged?.()
        } catch (e) {
          message.error(errMsg(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  const columns = [
    { title: '税种', dataIndex: 'name', width: 132 },
    {
      title: '税率', dataIndex: 'rate', width: 68, align: 'right',
      render: (v) => `${(v / 100).toFixed(2)}%`,
    },
    { title: '法定税额', dataIndex: 'gross', width: 96, align: 'right', render: (v) => fmt(v) },
    {
      title: '减半减免', dataIndex: 'reduction', width: 96, align: 'right',
      render: (v) => <span style={{ color: v ? '#1a7f45' : '#9ca3af' }}>{fmt(v)}</span>,
    },
    {
      title: '实际应缴', dataIndex: 'net', width: 96, align: 'right',
      render: (v) => <b className="mono-num">{fmt(v)}</b>,
    },
  ]

  return (
    <div className="calc-module">
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={9}>
          <Card size="small" bordered={false} className="calc-panel">
            <div className="calc-panel-title">
              <CalculatorOutlined />
              <span>附加税取数与参数</span>
            </div>
            <Form layout="vertical" size="small">
              <Form.Item label="计税依据来源" tooltip="优先从应交税费明细取数，不使用应交税费总账余额">
                <Select
                  value={source}
                  options={[{ value: 'auto', label: '自动选择可用来源' }, ...sourceOptions]}
                  onChange={(v) => { setSource(v); invalidate() }}
                />
              </Form.Item>

              <div className="calc-field-row">
                <Form.Item label="小微六税两费减半">
                  <Switch
                    checked={smallMicro}
                    onChange={(v) => { setSmallMicro(v); invalidate() }}
                    checkedChildren="50%"
                    unCheckedChildren="不减免"
                  />
                </Form.Item>
                <Form.Item label="城建税地区税率">
                  <Select
                    value={cityRate}
                    options={CITY_RATES}
                    onChange={(v) => { setCityRate(v); invalidate() }}
                  />
                </Form.Item>
              </div>

              <div className="calc-field-row">
                <Form.Item label="教育费附加">
                  <Input value="3%" disabled />
                </Form.Item>
                <Form.Item label="地方教育附加">
                  <Input value="2%" disabled />
                </Form.Item>
              </div>

              <Form.Item label="手工调整计税依据">
                <Space align="center">
                  <Switch
                    checked={manualMode}
                    onChange={(v) => { setManualMode(v); invalidate() }}
                  />
                  <InputNumber
                    value={manualBase}
                    min={0}
                    precision={2}
                    disabled={!manualMode}
                    onChange={(v) => { setManualBase(v || 0); invalidate() }}
                    addonAfter="元"
                    style={{ width: 210 }}
                  />
                </Space>
              </Form.Item>

              <Form.Item label="确认说明">
                <Input.TextArea
                  value={note}
                  rows={2}
                  maxLength={200}
                  placeholder="如：已核对本期增值税申报表，按转出未交增值税确认"
                  onChange={(e) => { setNote(e.target.value); setCalculationConfirmed(false) }}
                />
              </Form.Item>

              <Space>
                <Button type="primary" icon={<CalculatorOutlined />} loading={loading} onClick={doCalculate}>
                  重新计算
                </Button>
                <Button icon={<ReloadOutlined />} onClick={() => load(query)} loading={loading}>
                  刷新取数
                </Button>
              </Space>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={15}>
          <Card
            size="small"
            bordered={false}
            className="calc-result-card"
            title={<span><CheckCircleOutlined /> 试算结果</span>}
            extra={preview ? <Tag color={preview.period_status === 'open' ? 'green' : 'orange'}>
              {preview.period_status === 'open' ? '期间可生成' : '期间不可生成'}
            </Tag> : null}
          >
            {!preview ? (
              <div className="calc-empty">选择参数后点击“重新计算”，系统会从应交税费明细取数并生成试算结果。</div>
            ) : (
              <>
                {preview.existing_voucher ? (
                  <Alert
                    type="success"
                    showIcon
                    message={`本期已生成附加税凭证 ${preview.existing_voucher.voucher_no}`}
                    style={{ marginBottom: 12, background: '#eef8f2', borderColor: '#cce8d8' }}
                  />
                ) : null}
                {!preview.sources.some((x) => x.available) ? (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="应交税费明细没有可用金额"
                    description="请先确认本期增值税已转出未交增值税，或切换到手工调整并填写计税依据。"
                  />
                ) : null}

                <div className="calc-source-grid">
                  <div className="calc-source-item">
                    <span>取数来源</span>
                    <strong>{preview.selected_source_label}</strong>
                  </div>
                  <div className="calc-source-item">
                    <span>科目</span>
                    <strong className="mono-num">{preview.selected_account_code || '手工调整'}</strong>
                  </div>
                  <div className="calc-source-item">
                    <span>计税依据</span>
                    <strong className="mono-num">{fmt(preview.base_amount)} 元</strong>
                  </div>
                  <div className="calc-source-item">
                    <span>减半政策</span>
                    <span>{preview.small_micro ? <Tag color="green">六税两费减半 50%</Tag> : <Tag>不减免</Tag>}</span>
                  </div>
                </div>

                <div className="calc-kpis">
                  <Card size="small" bordered={false}>
                    <Statistic title="附加税法定合计" value={preview.gross_total} precision={2} />
                  </Card>
                  <Card size="small" bordered={false}>
                    <Statistic
                      title="减半减免"
                      value={preview.reduction_total}
                      precision={2}
                      valueStyle={{ color: '#1a7f45' }}
                    />
                  </Card>
                  <Card size="small" bordered={false}>
                    <Statistic
                      title="实际应缴"
                      value={preview.net_total}
                      precision={2}
                      valueStyle={{ color: '#b42318' }}
                    />
                  </Card>
                </div>

                <Table
                  rowKey="account_code"
                  size="small"
                  pagination={false}
                  dataSource={preview.items}
                  columns={columns}
                  tableLayout="fixed"
                  summary={() => (
                    <Table.Summary.Row style={{ fontWeight: 600, background: '#f8fafc' }}>
                      <Table.Summary.Cell index={0}>合计</Table.Summary.Cell>
                      <Table.Summary.Cell index={1} />
                      <Table.Summary.Cell index={2} align="right">{fmt(preview.gross_total)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={3} align="right">{fmt(preview.reduction_total)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={4} align="right">{fmt(preview.net_total)}</Table.Summary.Cell>
                    </Table.Summary.Row>
                  )}
                />

                <Divider style={{ margin: '16px 0 12px' }} />
                <div className="calc-confirm-box">
                  <Checkbox
                    checked={baseConfirmed}
                    onChange={(e) => { setBaseConfirmed(e.target.checked); setCalculationConfirmed(false) }}
                  >
                    我已核对本期增值税申报表和计税依据，确认金额无误
                  </Checkbox>
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<CheckCircleOutlined />}
                      disabled={readOnly || !baseConfirmed || !preview.can_generate || preview.net_total <= 0}
                      loading={busy}
                      onClick={doConfirm}
                    >
                      确认测算
                    </Button>
                    <Button
                      type="primary"
                      icon={<FileTextOutlined />}
                      disabled={readOnly || !calculationConfirmed || !preview.can_generate}
                      loading={busy}
                      onClick={doGenerate}
                    >
                      生成未审核凭证
                    </Button>
                  </Space>
                </div>
                <div className="calc-footnote">
                  凭证按减半后的实际应缴净额入账；减免额仅保留在测算记录中，不单独确认减免收益。
                </div>
              </>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}