import React from 'react'
import { EnemyAirSummary } from '../types'

interface EnemyAirWidgetProps {
  summary: EnemyAirSummary
  isOverlay: boolean
}

export const EnemyAirWidget: React.FC<EnemyAirWidgetProps> = ({ summary, isOverlay }) => {
  const hasAir = summary.planes > 0 || summary.helicopters > 0

  return (
    <div
      className={`enemy-air-pill ${hasAir ? 'enemy-air-active' : 'enemy-air-clear'} ${
        isOverlay ? 'glass-overlay' : 'glass-board'
      }`}
      aria-label={`Вражеская авиация: самолетов ${summary.planes}, вертолетов ${summary.helicopters}`}
    >
      <div className="enemy-air-counters">
        <span className={`air-counter-item ${summary.planes > 0 ? 'threat-hot' : ''}`} title={summary.plane_list.join(', ') || 'Нет самолётов'}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path
              fill="currentColor"
              d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1l3.5 1v-1.5L13 19v-5.5l8 2.5Z"
            />
          </svg>
          <span>Самолёты:</span>
          <strong>{summary.planes}</strong>
        </span>

        <span className="air-divider" aria-hidden="true" />

        <span className={`air-counter-item ${summary.helicopters > 0 ? 'threat-hot' : ''}`} title={summary.helicopter_list.join(', ') || 'Нет вертолётов'}>
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <path
              fill="currentColor"
              d="M3 5h18v2h-8v2.1a5 5 0 0 1 4 4.9v2h2l2-2h1v4h-2l-2 2H6a3 3 0 0 1-3-3v-3a5 5 0 0 1 4-4.9V7H3V5Zm6 6a3 3 0 0 0-3 3v1h8v-1a3 3 0 0 0-3-3H9Z"
            />
          </svg>
          <span>Вертолёты:</span>
          <strong>{summary.helicopters}</strong>
        </span>
      </div>

      {!isOverlay && (summary.plane_list.length > 0 || summary.helicopter_list.length > 0) && (
        <div className="enemy-air-models">
          {[...summary.plane_list, ...summary.helicopter_list].join(' · ')}
        </div>
      )}
    </div>
  )
}
