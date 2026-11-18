import React, { useContext, useEffect, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Checkbox, Col, Divider, Form, Input, InputNumber,
  Row, Space, Statistic, Switch, Tag,
} from 'antd'
import { CalculatorOutlined, CheckCircleOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

export default function IncomeTaxCalculator({ period: periodProp, onChanged }) {
  const { period: contextPeriod, readOnly } = useContext(AppCtx)
  const period = periodProp || contextPeriod
  const { message, modal } = AntApp.useApp()
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [priorLoss, setPriorLoss] = useState(0)
  const [employeeCount, setEmployeeCount] = useState(0)
  const [totalAssets, setTotalAssets] = useState(null)
  const [industryOk, setIndustryOk] = useState(true)
  const [note, setNote] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [checked, setChecked] = useState(false)
  const [form] = Form.useForm()

  const query = {
    prior_loss: Number(priorLoss) || 0,
    employee_count: Number(employeeCount) || 0,
    total_assets: totalAssets == null ? undefined : Number(totalAssets),
    industry_ok: industryOk,
  }

  const load = async (next = query) => {
    setLoading(true)
    try {
      const data = await api.incomeTaxPreview(period, next)
      setPreview(data)
      if (totalAssets == null && data.total_assets != null) setTotalAssets(data.total_assets)
      setConfirmed(Boolean(data.saved_confirmed && !data.existing_voucher))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setConfirmed(false)
    setChecked(false)
    setNote('')
    load({ prior_loss: 0, employee_count: 0, industry_ok: true })
  }, [period])

  const invalidate = () => { setConfirmed(false); setChecked(false) }
  const recalculate = () => load(query)

  const confirm = async () => {
    if (!preview || !checked) return
    setBusy(true)
    try {
      const data = await api.confirmIncomeTax(period, {
        period,
        prior_loss: Number(priorLoss) || 0,
        employee_count: Number(employeeCount) || 0,
        total_assets: totalAssets == null ? null : Number(totalAssets),
        industry_ok: industryOk,
        note,
      })
      setPreview(data)
      setConfirmed(true)
      message.success('企业所得税测算已确认')
      onChanged?.()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const generate = () => {
    modal.confirm({
      title: `${period} 企业所得税凭证`,
      content: (
        <div style={{ lineHeight: 1.9 }}>
          <div>累计应纳税所得额：<b>{fmt(preview?.taxable_income)}</b> 元</div>
          <div>实际税负：<b>{((preview?.effective_rate || 0) / 100).toFixed(2)}%</b></div>
          <div>本季度应计提：<b>{fmt(preview?.current_accrual)}</b> 元</div>
          <div style={{ color: '#6b7280', fontSize: 12 }}>凭证按未审核草稿生成，审核后才进入账簿。</div>
        </div>
      ),
      okText: preview?.current_accrual < 0 ? '生成冲回凭证' : '生成凭证',
      onOk: async () => {
        setBusy(true)
        try {
          const r = await api.createIncomeTaxVoucher(period)
          message.success(`已生成凭证 ${r.voucher_no}，金额 ${fmt(r.amount)} 元`)
          setConfirmed(false)
          setChecked(false)
          await load(query)
          onChanged?.()
        } catch (e) {
          message.error(errMsg(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  const conditionRows = [
    { key: 'income', label: '年度应纳税所得额≤300万元', ok: preview?.conditions?.income_ok },
    { key: 'employees', label: '从业人数≤300人', ok: preview?.conditions?.employees_ok },
    { key: 'assets', label: '资产总额≤5,000万元', ok: preview?.conditions?.assets_ok },
    { key: 'industry', label: '非限制和禁止行业', ok: preview?.conditions?.industry_ok },
  ]

  return (
    <div>
      {!preview ? (
        <div className="calc-empty">正在读取累计利润和所得税数据…</div>
      ) : !preview.applicable ? (
        <Alert type="info" showIcon message="当前月份不是季度末" description="企业所得税只在 3、6、9、12 月计提。" />
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} xl={9}>
            <Card size="small" bordered={false} className="calc-panel">
              <div className="calc-panel-title"><CalculatorOutlined /><span>所得税预提参数</span></div>
              <Form form={form} layout="vertical" size="small">
                <Form.Item label="可弥补以前年度亏损">
                  <InputNumber value={priorLoss} min={0} precision={2} addonAfter="元" style={{ width: '100%' }}
                    onChange={(v) => { setPriorLoss(v || 0); invalidate() }} />
                </Form.Item>
                <Form.Item label="季度平均从业人数" tooltip="按全年季度平均值填写">
                  <InputNumber value={employeeCount} min={0} precision={0} addonAfter="人" style={{ width: '100%' }}
                    onChange={(v) => { setEmployeeCount(v || 0); invalidate() }} />
                </Form.Item>
                <Form.Item label="季度平均资产总额" tooltip="按全年季度平均值填写；系统默认带出期末资产作为参考">
                  <InputNumber value={totalAssets} min={0} precision={2} addonAfter="元" style={{ width: '100%' }}
                    onChange={(v) => { setTotalAssets(v); invalidate() }} />
                </Form.Item>
                <Form.Item label="非限制和禁止行业">
                  <Switch checked={industryOk} onChange={(v) => { setIndustryOk(v); invalidate() }} />
                </Form.Item>
                <Form.Item label="确认说明">
                  <Input.TextArea value={note} rows={2} maxLength={200}
                    placeholder="如：按截至本季度累计会计利润预提"
                    onChange={(e) => { setNote(e.target.value); setConfirmed(false) }} />
                </Form.Item>
                <Space>
                  <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={recalculate}>重新计算</Button>
                </Space>
              </Form>
            </Card>
          </Col>
          <Col xs={24} xl={15}>
            <Card size="small" bordered={false} className="calc-result-card"
              title={<span><CheckCircleOutlined /> 季度所得税试算</span>}
              extra={preview.small_micro ? <Tag color="green">小型微利企业 · 5%</Tag> : <Tag color="orange">标准税率 · 25%</Tag>}>
              {preview.existing_voucher ? (
                <Alert type="success" showIcon style={{ marginBottom: 12, background: '#eef8f2', borderColor: '#cce8d8' }}
                  message={`本季度已生成企业所得税凭证 ${preview.existing_voucher.no}`} />
              ) : null}
              <div className="income-tax-formula">
                <div><span>年初至本季度末累计利润总额</span><b>{fmt(preview.ytd_profit)}</b></div>
                <div><span>减：可弥补以前年度亏损</span><b>{fmt(preview.prior_loss)}</b></div>
                <div className="total"><span>累计应纳税所得额</span><b>{fmt(preview.taxable_income)}</b></div>
              </div>
              <div className="calc-kpis calc-kpis-four">
                <Card size="small" bordered={false}><Statistic title="实际税负" value={(preview.effective_rate || 0) / 100} precision={2} suffix="%" /></Card>
                <Card size="small" bordered={false}><Statistic title="累计应纳所得税" value={preview.cumulative_tax} precision={2} /></Card>
                <Card size="small" bordered={false}><Statistic title="以前季度已计提" value={preview.previous_accrued} precision={2} /></Card>
                <Card size="small" bordered={false}><Statistic title="本季度应计提" value={preview.current_accrual} precision={2} valueStyle={{ color: preview.current_accrual < 0 ? '#1a7f45' : '#b42318' }} /></Card>
              </div>
              <div className="income-tax-conditions">
                {conditionRows.map((x) => <Tag key={x.key} color={x.ok ? 'green' : 'red'}>{x.label}</Tag>)}
              </div>
              {Math.abs(preview.previous_diff || 0) > 0.001 ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 10 }}
                  message="以前季度所得税测算与账面所得税费用不一致"
                  description={`测算快照 ${fmt(preview.previous_snapshot)} 元，账面 5801 所得税费用 ${fmt(preview.previous_book_expense)} 元。本季计算已优先采用账面已确认金额。`}
                />
              ) : null}
              <div className="calc-footnote">
                季度模块只按累计会计利润预提，不做纳税调增、调减；年度汇算清缴时再统一调整。
              </div>
              <Divider style={{ margin: '16px 0 12px' }} />
              <div className="calc-confirm-box">
                <Checkbox checked={checked} onChange={(e) => { setChecked(e.target.checked); setConfirmed(false) }}>
                  我已核对累计利润、从业人数、资产总额和企业所得税资格
                </Checkbox>
                <Space wrap>
                  <Button type="primary" icon={<CheckCircleOutlined />}
                    disabled={readOnly || !checked || employeeCount <= 0 || !totalAssets || preview.current_accrual === 0}
                    loading={busy} onClick={confirm}>确认测算</Button>
                  <Button type="primary" icon={<FileTextOutlined />}
                    disabled={readOnly || !confirmed || !preview.can_generate}
                    loading={busy} onClick={generate}>
                    {preview.current_accrual < 0 ? '生成冲回凭证' : '生成未审核凭证'}
                  </Button>
                </Space>
              </div>
            </Card>
          </Col>
        </Row>
      )}
    </div>
  )
}