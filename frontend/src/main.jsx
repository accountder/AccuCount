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
        colorPrimary: '#1f4f7a',
        colorInfo: '#1f4f7a',
        colorSuccess: '#16775b',
        colorWarning: '#b7791f',
        colorError: '#b42318',
        colorText: '#18202a',
        colorTextSecondary: '#667085',
        colorBorder: '#e4e9f0',
        colorBorderSecondary: '#edf1f5',
        colorBgLayout: '#f4f6f8',
        colorBgContainer: '#ffffff',
        borderRadius: 8,
        borderRadiusLG: 12,
        controlHeight: 34,
        fontSize: 14,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Source Han Sans SC', 'Noto Sans CJK SC', sans-serif",
        boxShadow: '0 1px 2px rgba(16, 24, 40, 0.04)',
        boxShadowSecondary: '0 8px 24px rgba(16, 24, 40, 0.08)',
      },
      components: {
        Layout: {
          headerBg: '#ffffff',
          bodyBg: '#f4f6f8',
          siderBg: '#f8f9fb',
        },
        Menu: {
          itemHeight: 40,
          itemBorderRadius: 8,
          itemMarginBlock: 2,
          itemMarginInline: 8,
          itemPaddingInline: 12,
          itemSelectedBg: '#eaf2f8',
          itemSelectedColor: '#1f4f7a',
          itemHoverBg: '#f0f4f8',
          groupTitleColor: '#98a2b3',
          groupTitleFontSize: 11,
        },
        Table: {
          headerBg: '#f8fafc',
          headerColor: '#667085',
          headerSplitColor: 'transparent',
          borderColor: '#edf1f5',
          rowHoverBg: '#f8fafc',
          cellPaddingBlock: 10,
          cellPaddingInline: 14,
          cellFontSize: 13,
        },
        Card: {
          borderRadiusLG: 12,
          headerFontSize: 15,
          headerHeight: 50,
        },
        Button: {
          fontWeight: 500,
          primaryShadow: '0 1px 2px rgba(31, 79, 122, 0.18)',
        },
        Input: {
          activeShadow: '0 0 0 3px rgba(31, 79, 122, 0.08)',
        },
        Select: {
          optionSelectedBg: '#eaf2f8',
        },
        Tag: {
          borderRadiusSM: 999,
        },
      },
    }}
  >
    <AntApp>
      <App />
    </AntApp>
  </ConfigProvider>,
)
