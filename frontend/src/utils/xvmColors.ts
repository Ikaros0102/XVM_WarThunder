import { BattleMode, ModeStats, PlayerStatsRecord } from '../types'

export const XVM_COLORS = {
  red: '#FE0E00',
  orange: '#FE7903',
  yellow: '#F8F400',
  green: '#60FF00',
  cyan: '#02C9B3',
  purple: '#D042F3',
  missing: '#8A96A8',
} as const

export function getWinrateColor(wr: number | null | undefined): string {
  if (wr === null || wr === undefined || Number.isNaN(wr)) return XVM_COLORS.missing
  if (wr < 47) return XVM_COLORS.red
  if (wr < 50) return XVM_COLORS.orange
  if (wr < 53) return XVM_COLORS.yellow
  if (wr < 57) return XVM_COLORS.green
  if (wr < 62) return XVM_COLORS.cyan
  return XVM_COLORS.purple
}

export function getKdColor(kd: number | null | undefined): string {
  if (kd === null || kd === undefined || Number.isNaN(kd)) return XVM_COLORS.missing
  if (kd < 0.6) return XVM_COLORS.red
  if (kd < 0.9) return XVM_COLORS.orange
  if (kd < 1.3) return XVM_COLORS.yellow
  if (kd < 1.9) return XVM_COLORS.green
  if (kd < 2.6) return XVM_COLORS.cyan
  return XVM_COLORS.purple
}

export function getBattlesColor(battles: number | null | undefined): string {
  if (battles === null || battles === undefined || Number.isNaN(battles)) return XVM_COLORS.missing
  if (battles < 1000) return XVM_COLORS.red
  if (battles < 3000) return XVM_COLORS.orange
  if (battles < 7000) return XVM_COLORS.yellow
  if (battles < 15000) return XVM_COLORS.green
  if (battles < 30000) return XVM_COLORS.cyan
  return XVM_COLORS.purple
}

export function formatBattles(
  battles: number | null | undefined,
  status?: string,
): string {
  if (battles === null || battles === undefined) {
    return status === 'ok' ? '0' : '—'
  }
  if (battles >= 1000) {
    return `${(battles / 1000).toFixed(1)}k`
  }
  return String(battles)
}

export function extractModeStats(
  stats: PlayerStatsRecord | null | undefined,
  mode: BattleMode,
): ModeStats | null {
  if (!stats || stats.status !== 'ok') return null
  return stats[mode] ?? null
}
