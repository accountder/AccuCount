import React from 'react'
import { Select } from 'antd'

export default function PeriodSelect({ meta, value, onChange, width = 132, size = 'small' }) {
  return (
    <Select
      value={value || undefined}
      onChange={onChange}
      size={size}
      style={{ width }}
      popupMatchSelectWidth={false}
      options={(meta?.periods || []).map((p) => ({
        value: p.code,
        label: `${p.code}${p.status === 'closed' ? '（已结）' : ''}`,
      }))}
    />
  )
}