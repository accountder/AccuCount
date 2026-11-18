import React, { useContext, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, DatePicker, Descriptions, Form, InputNumber, Row, Select,
  Space, Statistic, Table, Tabs, Tag, App as AntApp,
} from 'antd'
import { CalculatorOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

const METHODS = [
  { value: 'straight', label: '年限平均法' },
  { value: 'ddb', label: '双倍余额递减法' },
  { value: 'syoyds', label: '年数总和法' },
]

const METHOD_LABEL = Object.fromEntries(METHODS.map((x) => [x.value, x.label]))

export default function DepreciationCalculator() {
  const { period } = useContext(AppCtx)
  const { message } = AntApp.useApp()
  const [tab, setTab] = useState('all')
  const [allPreview, setAllPreview] = useState(null)
  const [allLoading, setAllLoading] = useState(false)
  const [single, setSingle] = useState(null)
  const [singleLoading, setSingleLoading] = useState(false)
  const [form] = Form.useForm()

  const loadAll = async () => {
    setAllLoading(true)
    try {
      setAllPreview(await api.depreciation({ period }))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setAllLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
  }, [period])

  useEffect(() => {
    form.setFieldsValue({
      original_value: 100000,
      salvage_rate: 5,
      useful_months: 60,
      method: 'straight',
      in_service_date: dayjs(period + '-01'),
      accum_dep: 0,
    })
    setSingle(null)
  }, [period])

  const doCalculate = async () => {
    const v = await form.validateFields()
    setSingleLoading(true)
    try {
      const data = await api.depreciationCalculate({
        original_value: v.original_value,
        salvage_rate: Math.round(Number(v.salvage_rate) * 100),
        useful_months: v.useful_months,
        method: v.method,
        in_service_date: v.in_service_date.format('YYYY-MM-DD'),
        dep_start_period: v.dep_start_period || undefined,
        accum_dep: v.accum_dep || 0,
        current_period: period,
      })
      setSingle(data)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setSingleLoading(false)
    }
  }

  const allRows = allPreview?.rows || []
  const allColumns = [
    { title: '资产编码', dataIndex: 'code', width: 100 },
    { title: '资产名称', dataIndex: 'name', ellipsis: true },
    { title: '类别', dataIndex: 'category', width: 100 },
    { title: '原值', dataIndex: 'original_value', width: 120, align: 'right', render: fmt },
    { title: '累计折旧', dataIndex: 'accum_dep', width: 120, align: 'right', render: fmt },
    { title: '账面净值', dataIndex: 'net_value', width: 120, align: 'right', render: fmt },
    {
      title: '本期应提', dataIndex: 'amount', width: 120, align: 'right',
      render: (v) => <b className="mono-num">{fmt(v)}</b>,
    },
    {
      title: '状态 / 原因', dataIndex: 'reason', width: 150,
      render: (v, r) => r.posted ? <Tag color="green">已计提</Tag> : <span style={{ color: v ? '#8a5a00' : '#1a7f45' }}>{v || '本期应提'}</span>,
    },
  ]

  const scheduleColumns = [
    { title: '期次', dataIndex: 'seq', width: 64, align: 'right' },
    { title: '期间', dataIndex: 'period', width: 100 },
    { title: '月折旧', dataIndex: 'amount', width: 120, align: 'right', render: fmt },
    { title: '累计折旧', dataIndex: 'accum', width: 120, align: 'right', render: fmt },
    { title: '账面净值', dataIndex: 'net', width: 120, align: 'right', render: fmt },
  ]

  return (
    <div className="calc-module">
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'all',
            label: '本期全部资产',
            children: (
              <>
                <div className="calc-kpis calc-kpis-four">
                  <Card size="small" bordered={false}>
                    <Statistic title="本期应提折旧" value={allPreview?.total || 0} precision={2} valueStyle={{ color: '#b42318' }} />
                  </Card>
                  <Card size="small" bordered={false}>
                    <Statistic title="已计提折旧" value={allPreview?.posted_total || 0} precision={2} valueStyle={{ color: '#1a7f45' }} />
                  </Card>
                  <Card size="small" bordered={false}>
                    <Statistic title="待提资产数" value={allPreview?.count || 0} suffix="项" />
                  </Card>
                  <Card size="small" bordered={false}>
                    <Statistic title="已提资产数" value={allPreview?.posted_count || 0} suffix="项" />
                  </Card>
                </div>
                {allPreview?.posted_total > 0 ? (
                  <Alert
                    type="success"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message={`${period} 已计提折旧 ${fmt(allPreview.posted_total)} 元，测算表同时展示已提和待提资产。`}
                  />
                ) : null}
                <Card
                  size="small"
                  bordered={false}
                  title={`${period} 全部资产折旧测算`}
                  extra={<Button size="small" icon={<ReloadOutlined />} loading={allLoading} onClick={loadAll}>刷新</Button>}
                >
                  <Table
                    rowKey="id"
                    size="small"
                    loading={allLoading}
                    dataSource={allRows}
                    columns={allColumns}
                    pagination={false}
                    scroll={{ x: 950, y: 420 }}
                    locale={{ emptyText: '还没有固定资产卡片' }}
                  />
                </Card>
              </>
            ),
          },
          {
            key: 'single',
            label: '单资产测算',
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} xl={8}>
                  <Card size="small" bordered={false} className="calc-panel">
                    <div className="calc-panel-title"><CalculatorOutlined /><span>资产参数</span></div>
                    <Form form={form} layout="vertical" size="small">
                      <Form.Item name="original_value" label="固定资产原值" rules={[{ required: true }]}>
                        <InputNumber min={0} precision={2} addonAfter="元" style={{ width: '100%' }} />
                      </Form.Item>
                      <div className="calc-field-row">
                        <Form.Item name="salvage_rate" label="预计净残值率" rules={[{ required: true }]}>
                          <InputNumber min={0} max={100} precision={2} addonAfter="%" style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item name="useful_months" label="使用月数" rules={[{ required: true }]}>
                          <InputNumber min={1} max={1200} precision={0} style={{ width: '100%' }} />
                        </Form.Item>
                      </div>
                      <Form.Item name="method" label="折旧方法" rules={[{ required: true }]}>
                        <Select options={METHODS} />
                      </Form.Item>
                      <Form.Item name="in_service_date" label="投入使用日期" rules={[{ required: true }]}>
                        <DatePicker style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="dep_start_period" label="折旧起始期间" tooltip="留空则按投入使用次月自动计算">
                        <Select
                          allowClear
                          showSearch
                          placeholder="自动：投入使用次月"
                          options={Array.from({ length: 24 }, (_, i) => {
                            const p = dayjs(period + '-01').add(i, 'month').format('YYYY-MM')
                            return { value: p, label: p }
                          })}
                        />
                      </Form.Item>
                      <Form.Item name="accum_dep" label="当前累计折旧">
                        <InputNumber min={0} precision={2} addonAfter="元" style={{ width: '100%' }} />
                      </Form.Item>
                      <Button type="primary" icon={<CalculatorOutlined />} loading={singleLoading} onClick={doCalculate}>
                        计算折旧计划
                      </Button>
                    </Form>
                  </Card>
                </Col>
                <Col xs={24} xl={16}>
                  <Card size="small" bordered={false} title="折旧测算结果">
                    {!single ? (
                      <div className="calc-empty">填写资产参数后，系统会生成完整折旧计划和本期折旧额。</div>
                    ) : (
                      <>
                        <div className="calc-kpis calc-kpis-four">
                          <Card size="small" bordered={false}>
                            <Statistic title="应计折旧总额" value={single.summary.depreciable_total} precision={2} />
                          </Card>
                          <Card size="small" bordered={false}>
                            <Statistic title="每月折旧" value={single.summary.monthly_dep} precision={2} valueStyle={{ color: '#1b57b0' }} />
                          </Card>
                          <Card size="small" bordered={false}>
                            <Statistic title={`${period} 应提`} value={single.summary.current_period_amount} precision={2} valueStyle={{ color: '#b42318' }} />
                          </Card>
                          <Card size="small" bordered={false}>
                            <Statistic title="账面净值" value={single.summary.net_value} precision={2} valueStyle={{ color: '#1a7f45' }} />
                          </Card>
                        </div>
                        <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered style={{ marginBottom: 12 }}>
                          <Descriptions.Item label="原值">{fmt(single.summary.original_value)}</Descriptions.Item>
                          <Descriptions.Item label="净残值">{fmt(single.summary.salvage)}</Descriptions.Item>
                          <Descriptions.Item label="折旧方法">{single.summary.method_label}</Descriptions.Item>
                          <Descriptions.Item label="折旧起始期间">{single.summary.dep_start_period}</Descriptions.Item>
                          <Descriptions.Item label="使用月数">{single.summary.useful_months} 个月</Descriptions.Item>
                          <Descriptions.Item label="当前累计折旧">{fmt(single.summary.accum_dep)}</Descriptions.Item>
                        </Descriptions>
                        <Table
                          rowKey="seq"
                          size="small"
                          pagination={false}
                          dataSource={single.rows}
                          columns={scheduleColumns}
                          scroll={{ y: 360 }}
                        />
                      </>
                    )}
                  </Card>
                </Col>
              </Row>
            ),
          },
        ]}
      />
    </div>
  )
}