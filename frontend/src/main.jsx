import React from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider, App as AntApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import App from './App'
import './styles.css'

dayjs.locale('zh-cn')

createRoot(document.getElementById('root')).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: '#1b57b0',
        borderRadius: 4,
        fontSize: 13,
        colorBgLayout: '#f0f2f5',
      },
      components: {
        Table: { cellPaddingBlockSM: 4, headerBg: '#fafafa' },
      },
    }}
  >
    <AntApp>
      <App />
    </AntApp>
  </ConfigProvider>,
)
