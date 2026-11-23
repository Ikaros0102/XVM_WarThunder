import React, { useEffect, useState } from 'react'
import { AirAlertState, AirAlertStyle } from '../types'

interface AirAlertWidgetProps {
  alert: AirAlertState | null
  enabled: boolean
  durationSec: number
  styleVariant?: AirAlertStyle
  editLayout: boolean
}

export const AirAlertWidget: React.FC<AirAlertWidgetProps> = ({
  alert,
  enabled,
  durationSec,
  styleVariant = 'rect',
  editLayout,
}) => {
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000)

  useEffect(() => {
    if (!alert) return
    setNowSec(Date.now() / 1000)
    const timer = window.setInterval(() => {
      setNowSec(Date.now() / 1000)
    }, 100)
    return () => window.clearInterval(timer)
  }, [alert?.triggered_at, alert?.expires_at])

  if (!enabled && !editLayout) {
    return null
  }

  const active = alert && nowSec < alert.expires_at ? alert : null
  if (!active && !editLayout) {
    return null
  }

  const az = active ? active.azimuth : 330
  const alt = active ? active.altitude : 1000
  const totalDur = active ? Math.max(1, active.expires_at - active.triggered_at) : durationSec
  const remaining = active ? Math.max(0, active.expires_at - nowSec) : durationSec
  const pct = Math.max(0, Math.min(100, (remaining / totalDur) * 100))

  if (styleVariant === 'circle') {
    return (
      <div
        className={`air-alert-circle ${!active && editLayout ? 'air-alert-preview' : ''}`}
        role="status"
        aria-live="polite"
      >
        <svg className="air-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path
            fill="currentColor"
            d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1l3.5 1v-1.5L13 19v-5.5l8 2.5Z"
          />
        </svg>
        <span className="air-circle-az">{az}°</span>
        <span className="air-circle-alt">{alt}м</span>
      </div>
    )
  }

  return (
    <div
      className={`air-alert-pill ${!active && editLayout ? 'air-alert-preview' : ''}`}
      role="status"
      aria-live="polite"
    >
      <div className="air-alert-row">
        <svg className="air-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path
            fill="currentColor"
            d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1l3.5 1v-1.5L13 19v-5.5l8 2.5Z"
          />
        </svg>
        <span className="air-label">ВОЗДУХ!</span>
        <span className="air-values">
          <strong>{az}°</strong> · ~{alt}м
        </span>
        <span className="air-timer">{remaining.toFixed(0)}с</span>
      </div>
      <div className="air-progress-track">
        <div className="air-progress-bar" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
