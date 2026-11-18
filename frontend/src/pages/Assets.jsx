import React, { useContext, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, DatePicker, Descriptions, Drawer, Form, Input, InputNumber, Modal,
  Progress, Select, Space, Statistic, Table, Tag, App as AntApp,
} from 'antd'
import {
  DeleteOutlined, DollarOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'
import PeriodSelect from '../components/PeriodSelect'

const STATUS = {
  in_use: { color: 'green', text: '在用' },
  idle: { color: 'default', text: '停用' },
  disposed: { color: 'red', text: '已处置' },
}

const METHOD = { straight: '年限平均法', ddb: '双倍余额递减法', syoyds: '年数总和法' }

export default function Assets() {
  const { meta, readOnly } = useContext(AppCtx)
  const [period, setPeriod] = useState(meta.open_period || meta.periods[0]?.code)
  const [rows, setRows] = useState([])
  const [summary, setSummary] = useState(null)
  const [preview, setPreview] = useState(null)
  const [am, setAm] = useState(null)
  const [accs, setAccs] = useState([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [sched, setSched] = useState(null)
  const [disp, setDisp] = useState(null)
  const [cap, setCap] = useState(null)
  const [form] = Form.useForm()
  const [dispForm] = Form.useForm()
  const [capForm] = Form.useForm()
  const { message, modal } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      const [a, s, p, m, acc] = await Promise.all([
        api.assets(),
        api.assetSummary({ period }),
        api.depreciation({ period }),
        api.assetMeta(),
        api.accounts({ only_enabled: true }),
      ])
      setRows(a)
      setSummary(s)
      setPreview(p)
      setAm(m)
      setAccs(acc.filter((x) => x.is_leaf))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [period])

  const openNew = () => {
    setEditing(null)
    form.resetFields()
    const c = am?.categories?.[3]
    form.setFieldsValue({
      category: '电子设备',
      salvage_rate: 500,
      useful_months: 60,
      method: 'straight',
      in_service_date: dayjs(period + '-01'),
      status: 'in_use',
      original_value: 0,
      asset_account_id: c?.asset_account_id,
      dep_account_id: c?.dep_account_id,
      expense_account_id: c?.expense_account_id,
    })
    setOpen(true)
  }

  const openEdit = (r) => {
    setEditing(r)
    form.setFieldsValue({ ...r, in_service_date: dayjs(r.in_service_date) })
    setOpen(true)
  }

  const onCategory = (v) => {
    const c = am?.categories?.find((x) => x.category === v)
    if (c) {
      form.setFieldsValue({
        asset_account_id: c.asset_account_id,
        dep_account_id: c.dep_account_id,
        expense_account_id: c.expense_account_id,
      })
    }
  }

  const submit = async () => {
    const v = await form.validateFields()
    const payload = { ...v, in_service_date: v.in_service_date.format('YYYY-MM-DD') }
    try {
      if (editing) await api.updateAsset(editing.id, payload)
      else await api.createAsset(payload)
      message.success('已保存')
      setOpen(false)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doPost = () => {
    modal.confirm({
      title: `计提 ${period} 折旧`,
      content: (
        <div>
          <div>
            本期应提 <b>{fmt(preview?.total)}</b> 元，涉及 {preview?.count} 项资产。
          </div>
          <div style={{ fontSize: 12, color: '#6b7280', marginTop: 8 }}>
            将生成一张折旧凭证（借 折旧费用 / 贷 累计折旧），并回写资产累计折旧。
          </div>
        </div>
      ),
      okText: '生成凭证',
      onOk: async () => {
        setBusy(true)
        try {
          const r = await api.postDepreciation({ period })
          message.success(`已生成凭证 ${r.voucher_no}，金额 ${fmt(r.total)}`)
          load()
        } catch (e) {
          message.error(errMsg(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  const doUnpost = () => {
    modal.confirm({
      title: '撤销本期折旧',
      content: '将删除本期折旧凭证并回滚累计折旧，已记账的凭证需要先反记账。',
      okType: 'danger',
      onOk: async () => {
        try {
          const r = await api.unpostDepreciation({ period })
          message.success(`已撤销 ${r.removed} 条折旧记录`)
          load()
        } catch (e) {
          message.error(errMsg(e))
        }
      },
    })
  }

  const openSchedule = async (r) => {
    try {
      setSched(await api.assetSchedule(r.id))
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doCapitalize = async () => {
    const v = await capForm.validateFields()
    try {
      const r = await api.capitalizeAsset(cap.id, { ...v, date: v.date.format('YYYY-MM-DD') })
      message.success(`已生成入账凭证 ${r.voucher_no}`)
      setCap(null)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doDispose = async () => {
    const v = await dispForm.validateFields()
    try {
      const r = await api.disposeAsset(disp.id, { ...v, period })
      message.success(`已处置，凭证 ${r.voucher_no}，处置损益 ${fmt(r.gain)}`)
      setDisp(null)
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const assetAccs = useMemo(
    () => accs.filter((a) => a.code.startsWith('1601') || a.code.startsWith('1602')).map((a) => ({ value: a.id, label: `${a.code} ${a.name}` })),
    [accs],
  )
  const expenseAccs = useMemo(
    () => (am?.expense_accounts || []).map((a) => ({ value: a.id, label: a.label })),
    [am],
  )
  const allAccs = useMemo(() => accs.map((a) => ({ value: a.id, label: `${a.code} ${a.name}` })), [accs])

  const columns = [
    { title: '编码', dataIndex: 'code', width: 92, render: (v) => <span className="mono-num" style={{ fontSize: 12 }}>{v}</span> },
    {
      title: '资产名称', dataIndex: 'name', ellipsis: true,
      render: (v, r) => (
        <div>
          <a onClick={() => openSchedule(r)}>{v}</a>
          <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 1 }}>
            {r.category}
            {r.dept ? ` · ${r.dept}` : ''} · {r.dep_start_period} 起提 · 月提 {fmt(r.monthly_dep)}
          </div>
        </div>
      ),
    },
    { title: '原值', dataIndex: 'original_value', width: 108, className: 'num', render: (v) => fmt(v) },
    {
      title: '已提折旧', dataIndex: 'accum_dep', width: 108, className: 'num',
      render: (v) => fmt(v),
    },
    {
      title: '净值', dataIndex: 'net_value', width: 108, className: 'num',
      render: (v) => <b>{fmt(v)}</b>,
    },
    {
      title: '折旧进度', dataIndex: 'progress', width: 96,
      render: (v) => <Progress percent={Math.min(v, 100)} size="small" strokeColor="#1b57b0" />,
    },
    {
      title: '状态', dataIndex: 'status', width: 72, align: 'center',
      render: (v) => <Tag color={STATUS[v]?.color}>{STATUS[v]?.text || v}</Tag>,
    },
    {
      title: '', width: 132, align: 'right', fixed: 'right',
      render: (_, r) =>
        r.status === 'disposed' ? (
          <span style={{ color: '#9ca3af', fontSize: 12 }}>{r.disposed_period} 处置</span>
        ) : (
          <Space size={6}>
            <a onClick={() => openEdit(r)}>编辑</a>
            <a onClick={() => { setCap(r); capForm.setFieldsValue({ date: dayjs(r.in_service_date), credit_account_id: undefined, tax_rate: 0 }) }}>入账</a>
            <a style={{ color: '#c0392b' }} onClick={() => { setDisp(r); dispForm.setFieldsValue({ income: 0, tax_rate: 1300 }) }}>
              处置
            </a>
          </Space>
        ),
    },
  ]

  const visibleColumns = readOnly ? columns.slice(0, -1) : columns
  const t = summary?.total || {}
  const posted = !!preview?.rows.some((r) => r.posted)

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">固定资产</div>
          <div className="page-desc">
            当月增加次月起提 · 直线法/双倍余额递减/年数总和 · 本期末 {period}
          </div>
        </div>
        <Space>
          <PeriodSelect meta={meta} value={period} onChange={setPeriod} />
          <Button size="small" icon={<ReloadOutlined />} onClick={load} />
          {!readOnly && preview?.count > 0 && !posted ? (
            <Button size="small" type="primary" icon={<DollarOutlined />} loading={busy} onClick={doPost}>
              计提本期折旧 {fmt(preview.total)}
            </Button>
          ) : null}
          {!readOnly && posted ? (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={doUnpost}>
              撤销本期折旧
            </Button>
          ) : null}
          {!readOnly ? (
            <Button size="small" icon={<PlusOutlined />} onClick={openNew}>
              新增资产
            </Button>
          ) : null}
        </Space>
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="固定资产原值" value={t.original || 0} precision={2} valueStyle={{ fontSize: 22 }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="累计折旧" value={t.accum || 0} precision={2} valueStyle={{ fontSize: 22, color: '#e08a00' }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="账面净值" value={t.net || 0} precision={2} valueStyle={{ fontSize: 22, color: '#1b57b0' }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic
            title={posted ? `${period} 已提折旧` : `${period} 应提折旧`}
            value={posted ? preview?.posted_total || 0 : preview?.total || 0}
            precision={2}
            valueStyle={{ fontSize: 22, color: '#1a7f45' }}
          />
        </Card>
      </div>

      {posted ? (
        <Alert type="success" showIcon style={{ marginBottom: 12 }}
          message={`${period} 折旧已计提，凭证金额 ${fmt(preview.posted_total)} 元`} />
      ) : null}

      <Card size="small" bordered={false}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={visibleColumns}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 950, y: 'calc(100vh - 400px)' }}
          locale={{ emptyText: '还没有固定资产，点右上角「新增资产」建卡' }}
        />
      </Card>

      {/* 建卡/编辑 */}
      <Drawer
        title={editing ? `编辑 · ${editing.name}` : '新增固定资产'}
        open={open}
        onClose={() => setOpen(false)}
        width={560}
        extra={<Button type="primary" size="small" onClick={submit}>保存</Button>}
      >
        <Form form={form} layout="vertical" size="small">
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="code" label="资产编码" style={{ width: '40%' }}>
              <Input placeholder="留空自动生成" />
            </Form.Item>
            <Form.Item name="name" label="资产名称" rules={[{ required: true, message: '必填' }]} style={{ width: '60%' }}>
              <Input />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="category" label="类别" style={{ width: '50%' }}>
              <Select
                onChange={onCategory}
                options={(am?.categories || []).map((c) => ({ value: c.category, label: c.category }))}
              />
            </Form.Item>
            <Form.Item name="dept" label="使用部门" style={{ width: '50%' }}>
              <Input placeholder="如：生产部" />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="original_value" label="原值（元）" rules={[{ required: true }]} style={{ width: '50%' }}>
              <InputNumber min={0} precision={2} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="in_service_date" label="投入使用日期" rules={[{ required: true }]} style={{ width: '50%' }}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="useful_months" label="使用月数" style={{ width: '34%' }}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="salvage_rate" label="残值率" style={{ width: '33%' }}>
              <InputNumber min={0} max={10000} step={100} addonAfter="‱" style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="method" label="折旧方法" style={{ width: '33%' }}>
              <Select options={(am?.methods || []).map((m) => ({ value: m.value, label: m.label }))} />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="dep_start_period" label="折旧起始期间" tooltip="留空按投入使用次月">
            <Input placeholder="YYYY-MM" />
          </Form.Item>
          <Form.Item name="asset_account_id" label="固定资产科目">
            <Select showSearch optionFilterProp="label" options={assetAccs} />
          </Form.Item>
          <Form.Item name="dep_account_id" label="累计折旧科目">
            <Select showSearch optionFilterProp="label" options={assetAccs} />
          </Form.Item>
          <Form.Item name="expense_account_id" label="折旧费用科目">
            <Select showSearch optionFilterProp="label" options={expenseAccs} />
          </Form.Item>
          <Form.Item name="location" label="存放地点">
            <Input />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Drawer>

      {/* 折旧计划 */}
      <Drawer
        title={sched ? `折旧计划 · ${sched.asset.name}` : ''}
        open={!!sched}
        onClose={() => setSched(null)}
        width={620}
      >
        {sched ? (
          <>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
              <Descriptions.Item label="原值">{fmt(sched.asset.original_value)}</Descriptions.Item>
              <Descriptions.Item label="净残值">{fmt(sched.asset.salvage)}</Descriptions.Item>
              <Descriptions.Item label="应计折旧总额">{fmt(sched.asset.depreciable_total)}</Descriptions.Item>
              <Descriptions.Item label="已提">{fmt(sched.asset.accum_dep)}</Descriptions.Item>
              <Descriptions.Item label="月折旧">{fmt(sched.asset.monthly_dep)}</Descriptions.Item>
              <Descriptions.Item label="方法">{METHOD[sched.asset.method]}</Descriptions.Item>
            </Descriptions>
            <Table
              rowKey="seq"
              size="small"
              pagination={false}
              dataSource={sched.rows}
              scroll={{ y: 'calc(100vh - 420px)' }}
              columns={[
                { title: '期次', dataIndex: 'seq', width: 64, align: 'right' },
                { title: '期间', dataIndex: 'period', width: 96 },
                { title: '月折旧', dataIndex: 'amount', width: 110, className: 'num', render: (v) => fmt(v) },
                { title: '累计折旧', dataIndex: 'accum', width: 120, className: 'num', render: (v) => fmt(v) },
                { title: '净值', dataIndex: 'net', className: 'num', render: (v) => fmt(v) },
              ]}
            />
          </>
        ) : null}
      </Drawer>

      {/* 入账凭证 */}
      <Modal
        title={cap ? `固定资产入账 · ${cap.name}` : ''}
        open={!!cap}
        onCancel={() => setCap(null)}
        onOk={doCapitalize}
        okText="生成凭证"
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={`将生成：借 ${cap?.asset_account} ${fmt(cap?.original_value)} / 贷 指定科目`} />
        <Form form={capForm} layout="vertical" size="small">
          <Form.Item name="credit_account_id" label="贷方科目" rules={[{ required: true, message: '请选择' }]}>
            <Select showSearch optionFilterProp="label" options={allAccs} placeholder="银行存款 / 应付账款" />
          </Form.Item>
          <Form.Item name="date" label="入账日期" rules={[{ required: true }]}>
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="tax_rate" label="进项税率（设备专票可抵扣）">
            <Select
              options={[
                { value: 0, label: '不抵扣（价税合计入原值）' },
                { value: 1300, label: '13%' },
                { value: 900, label: '9%' },
                { value: 300, label: '3%' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 处置 */}
      <Modal
        title={disp ? `处置 · ${disp.name}` : ''}
        open={!!disp}
        onCancel={() => setDisp(null)}
        onOk={doDispose}
        okText="生成处置凭证"
        okButtonProps={{ danger: true }}
      >
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="处置凭证会自动计算清理损益，转入营业外收入/支出" />
        <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
          <Descriptions.Item label="原值">{fmt(disp?.original_value)}</Descriptions.Item>
          <Descriptions.Item label="累计折旧">{fmt(disp?.accum_dep)}</Descriptions.Item>
          <Descriptions.Item label="账面净值">{fmt(disp?.net_value)}</Descriptions.Item>
        </Descriptions>
        <Form form={dispForm} layout="vertical" size="small">
          <Form.Item name="income" label="处置价款（含税）">
            <InputNumber min={0} precision={2} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="tax_rate" label="销项税率">
            <Select
              options={[
                { value: 1300, label: '13%' },
                { value: 300, label: '3%（减按 2% 请在备注说明）' },
                { value: 0, label: '不涉税' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
