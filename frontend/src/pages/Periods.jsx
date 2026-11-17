import React, { useContext, useEffect, useState } from 'react'
import { App as AntApp, Button, Card, Empty, Popconfirm, Spin, Tag } from 'antd'
import {
  CalendarOutlined,
  CheckCircleOutlined,
  LockOutlined,
  ReloadOutlined,
  UnlockOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errMsg, fmt } from '../api'
import { AppCtx } from '../context'

export default function Periods() {
  const { period, setPeriod, reloadMeta, user } = useContext(AppCtx)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const { message } = AntApp.useApp()

  const load = async () => {
    setLoading(true)
    try {
      setRows(await api.periods())
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const run = async (fn, ok, ...args) => {
    try {
      const r = await fn(...args)
      message.success(typeof ok === 'function' ? ok(r) : ok)
      load()
      reloadMeta()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="page-title">期末处理</div>
          <div className="page-desc">审核后的凭证自动进入账簿；一键结账会先结转损益，再锁定期间。</div>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
      </div>

      <div className="period-note">
        <LockOutlined />
        <div>
          <strong>结账会自动完成损益结转</strong>
          <span>点击“一键结账”后，系统生成并审核结转凭证，随后锁定本期。结账后如需修改，先反结账。</span>
        </div>
      </div>

      {loading && !rows.length ? (
        <div className="period-loading"><Spin /></div>
      ) : rows.length ? (
        <div className="period-grid">
          {rows.map((r) => {
            const booked = r.booked ?? r.posted ?? 0
            const active = r.code === period
            return (
              <Card
                key={r.code}
                className={`period-card ${active ? 'is-active' : ''} ${r.status === 'closed' ? 'is-closed' : ''}`}
                bordered={false}
              >
                <div className="period-card-head">
                  <div className="period-code">
                    <CalendarOutlined />
                    <span>{r.code}</span>
                  </div>
                  <Tag color={r.status === 'closed' ? 'default' : 'green'}>
                    {r.status === 'closed' ? '已结账' : '开放中'}
                  </Tag>
                </div>

                <div className="period-stats">
                  <div>
                    <span>已入账凭证</span>
                    <strong>{booked}</strong>
                  </div>
                  <div>
                    <span>待审核凭证</span>
                    <strong className={r.pending ? 'has-pending' : ''}>{r.pending}</strong>
                  </div>
                </div>

                <div className="period-status-line">
                  {r.status === 'closed' ? (
                    <span><LockOutlined /> {r.closed_at ? dayjs(r.closed_at).format('YYYY-MM-DD HH:mm') : '已锁定'}</span>
                  ) : r.pending ? (
                    <span className="warn"><CheckCircleOutlined /> 审核全部凭证后才能结账</span>
                  ) : (
                    <span className="ok"><CheckCircleOutlined /> 已具备结账条件</span>
                  )}
                </div>

                <div className="period-card-actions">
                  <Button
                    onClick={() => setPeriod(r.code)}
                    disabled={active}
                    icon={active ? <CheckCircleOutlined /> : <CalendarOutlined />}
                  >
                    {active ? '当前期间' : '切换到此期'}
                  </Button>

                  {user?.role === 'admin' && r.status === 'open' ? (
                    <Popconfirm
                      title={`一键结账 ${r.code}？`}
                      description="系统将自动结转损益并锁定本期，此操作会产生结转凭证。"
                      okText="确认结账"
                      onConfirm={() => run(
                        api.closePeriod,
                        (res) => res.carryover_no
                          ? `已结账，自动生成结转凭证 ${res.carryover_no}${res.profit != null ? `，本期利润 ${fmt(res.profit)} 元` : ''}`
                          : '已结账，本期无需结转损益',
                        r.code,
                      )}
                    >
                      <Button type="primary" icon={<LockOutlined />}>一键结账</Button>
                    </Popconfirm>
                  ) : null}

                  {user?.role === 'admin' && r.status === 'closed' ? (
                    <Popconfirm
                      title={`反结账 ${r.code}？`}
                      description="反结账后本期恢复可编辑，原结转凭证会保留。"
                      onConfirm={() => run(api.reopenPeriod, '已反结账', r.code)}
                    >
                      <Button icon={<UnlockOutlined />}>反结账</Button>
                    </Popconfirm>
                  ) : null}
                </div>
              </Card>
            )
          })}
        </div>
      ) : (
        <Empty description="暂无会计期间" />
      )}
    </div>
  )
}
