import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AirAlertWidget } from './components/AirAlertWidget'
import { EnemyAirWidget } from './components/EnemyAirWidget'
import { SettingsDrawer } from './components/SettingsDrawer'
import { TeamPanel } from './components/TeamPanel'
import { WidgetWrapper } from './components/WidgetWrapper'
import { AppConfig, BattleMode, SessionSnapshot, WidgetLayout } from './types'

const DEFAULT_SNAPSHOT: SessionSnapshot = {
  battle_active: false,
  wt_connected: false,
  mock_mode: false,
  battle_mode: 'rb',
  allies: [],
  enemies: [],
  unassigned_count: 0,
  air_alert: null,
  enemy_air: { planes: 0, helicopters: 0, plane_list: [], helicopter_list: [] },
  provider_status: {
    name: 'ThunderSkill',
    state: 'idle',
    paused_until: null,
    last_error: null,
    queue_size: 0,
    cache_count: 0,
  },
  ocr_status: {
    last_scan_at: null,
    cooldown_remaining: 0,
    last_allies_count: 0,
    last_enemies_count: 0,
    last_error: null,
  },
}

const DEFAULT_CONFIG: AppConfig = {
  host: '127.0.0.1',
  port: 8123,
  wt_api_url: 'http://localhost:8111',
  wt_window_title: 'War Thunder',
  battle_mode: 'rb',
  log_language: 'auto',
  air_alert_enabled: true,
  air_alert_duration_sec: 10,
  air_alert_style: 'rect',
  tab_debounce_sec: 30,
  tab_hold_delay_ms: 220,
  stats_min_interval_sec: 1.5,
  stats_jitter_sec: 0.4,
  stats_cache_ttl_days: 7,
  edit_layout: false,
  overlay_visible: true,
  toggle_overlay_hotkey: 'F8',
  debug_mode: false,
  widgets: {
    ally_panel: { enabled: true, x: 16, y: 140, scale: 1, opacity: 1, anchor_x: 'left' },
    enemy_panel: { enabled: true, x: 16, y: 140, scale: 1, opacity: 1, anchor_x: 'right' },
    air_alert: { enabled: true, x: 835, y: 640, scale: 1, opacity: 1, anchor_x: 'left' },
    enemy_air_counter: { enabled: true, x: 845, y: 24, scale: 1, opacity: 1, anchor_x: 'left' },
    show_fpe: true,
    show_nuke_threat: true,
    show_disconnect: true,
    show_vehicle: true,
    show_match_kd: true,
    show_wr: true,
    show_kd: true,
    show_battles: true,
  },
  ocr_roi: {
    left_nicks: { x: 0.342, y: 0.312, w: 0.146, h: 0.47 },
    right_nicks: { x: 0.512, y: 0.312, w: 0.158, h: 0.47 },
  },
}

const PANEL_COLUMN_TOGGLES = [
  { key: 'show_vehicle', label: 'Техника' },
  { key: 'show_wr', label: 'WR' },
  { key: 'show_kd', label: 'K/D' },
  { key: 'show_battles', label: 'Бои' },
  { key: 'show_match_kd', label: 'Бой' },
  { key: 'show_fpe', label: 'Огн' },
  { key: 'show_nuke_threat', label: '☢' },
  { key: 'show_disconnect', label: '⏻' },
] as const

