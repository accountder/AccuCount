import axios from 'axios'

export const TOKEN_KEY = 'accucount_token'
export const USER_KEY = 'accucount_user'

// 兼容 0.1 版本留下的登录态，避免升级后无谓地掉线。
for (const [newKey, oldKey] of [[TOKEN_KEY, 'cnerp_token'], [USER_KEY, 'cnerp_user']]) {
  if (!localStorage.getItem(newKey) && localStorage.getItem(oldKey)) {
    localStorage.setItem(newKey, localStorage.getItem(oldKey))
    localStorage.removeItem(oldKey)
  }
}

const http = axios.create({ baseURL: '/api' })

http.interceptors.request.use((cfg) => {
  const t = localStorage.getItem(TOKEN_KEY)
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

http.interceptors.response.use(
  (r) => r,
  (e) => {
    if (e.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      localStorage.removeItem('cnerp_token')
      localStorage.removeItem('cnerp_user')
      if (!location.pathname.startsWith('/login')) location.reload()
    }
    return Promise.reject(e)
  },
)

export function errMsg(e, fallback = '操作失败') {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d) && d.length) return d[0]?.msg || fallback
  return e?.message || fallback
}

export const api = {
  login: (username, password) => http.post('/login', { username, password }).then((r) => r.data),
  changePassword: (p) => http.post('/me/password', p).then((r) => r.data),
  users: () => http.get('/users').then((r) => r.data),
  createUser: (p) => http.post('/users', p).then((r) => r.data),
  updateUser: (id, p) => http.put(`/users/${id}`, p).then((r) => r.data),
  resetUserPassword: (id, p) => http.post(`/users/${id}/reset-password`, p).then((r) => r.data),
  auditLogs: (p) => http.get('/audit-logs', { params: p }).then((r) => r.data),
  downloadBackup: () => http.get('/admin/backup', { responseType: 'blob' }).then((r) => r.data),
  meta: () => http.get('/meta').then((r) => r.data),
  updateCompany: (p) => http.put('/company', p).then((r) => r.data),

  accounts: (p) => http.get('/accounts', { params: p }).then((r) => r.data),
  accountBalance: (id, period) =>
    http.get(`/accounts/${id}/balance`, { params: { period } }).then((r) => r.data),
  createAccount: (p) => http.post('/accounts', p).then((r) => r.data),
  updateAccount: (id, p) => http.put(`/accounts/${id}`, p).then((r) => r.data),
  deleteAccount: (id) => http.delete(`/accounts/${id}`).then((r) => r.data),

  vouchers: (p) => http.get('/vouchers', { params: p }).then((r) => r.data),
  voucher: (id) => http.get(`/vouchers/${id}`).then((r) => r.data),
  createVoucher: (p, q) => http.post('/vouchers', p, { params: q }).then((r) => r.data),
  updateVoucher: (id, p) => http.put(`/vouchers/${id}`, p).then((r) => r.data),
  deleteVoucher: (id) => http.delete(`/vouchers/${id}`).then((r) => r.data),
  batchVouchers: (p) => http.post('/vouchers/batch', p).then((r) => r.data),
  renumberVouchers: (p) => http.post('/vouchers/renumber', null, { params: p }).then((r) => r.data),
  copyVoucher: (id, p) => http.post(`/vouchers/${id}/copy`, p || {}).then((r) => r.data),
  voucherAction: (id, act) => http.post(`/vouchers/${id}/${act}`).then((r) => r.data),

  trialBalance: (p) => http.get('/reports/trial-balance', { params: p }).then((r) => r.data),
  ledger: (p) => http.get('/reports/ledger', { params: p }).then((r) => r.data),
  balanceSheet: (p) => http.get('/reports/balance-sheet', { params: p }).then((r) => r.data),
  profit: (p) => http.get('/reports/profit', { params: p }).then((r) => r.data),
  check: (p) => http.get('/reports/check', { params: p }).then((r) => r.data),
  stats: (p) => http.get('/stats', { params: p }).then((r) => r.data),

  periods: () => http.get('/periods').then((r) => r.data),
  carryover: (code) => http.post(`/periods/${code}/carryover`).then((r) => r.data),
  closePeriod: (code) => http.post(`/periods/${code}/close`).then((r) => r.data),
  reopenPeriod: (code) => http.post(`/periods/${code}/reopen`).then((r) => r.data),

  // ---- 往来
  partners: (p) => http.get('/partners', { params: p }).then((r) => r.data),
  partnerOptions: () => http.get('/partners/options').then((r) => r.data),
  createPartner: (p) => http.post('/partners', p).then((r) => r.data),
  updatePartner: (id, p) => http.put(`/partners/${id}`, p).then((r) => r.data),
  deletePartner: (id) => http.delete(`/partners/${id}`).then((r) => r.data),
  partnerLedger: (p) => http.get('/partners/ledger', { params: p }).then((r) => r.data),
  partnerAging: (p) => http.get('/partners/aging', { params: p }).then((r) => r.data),
  runSettle: (p) => http.post('/partners/settle', null, { params: p }).then((r) => r.data),
  clearSettle: (p) => http.delete('/partners/settle', { params: p }).then((r) => r.data),
  statement: (p) => http.get('/partners/statement', { params: p }).then((r) => r.data),
  agingBands: () => http.get('/aging-bands').then((r) => r.data),
  updateAgingBands: (p) => http.put('/aging-bands', p).then((r) => r.data),

  // ---- 固定资产
  assets: (p) => http.get('/assets', { params: p }).then((r) => r.data),
  assetMeta: () => http.get('/assets/meta').then((r) => r.data),
  createAsset: (p) => http.post('/assets', p).then((r) => r.data),
  updateAsset: (id, p) => http.put(`/assets/${id}`, p).then((r) => r.data),
  deleteAsset: (id) => http.delete(`/assets/${id}`).then((r) => r.data),
  assetSummary: (p) => http.get('/assets/summary', { params: p }).then((r) => r.data),
  depreciation: (p) => http.get('/assets/depreciation', { params: p }).then((r) => r.data),
  postDepreciation: (p) => http.post('/assets/depreciation', null, { params: p }).then((r) => r.data),
  unpostDepreciation: (p) => http.delete('/assets/depreciation', { params: p }).then((r) => r.data),
  assetSchedule: (id) => http.get(`/assets/${id}/schedule`).then((r) => r.data),
  capitalizeAsset: (id, p) => http.post(`/assets/${id}/capitalize`, p).then((r) => r.data),
  disposeAsset: (id, p) => http.post(`/assets/${id}/dispose`, p).then((r) => r.data),

  // ---- 增值税
  vatBook: (p) => http.get('/vat/book', { params: p }).then((r) => r.data),
  saveVatBook: (p, body) => http.put('/vat/book', body, { params: p }).then((r) => r.data),
  vatTable: (p) => http.get('/vat/table', { params: p }).then((r) => r.data),
  vatDeclaration: (p) => http.get('/vat/declaration', { params: p }).then((r) => r.data),
  vatCheck: (p) => http.get('/vat/check', { params: p }).then((r) => r.data),
  postSurtax: (p) => http.post('/vat/surtax', null, { params: p }).then((r) => r.data),

  // ---- 导出（返回二进制，由页面触发下载）
  exportBalanceSheet: (p) =>
    http.get('/reports/balance-sheet/export', { params: p, responseType: 'blob' }).then((r) => r.data),
  exportProfit: (p) =>
    http.get('/reports/profit/export', { params: p, responseType: 'blob' }).then((r) => r.data),
  exportTrialBalance: (p) =>
    http.get('/reports/trial-balance/export', { params: p, responseType: 'blob' }).then((r) => r.data),
}

/** 触发浏览器下载一个 Blob，文件名由调用方给（后端用 ASCII 名，这里用中文名） */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const fmt = (v) =>
  (Number(v) || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const fmt0 = (v) => (Number(v) || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })

export default http
