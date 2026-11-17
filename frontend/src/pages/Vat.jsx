import React, { useContext, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Form, Input, InputNumber, Row, Space, Statistic,
  Switch, Table, Tabs, Tag, App as AntApp,
} from 'antd'
import { FileTextOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

export default function Vat() {
  const { period, readOnly } = useContext(AppCtx)
  const [tab, setTab] = useState('book')
  const [book, setBook] = useState(null)
  const [decl, setDecl] = useState(null)
  const [year, setYear] = useState(null)
  const [chk, setChk] = useState(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm()
  const { message, modal } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      const [b, c] = await Promise.all([api.vatBook({ period }), api.vatCheck({ period })])
      setBook(b)
      setChk(c)
      form.setFieldsValue(b.saved)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [period])

  useEffect(() => {
    if (tab === 'decl') {
      api.vatDeclaration({ period }).then(setDecl).catch((e) => message.error(errMsg(e)))
    }
    if (tab === 'year') {
      api.vatTable({ year: Number(period.slice(0, 4)) }).then(setYear).catch((e) => message.error(errMsg(e)))
    }
  }, [tab, period])

  const save = async () => {
    const v = await form.validateFields()
    try {
      await api.saveVatBook({ period }, v)
      message.success('已保存调整项')
      load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const doSurtax = () => {
    modal.confirm({
      title: `计提 ${period} 附加税费`,
      content: `以本期应纳增值税 ${fmt(book?.payable)} 元为计税依据，生成城建税、教育费附加、地方教育附加三笔分录。`,
      onOk: async () => {
        setBusy(true)
        try {
          const r = await api.postSurtax({ period })
          message.success(`已生成凭证 ${r.voucher_no}，合计 ${fmt(r.total)}`)
          load()
        } catch (e) {
          message.error(errMsg(e))
        } finally {
          setBusy(false)
        }
      },
    })
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">增值税</div>
          <div className="page-desc">
            {period} · 销项/进项按凭证行税率分档，留抵按 2221.01 累计余额判定
          </div>
        </div>
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={load} />
          {!readOnly ? (
            <Button size="small" icon={<FileTextOutlined />} loading={busy} onClick={doSurtax}
              disabled={!book?.payable}>
              计提附加税费
            </Button>
          ) : null}
        </Space>
      </div>

      {chk?.issues?.length ? (
        <div style={{ marginBottom: 12 }}>
          {chk.issues.map((it, i) => (
            <Alert
              key={i}
              type={it.level === 'error' ? 'error' : it.level === 'warn' ? 'warning' : 'info'}
              showIcon
              style={{ marginBottom: 6 }}
              message={it.msg}
            />
          ))}
        </div>
      ) : null}

      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="销项税额" value={book?.output_tax || 0} precision={2}
            valueStyle={{ fontSize: 21, color: '#1b57b0' }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="进项税额" value={book?.input_tax || 0} precision={2}
            valueStyle={{ fontSize: 21, color: '#1a7f45' }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="本期应纳税额" value={book?.payable || 0} precision={2}
            valueStyle={{ fontSize: 21, color: '#c0392b' }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="期末留抵" value={book?.closing_credit || 0} precision={2}
            valueStyle={{ fontSize: 21, color: book?.closing_credit ? '#e08a00' : '#6b7280' }} />
        </Card>
        <Card size="small" bordered={false} style={{ flex: 1 }}>
          <Statistic title="本期已缴" value={book?.paid || 0} precision={2}
            valueStyle={{ fontSize: 21, color: '#6b7280' }} />
        </Card>
      </div>

      <Card size="small" bordered={false}>
        <Tabs
          size="small"
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: 'book',
              label: '台账',
              children: (
                <Row gutter={16}>
                  <Col span={14}>
                    <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>销项按税率分档（附列资料一取数）</div>
                    <Table
                      rowKey="rate"
                      size="small"
                      pagination={false}
                      loading={loading}
                      dataSource={book?.output_by_rate || []}
                      locale={{ emptyText: '本期无销项税额' }}
                      columns={[
                        { title: '税率', dataIndex: 'label', width: 100 },
                        { title: '销售额', dataIndex: 'sales', className: 'num', render: (v) => fmt(v) },
                        { title: '销项税额', dataIndex: 'tax', className: 'num', render: (v) => fmt(v) },
                      ]}
                      summary={(rows) => (
                        <Table.Summary.Row style={{ fontWeight: 600, background: '#fafafa' }}>
                          <Table.Summary.Cell index={0}>合计</Table.Summary.Cell>
                          <Table.Summary.Cell index={1} className="num">
                            {fmt(rows.reduce((s, r) => s + r.sales, 0))}
                          </Table.Summary.Cell>
                          <Table.Summary.Cell index={2} className="num">
                            {fmt(rows.reduce((s, r) => s + r.tax, 0))}
                          </Table.Summary.Cell>
                        </Table.Summary.Row>
                      )}
                    />
                    <div style={{ fontSize: 12, color: '#6b7280', margin: '18px 0 6px' }}>
                      进项按税率分档（附列资料二取数）
                    </div>
                    <Table
                      rowKey="rate"
                      size="small"
                      pagination={false}
                      loading={loading}
                      dataSource={book?.input_by_rate || []}
                      locale={{ emptyText: '本期无进项税额' }}
                      columns={[
                        { title: '税率', dataIndex: 'label', width: 100 },
                        { title: '进项税额', dataIndex: 'amount', className: 'num', render: (v) => fmt(v) },
                      ]}
                      summary={(rows) => (
                        <Table.Summary.Row style={{ fontWeight: 600, background: '#fafafa' }}>
                          <Table.Summary.Cell index={0}>合计</Table.Summary.Cell>
                          <Table.Summary.Cell index={1} className="num">
                            {fmt(rows.reduce((s, r) => s + r.amount, 0))}
                          </Table.Summary.Cell>
                        </Table.Summary.Row>
                      )}
                    />
                    {book?.transfer_out ? (
                      <div style={{ marginTop: 10, fontSize: 12 }}>
                        <Tag color="orange">进项税额转出 {fmt(book.transfer_out)}</Tag>
                        已从可抵扣进项中扣除
                      </div>
                    ) : null}
                  </Col>
                  <Col span={10}>
                    <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 8 }}>
                      手工调整项（账面无法自动区分的情况）
                    </div>
                    <Form form={form} layout="vertical" size="small" disabled={readOnly}>
                      <Form.Item name="adjust_output_tax" label="调整销项税额" tooltip="未开票收入、视同销售等补记的销项">
                        <InputNumber precision={2} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="adjust_input_tax" label="调整进项税额" tooltip="计算抵扣（如农产品收购发票）、补认证进项">
                        <InputNumber precision={2} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="paid_tax" label="本期实际缴纳">
                        <InputNumber precision={2} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item name="adjust_note" label="调整说明">
                        <Input.TextArea rows={2} placeholder="如：10 月未开票收入 5 万，补计销项 6,500" />
                      </Form.Item>
                      <Form.Item name="confirmed" label="已确认申报" valuePropName="checked">
                        <Switch />
                      </Form.Item>
                      {!readOnly ? (
                        <Space>
                          <Button type="primary" size="small" icon={<SaveOutlined />} onClick={save}>
                            保存
                          </Button>
                        </Space>
                      ) : null}
                    </Form>
                    <div style={{ marginTop: 16, padding: 10, background: '#f8fafc', borderRadius: 4, fontSize: 12, lineHeight: 1.9 }}>
                      <div>期初留抵：<b className="mono-num">{fmt(book?.opening_credit)}</b></div>
                      <div>可抵扣进项：<b className="mono-num">{fmt(book?.deductible)}</b></div>
                      <div>账面应交（2221.01）：<b className="mono-num">{fmt(book?.book_payable)}</b></div>
                      <div>账面留抵（2221.01）：<b className="mono-num">{fmt(book?.book_credit)}</b></div>
                      <div style={{ color: book?.diff ? '#c0392b' : '#1a7f45' }}>
                        账实差异：<b className="mono-num">{fmt(book?.diff)}</b>
                        {book?.diff ? '（检查涉税科目或转出凭证）' : '（一致）'}
                      </div>
                    </div>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'decl',
              label: '申报底稿',
              children: decl ? (
                <Row gutter={20}>
                  <Col span={12}>
                    <div className="sheet" style={{ padding: 0 }}>
                      <h1 style={{ fontSize: 15 }}>增值税及附加税费申报表（主表）</h1>
                      <div className="sub">税款所属期 {decl.period} · 单位：元</div>
                      <table className="fin" style={{ marginTop: 10 }}>
                        <thead>
                          <tr>
                            <th style={{ width: 56 }}>行次</th>
                            <th>项目</th>
                            <th style={{ width: 140 }}>金额</th>
                          </tr>
                        </thead>
                        <tbody>
                          {decl.main.map((m) => (
                            <tr key={m.no} className={['19', '24', '34'].includes(m.no) ? 'bold' : ''}>
                              <td style={{ textAlign: 'center' }}>{m.no}</td>
                              <td>{m.label}</td>
                              <td className="amt">{fmt(m.value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div style={{ marginTop: 12, fontSize: 12, color: '#6b7280' }}>
                        第 34 行大于 0 表示本期需补缴，小于 0 表示可退或结转下期。
                      </div>
                    </div>
                  </Col>
                  <Col span={12}>
                    <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>附列资料一（销项明细）</div>
                    <table className="fin">
                      <thead>
                        <tr>
                          <th>税率</th>
                          <th>销售额</th>
                          <th>销项税额</th>
                        </tr>
                      </thead>
                      <tbody>
                        {decl.sheet1.length ? (
                          decl.sheet1.map((r) => (
                            <tr key={r.rate}>
                              <td style={{ textAlign: 'center' }}>{r.label}</td>
                              <td className="amt">{fmt(r.sales)}</td>
                              <td className="amt">{fmt(r.tax)}</td>
                            </tr>
                          ))
                        ) : (
                          <tr><td colSpan={3} style={{ textAlign: 'center', color: '#9ca3af' }}>本期无销项</td></tr>
                        )}
                        <tr className="bold">
                          <td style={{ textAlign: 'center' }}>合计</td>
                          <td className="amt">{fmt(decl.summary.output_tax ? decl.sheet1.reduce((s, r) => s + r.sales, 0) : 0)}</td>
                          <td className="amt">{fmt(decl.summary.output_tax)}</td>
                        </tr>
                      </tbody>
                    </table>

                    <div style={{ fontSize: 12, color: '#6b7280', margin: '16px 0 6px' }}>附列资料二（进项明细）</div>
                    <table className="fin">
                      <thead>
                        <tr>
                          <th>税率</th>
                          <th>进项税额</th>
                        </tr>
                      </thead>
                      <tbody>
                        {decl.sheet2.detail.length ? (
                          decl.sheet2.detail.map((r) => (
                            <tr key={r.rate}>
                              <td style={{ textAlign: 'center' }}>{r.label}</td>
                              <td className="amt">{fmt(r.amount)}</td>
                            </tr>
                          ))
                        ) : (
                          <tr><td colSpan={2} style={{ textAlign: 'center', color: '#9ca3af' }}>本期无进项</td></tr>
                        )}
                        {decl.sheet2.transfer_out ? (
                          <tr>
                            <td style={{ textAlign: 'center' }}>进项税额转出</td>
                            <td className="amt">-{fmt(decl.sheet2.transfer_out)}</td>
                          </tr>
                        ) : null}
                        <tr className="bold">
                          <td style={{ textAlign: 'center' }}>合计</td>
                          <td className="amt">{fmt(decl.sheet2.total)}</td>
                        </tr>
                      </tbody>
                    </table>

                    <div style={{ fontSize: 12, color: '#6b7280', margin: '16px 0 6px' }}>附加税费（以应纳增值税为基数）</div>
                    <table className="fin">
                      <thead>
                        <tr>
                          <th>税种</th>
                          <th style={{ width: 90 }}>费率</th>
                          <th style={{ width: 120 }}>金额</th>
                        </tr>
                      </thead>
                      <tbody>
                        {decl.surtax.map((s) => (
                          <tr key={s.code}>
                            <td>{s.name}</td>
                            <td style={{ textAlign: 'center' }}>{(s.rate / 100).toFixed(0)}%</td>
                            <td className="amt">{fmt(s.amount)}</td>
                          </tr>
                        ))}
                        <tr className="bold">
                          <td colSpan={2} style={{ textAlign: 'right' }}>合计</td>
                          <td className="amt">{fmt(decl.surtax.reduce((a, b) => a + b.amount, 0))}</td>
                        </tr>
                      </tbody>
                    </table>
                  </Col>
                </Row>
              ) : null,
            },
            {
              key: 'year',
              label: `${period.slice(0, 4)} 年度`,
              children: year ? (
                <Table
                  rowKey="period"
                  size="small"
                  pagination={false}
                  dataSource={year.rows}
                  columns={[
                    { title: '期间', dataIndex: 'period', width: 100 },
                    { title: '销售额', dataIndex: 'sales', className: 'num', render: (v) => (v ? fmt(v) : '') },
                    { title: '销项税额', dataIndex: 'output', className: 'num', render: (v) => (v ? fmt(v) : '') },
                    { title: '进项税额', dataIndex: 'input', className: 'num', render: (v) => (v ? fmt(v) : '') },
                    { title: '进项转出', dataIndex: 'transfer_out', className: 'num', render: (v) => (v ? fmt(v) : '') },
                    {
                      title: '应纳税额', dataIndex: 'payable', className: 'num',
                      render: (v) => (v ? <b style={{ color: '#c0392b' }}>{fmt(v)}</b> : ''),
                    },
                    {
                      title: '期末留抵', dataIndex: 'closing_credit', className: 'num',
                      render: (v) => (v ? <span style={{ color: '#e08a00' }}>{fmt(v)}</span> : ''),
                    },
                    { title: '已缴', dataIndex: 'paid', className: 'num', render: (v) => (v ? fmt(v) : '') },
                    {
                      title: '欠缴', dataIndex: 'unpaid', className: 'num',
                      render: (v) => (v ? <span style={{ color: '#c0392b' }}>{fmt(v)}</span> : ''),
                    },
                  ]}
                  summary={() => (
                    <Table.Summary.Row style={{ fontWeight: 600, background: '#fafafa' }}>
                      <Table.Summary.Cell index={0}>全年</Table.Summary.Cell>
                      <Table.Summary.Cell index={1} className="num">{fmt(year.total.sales)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={2} className="num">{fmt(year.total.output)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={3} className="num">{fmt(year.total.input)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={4} />
                      <Table.Summary.Cell index={5} className="num">{fmt(year.total.payable)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={6} />
                      <Table.Summary.Cell index={7} className="num">{fmt(year.total.paid)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={8} />
                    </Table.Summary.Row>
                  )}
                />
              ) : null,
            },
          ]}
        />
      </Card>
    </div>
  )
}