export const App: React.FC = () => {
  const isOverlay = useMemo(
    () => new URLSearchParams(window.location.search).get('mode') === 'overlay',
    [],
  )

  const [snapshot, setSnapshot] = useState<SessionSnapshot>(DEFAULT_SNAPSHOT)
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG)
  const [wsConnected, setWsConnected] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const reconnectTimer = useRef<number | null>(null)

  useEffect(() => {
    document.documentElement.dataset.mode = isOverlay ? 'overlay' : 'board'
  }, [isOverlay])

  useEffect(() => {
    let active = true
    const fetchInitial = async () => {
      try {
        const [stResp, cfgResp] = await Promise.all([fetch('/api/state'), fetch('/api/config')])
        if (!active) return
        if (stResp.ok) setSnapshot(await stResp.json())
        if (cfgResp.ok) setConfig(await cfgResp.json())
      } catch {
        // Backend may still be starting
      }
    }
    void fetchInitial()

    const connectWs = () => {
      if (!active) return
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`)

      ws.onopen = () => {
        if (active) setWsConnected(true)
      }
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'init') {
            if (msg.state) setSnapshot(msg.state)
            if (msg.config) setConfig(msg.config)
          } else if (msg.type === 'state' && msg.state) {
            setSnapshot(msg.state)
          } else if (msg.type === 'config' && msg.config) {
            setConfig(msg.config)
          }
        } catch {
          // Ignore malformed WS message
        }
      }
      ws.onclose = () => {
        if (!active) return
        setWsConnected(false)
        reconnectTimer.current = window.setTimeout(connectWs, 1500)
      }
      ws.onerror = () => {
        ws.close()
      }
    }

    connectWs()
    return () => {
      active = false
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current)
      }
    }
  }, [])

  const patchConfig = useCallback(async (patch: Record<string, unknown>) => {
    const resp = await fetch('/api/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (resp.ok) {
      setConfig(await resp.json())
    }
  }, [])

  const handleWidgetLayoutChange = useCallback(
    (widgetId: string, patch: Partial<WidgetLayout>) => {
      setConfig((prev) => {
        const current = prev.widgets[widgetId as keyof typeof prev.widgets] as WidgetLayout
        return {
          ...prev,
          widgets: {
            ...prev.widgets,
            [widgetId]: { ...current, ...patch },
          },
        }
      })
      void patchConfig({ widgets: { [widgetId]: patch } })
    },
    [patchConfig],
  )

  const handleModeSwitch = async (mode: BattleMode) => {
    const resp = await fetch(`/api/mode/${mode}`, { method: 'POST' })
    if (resp.ok) {
      setConfig((prev) => ({ ...prev, battle_mode: mode }))
    }
  }

  const handleResetLayout = async () => {
    const resp = await fetch('/api/config/reset-layout', { method: 'POST' })
    if (resp.ok) setConfig(await resp.json())
  }

  const handleResetOcrRoi = async () => {
    const resp = await fetch('/api/config/reset-ocr-roi', { method: 'POST' })
    if (resp.ok) setConfig(await resp.json())
  }

  const handleResumeStats = async () => {
    await fetch('/api/stats/resume', { method: 'POST' })
  }

  const handleClearCache = async () => {
    await fetch('/api/stats/cache', { method: 'DELETE' })
  }

  const handleStartMock = async (lang: 'ru' | 'en') => {
    await fetch(`/api/mock/start?lang=${lang}&speed=5`, { method: 'POST' })
  }

  const handleStopMock = async () => {
    await fetch('/api/mock/stop', { method: 'POST' })
  }

  const handleQuitApp = async () => {
    await fetch('/api/app/quit', { method: 'POST' })
  }

  const editLayout = isOverlay ? config.edit_layout : false

  useEffect(() => {
    if (isOverlay && !editLayout) {
      setSettingsOpen(false)
    }
  }, [isOverlay, editLayout])

  // In Overlay mode when no match is active and not editing, stay clean & invisible
  const showBattleWidgets = !isOverlay || snapshot.battle_active || editLayout
  const overlayHidden = isOverlay && !config.overlay_visible

  if (overlayHidden) {
    return null
  }

  return (
    <div className={`app-root ${isOverlay ? 'mode-overlay' : 'mode-board'}`}>
      {!isOverlay && (
        <header className="board-topbar">
          <div className="topbar-brand">
            <span className="brand-badge">WT-XVM</span>
            <span className={`status-pill ${snapshot.wt_connected ? 'pill-ok' : 'pill-wait'}`}>
              {snapshot.mock_mode
                ? 'Симуляция боя'
                : snapshot.wt_connected
                  ? snapshot.battle_active
                    ? 'В бою (8111)'
                    : 'В ангаре (8111)'
                  : 'Ожидание игры (8111)'}
            </span>
            <span className={`status-pill ${wsConnected ? 'pill-ok' : 'pill-warn'}`}>
              {wsConnected ? 'Live' : 'Переподключение...'}
            </span>
            <span
              className={`status-pill ${
                snapshot.provider_status.state === 'paused' ? 'pill-warn' : 'pill-neutral'
              }`}
              title={snapshot.provider_status.last_error ?? undefined}
            >
              TS: {snapshot.provider_status.state} ({snapshot.provider_status.cache_count})
            </span>
            {config.debug_mode && (
              <span
                className="status-pill pill-warn"
                title="Включена запись диагностики в logs/debug_sessions"
              >
                DEBUG ON
              </span>
            )}
          </div>

          <div className="topbar-controls">
            <div className="mode-switcher" role="group" aria-label="Режим боёв">
              {(['rb', 'ab', 'sb'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`mode-btn ${config.battle_mode === m ? 'mode-btn-active' : ''}`}
                  onClick={() => void handleModeSwitch(m)}
                >
                  {m.toUpperCase()}
                </button>
              ))}
            </div>

            {!snapshot.battle_active && !snapshot.mock_mode && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void handleStartMock('ru')}
              >
                Демо бой
              </button>
            )}
            {snapshot.mock_mode && (
              <button type="button" className="btn-danger" onClick={() => void handleStopMock()}>
                Стоп демо
              </button>
            )}

            <button
              type="button"
              className={config.edit_layout ? 'btn-accent' : 'btn-secondary'}
              onClick={() => void patchConfig({ edit_layout: !config.edit_layout })}
            >
              {config.edit_layout ? 'Лейаут: Вкл (F9)' : 'Лейаут (F9)'}
            </button>

            <button type="button" className="btn-primary" onClick={() => setSettingsOpen(true)}>
              Настройки
            </button>

            <button
              type="button"
              className="btn-danger"
              title="Закрыть WT-XVM"
              onClick={() => void handleQuitApp()}
            >
              Выход
            </button>
          </div>
        </header>
      )}

      {isOverlay && editLayout && (
        <div className="overlay-edit-banner">
          <div className="overlay-edit-header">
            <span>Режим редактирования лейаута (F9) — перетаскивайте виджеты</span>
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                void patchConfig({
                  air_alert_style: config.air_alert_style === 'circle' ? 'rect' : 'circle',
                })
              }
            >
              Воздух: {config.air_alert_style === 'circle' ? 'Круг' : 'Полоса'}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setSettingsOpen(true)}>
              Настройки
            </button>
            <button
              type="button"
              className="btn-accent"
              onClick={() => void patchConfig({ edit_layout: false })}
            >
              Готово (Click-Through)
            </button>
            <button
              type="button"
              className="btn-danger"
              title="Полностью закрыть WT-XVM"
              onClick={() => void handleQuitApp()}
            >
              Выход
            </button>
          </div>
          <div className="overlay-column-toggles" role="group" aria-label="Видимые колонки ушей">
            <span className="overlay-cols-label">Колонки:</span>
            {PANEL_COLUMN_TOGGLES.map((col) => {
              const enabled = Boolean(config.widgets[col.key])
              return (
                <button
                  key={col.key}
                  type="button"
                  aria-pressed={enabled}
                  className={`col-chip ${enabled ? 'col-chip-active' : ''}`}
                  onClick={() => void patchConfig({ widgets: { [col.key]: !enabled } })}
                >
                  {col.label}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {showBattleWidgets && (
        <main className={isOverlay ? 'overlay-canvas' : 'board-grid'}>
          <div className={isOverlay ? undefined : 'board-top-widgets'}>
            <WidgetWrapper
              id="enemy_air_counter"
              title="Авиация врага"
              layout={config.widgets.enemy_air_counter}
              isOverlay={isOverlay}
              editLayout={editLayout}
              onChangeLayout={handleWidgetLayoutChange}
            >
              <EnemyAirWidget summary={snapshot.enemy_air} isOverlay={isOverlay} />
            </WidgetWrapper>

            <WidgetWrapper
              id="air_alert"
              title="Воздух!"
              layout={config.widgets.air_alert}
              isOverlay={isOverlay}
              editLayout={editLayout}
              onChangeLayout={handleWidgetLayoutChange}
            >
              <AirAlertWidget
                alert={snapshot.air_alert}
                enabled={config.air_alert_enabled}
                durationSec={config.air_alert_duration_sec}
                styleVariant={config.air_alert_style}
                editLayout={editLayout}
              />
            </WidgetWrapper>
          </div>

          <div className={isOverlay ? undefined : 'board-teams-grid'}>
            <WidgetWrapper
              id="ally_panel"
              title="Уши союзников"
              layout={config.widgets.ally_panel}
              isOverlay={isOverlay}
              editLayout={editLayout}
              onChangeLayout={handleWidgetLayoutChange}
            >
              <TeamPanel
                title="СОЮЗНИКИ"
                side="ally"
                players={snapshot.allies}
                battleMode={config.battle_mode}
                widgetsConfig={config.widgets}
                isOverlay={isOverlay}
              />
            </WidgetWrapper>

            <WidgetWrapper
              id="enemy_panel"
              title="Уши противников"
              layout={config.widgets.enemy_panel}
              isOverlay={isOverlay}
              editLayout={editLayout}
              onChangeLayout={handleWidgetLayoutChange}
            >
              <TeamPanel
                title="ПРОТИВНИКИ"
                side="enemy"
                players={snapshot.enemies}
                battleMode={config.battle_mode}
                widgetsConfig={config.widgets}
                isOverlay={isOverlay}
              />
            </WidgetWrapper>
          </div>
        </main>
      )}

      {!isOverlay && snapshot.unassigned_count > 0 && (
        <footer className="board-footer">
          Игроки из лога до нажатия Tab (команда неизвестна): {snapshot.unassigned_count}. Нажмите Tab в игре для привязки команд.
        </footer>
      )}

      <SettingsDrawer
        open={settingsOpen}
        config={config}
        providerStatus={snapshot.provider_status}
        mockMode={snapshot.mock_mode}
        onClose={() => setSettingsOpen(false)}
        onPatchConfig={patchConfig}
        onResetLayout={handleResetLayout}
        onResetOcrRoi={handleResetOcrRoi}
        onResumeStats={handleResumeStats}
        onClearCache={handleClearCache}
        onStartMock={handleStartMock}
        onStopMock={handleStopMock}
        onQuitApp={handleQuitApp}
      />
    </div>
  )
}
export default App
