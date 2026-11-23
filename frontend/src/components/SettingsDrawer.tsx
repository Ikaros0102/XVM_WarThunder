import React, { useEffect, useRef, useState } from 'react'
import {
  AirAlertStyle,
  AppConfig,
  BattleMode,
  LogLanguage,
  NormalizedRoi,
  ProviderStatus,
} from '../types'

interface SettingsDrawerProps {
  open: boolean
  config: AppConfig
  providerStatus: ProviderStatus
  mockMode: boolean
  onClose: () => void
  onPatchConfig: (patch: Record<string, unknown>) => Promise<void>
  onResetLayout: () => Promise<void>
  onResetOcrRoi: () => Promise<void>
  onResumeStats: () => Promise<void>
  onClearCache: () => Promise<void>
  onStartMock: (lang: 'ru' | 'en') => Promise<void>
  onStopMock: () => Promise<void>
  onQuitApp?: () => Promise<void>
}

const HOTKEY_PRESETS = [
  'F7',
  'F8',
  'F10',
  'F11',
  'F12',
  'INSERT',
  'HOME',
  'END',
  'PAGEUP',
  'PAGEDOWN',
  'PAUSE',
  'BACKQUOTE',
] as const

const OPACITY_WIDGETS = [
  { key: 'ally_panel', label: 'Прозрачность: Уши союзников' },
  { key: 'enemy_panel', label: 'Прозрачность: Уши противников' },
  { key: 'air_alert', label: 'Прозрачность: Виджет «Воздух!»' },
  { key: 'enemy_air_counter', label: 'Прозрачность: Счётчик авиации врага' },
] as const

function normalizeCapturedHotkey(e: KeyboardEvent): string | null {
  const code = e.code || ''
  if (/^F([1-9]|1[0-2])$/i.test(code)) {
    const f = code.toUpperCase()
    return f === 'F9' ? null : f
  }
  if (/^Key([A-Z])$/i.test(code)) {
    return code.slice(3).toUpperCase()
  }
  if (/^Digit([0-9])$/i.test(code)) {
    return code.slice(5)
  }
  if (/^Numpad([0-9]|Multiply|Add|Subtract|Decimal|Divide)$/i.test(code)) {
    return code.toUpperCase()
  }
  const upperCode = code.toUpperCase()
  if (
    [
      'INSERT',
      'DELETE',
      'HOME',
      'END',
      'PAGEUP',
      'PAGEDOWN',
      'PAUSE',
      'SCROLLLOCK',
      'BACKQUOTE',
      'MINUS',
      'EQUAL',
      'BACKSLASH',
      'BRACKETLEFT',
      'BRACKETRIGHT',
    ].includes(upperCode)
  ) {
    return upperCode
  }
  return null
}

export const SettingsDrawer: React.FC<SettingsDrawerProps> = ({
  open,
  config,
  providerStatus,
  mockMode,
  onClose,
  onPatchConfig,
  onResetLayout,
  onResetOcrRoi,
  onResumeStats,
  onClearCache,
  onStartMock,
  onStopMock,
  onQuitApp,
}) => {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [capturingHotkey, setCapturingHotkey] = useState(false)

  useEffect(() => {
    if (!open) setCapturingHotkey(false)
  }, [open])

  useEffect(() => {
    if (!capturingHotkey) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setCapturingHotkey(false)
        return
      }
      if (['Shift', 'Control', 'Alt', 'Meta', 'Tab'].includes(e.key) || e.code === 'F9') {
        e.preventDefault()
        e.stopPropagation()
        return
      }
      const normalized = normalizeCapturedHotkey(e)
      if (normalized) {
        e.preventDefault()
        e.stopPropagation()
        setCapturingHotkey(false)
        void onPatchConfig({ toggle_overlay_hotkey: normalized })
      }
    }
    window.addEventListener('keydown', onKeyDown, { capture: true })
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true })
  }, [capturingHotkey, onPatchConfig])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) {
      dialog.showModal()
    } else if (!open && dialog.open) {
      dialog.close()
    }
  }, [open])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return

    const handleClose = () => {
      if (open) onClose()
    }
    dialog.addEventListener('close', handleClose)

    // Mandatory fallback for browsers without HTMLDialogElement.closedBy
    let cleanupFallback: (() => void) | undefined
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      let pressedOutside = false
      const outside = (e: PointerEvent) => {
        const r = dialog.getBoundingClientRect()
        return (
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom
        )
      }
      const onDown = (e: PointerEvent) => {
        pressedOutside = outside(e)
      }
      const onUp = (e: PointerEvent) => {
        if (pressedOutside && outside(e)) dialog.close()
      }
      dialog.addEventListener('pointerdown', onDown)
      dialog.addEventListener('pointerup', onUp)
      cleanupFallback = () => {
        dialog.removeEventListener('pointerdown', onDown)
        dialog.removeEventListener('pointerup', onUp)
      }
    }

    return () => {
      dialog.removeEventListener('close', handleClose)
      if (cleanupFallback) cleanupFallback()
    }
  }, [open, onClose])

  const updateRoi = (side: 'left_nicks' | 'right_nicks', key: keyof NormalizedRoi, val: number) => {
    void onPatchConfig({
      ocr_roi: {
        [side]: {
          ...config.ocr_roi[side],
          [key]: Number(val.toFixed(3)),
        },
      },
    })
  }

  const loadPreview = async () => {
    try {
      const resp = await fetch(`/api/ocr/preview?t=${Date.now()}`)
      if (resp.status === 200) {
        const blob = await resp.blob()
        setPreviewUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev)
          return URL.createObjectURL(blob)
        })
      } else {
        setPreviewUrl(null)
      }
    } catch {
      setPreviewUrl(null)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      {...({ closedby: 'any' } as Record<string, string>)}
      aria-labelledby="settings-title"
      className="settings-dialog"
    >
      <div className="settings-shell">
        <header className="settings-header">
          <h2 id="settings-title">Настройки WT-XVM</h2>
          <button type="button" className="btn-secondary" onClick={onClose} autoFocus>
            Закрыть
          </button>
        </header>

        <div className="settings-body">
          <section className="settings-group">
            <h3>Режим статистики и локаль логов</h3>
            <div className="settings-row">
              <label htmlFor="battle-mode-select">Режим боёв</label>
              <select
                id="battle-mode-select"
                value={config.battle_mode}
                onChange={(e) => void onPatchConfig({ battle_mode: e.target.value as BattleMode })}
              >
                <option value="rb">Realistic Battles (RB)</option>
                <option value="ab">Arcade Battles (AB)</option>
                <option value="sb">Simulation Battles (SB)</option>
              </select>
            </div>

            <div className="settings-row">
              <label htmlFor="log-lang-select">Язык логов игры</label>
              <select
                id="log-lang-select"
                value={config.log_language}
                onChange={(e) => void onPatchConfig({ log_language: e.target.value as LogLanguage })}
              >
                <option value="auto">Авто (RU + EN)</option>
                <option value="ru">Русский (RU)</option>
                <option value="en">English (EN)</option>
              </select>
            </div>
          </section>

          <section className="settings-group">
            <h3>Виджеты и оверлей</h3>
            <div className="settings-actions">
              <button
                type="button"
                className={config.edit_layout ? 'btn-accent' : 'btn-secondary'}
                onClick={() => void onPatchConfig({ edit_layout: !config.edit_layout })}
              >
                {config.edit_layout ? 'Завершить редактирование (F9)' : 'Редактировать лейаут (F9)'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => void onResetLayout()}>
                Сбросить позиции
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void onPatchConfig({ overlay_visible: !config.overlay_visible })}
              >
                {config.overlay_visible ? 'Скрыть оверлей' : 'Показать оверлей'}
              </button>
            </div>

            <div className="checkbox-grid">
              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.ally_panel.enabled}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { ally_panel: { enabled: e.target.checked } } })
                  }
                />
                <span>Уши союзников</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.enemy_panel.enabled}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { enemy_panel: { enabled: e.target.checked } } })
                  }
                />
                <span>Уши противников</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.enemy_air_counter.enabled}
                  onChange={(e) =>
                    void onPatchConfig({
                      widgets: { enemy_air_counter: { enabled: e.target.checked } },
                    })
                  }
                />
                <span>Счётчик авиации врага</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.air_alert_enabled}
                  onChange={(e) =>
                    void onPatchConfig({
                      air_alert_enabled: e.target.checked,
                      widgets: { air_alert: { enabled: e.target.checked } },
                    })
                  }
                />
                <span>Виджет «Воздух!»</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_vehicle}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { show_vehicle: e.target.checked } })
                  }
                />
                <span>Текущая техника</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_wr}
                  onChange={(e) => void onPatchConfig({ widgets: { show_wr: e.target.checked } })}
                />
                <span>WR (% побед)</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_kd}
                  onChange={(e) => void onPatchConfig({ widgets: { show_kd: e.target.checked } })}
                />
                <span>Общий K/D</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_battles}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { show_battles: e.target.checked } })
                  }
                />
                <span>Количество боёв</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_match_kd}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { show_match_kd: e.target.checked } })
                  }
                />
                <span>K/D в текущем бою</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_fpe}
                  onChange={(e) => void onPatchConfig({ widgets: { show_fpe: e.target.checked } })}
                />
                <span>Огнетушители (FPE)</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_nuke_threat}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { show_nuke_threat: e.target.checked } })
                  }
                />
                <span>Угроза ядерного удара</span>
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={config.widgets.show_disconnect}
                  onChange={(e) =>
                    void onPatchConfig({ widgets: { show_disconnect: e.target.checked } })
                  }
                />
                <span>Статус выхода из боя</span>
              </label>
            </div>

            <div className="settings-row">
              <label htmlFor="air-duration">
                Таймер «Воздух!»: <strong>{config.air_alert_duration_sec} сек</strong>
              </label>
              <input
                id="air-duration"
                type="range"
                min={1}
                max={15}
                step={1}
                value={config.air_alert_duration_sec}
                onChange={(e) =>
                  void onPatchConfig({ air_alert_duration_sec: Number(e.target.value) })
                }
              />
            </div>

            <div className="settings-row">
              <label htmlFor="air-style-select">Вид виджета «Воздух!»</label>
              <select
                id="air-style-select"
                value={config.air_alert_style}
                onChange={(e) =>
                  void onPatchConfig({ air_alert_style: e.target.value as AirAlertStyle })
                }
              >
                <option value="rect">Прямоугольный (полоса)</option>
                <option value="circle">Круглый (компактный)</option>
              </select>
            </div>

            <div className="settings-row">
              <label htmlFor="toggle-hotkey-select">Хоткей скрытия/показа оверлея</label>
              <div className="hotkey-controls">
                <button
                  type="button"
                  className={capturingHotkey ? 'btn-accent' : 'btn-secondary'}
                  onClick={() => setCapturingHotkey((prev) => !prev)}
                  title="Нажмите кнопку и затем любую клавишу (кроме F9 и Tab); Esc — отмена"
                >
                  {capturingHotkey
                    ? 'Нажмите клавишу...'
                    : `Назначить (${config.toggle_overlay_hotkey || 'F8'})`}
                </button>
                <select
                  id="toggle-hotkey-select"
                  value={config.toggle_overlay_hotkey || 'F8'}
                  onChange={(e) => void onPatchConfig({ toggle_overlay_hotkey: e.target.value })}
                >
                  {!HOTKEY_PRESETS.includes(
                    (config.toggle_overlay_hotkey || 'F8') as (typeof HOTKEY_PRESETS)[number],
                  ) && (
                    <option value={config.toggle_overlay_hotkey}>
                      {config.toggle_overlay_hotkey}
                    </option>
                  )}
                  {HOTKEY_PRESETS.map((hk) => (
                    <option key={hk} value={hk}>
                      {hk}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {OPACITY_WIDGETS.map((item) => {
              const opacityVal = config.widgets[item.key]?.opacity ?? 1
              return (
                <div key={item.key} className="settings-row">
                  <label htmlFor={`opacity-${item.key}`}>
                    {item.label}: <strong>{Math.round(opacityVal * 100)}%</strong>
                  </label>
                  <input
                    id={`opacity-${item.key}`}
                    type="range"
                    min={0.1}
                    max={1.0}
                    step={0.05}
                    value={opacityVal}
                    onChange={(e) =>
                      void onPatchConfig({
                        widgets: { [item.key]: { opacity: Number(e.target.value) } },
                      })
                    }
                  />
                </div>
              )
            })}
          </section>

          <section className="settings-group">
            <h3>Статистика (ThunderSkill + SQLite 7 дней)</h3>
            <p className="settings-meta">
              Статус: <strong>{providerStatus.state}</strong> · Очередь:{' '}
              <strong>{providerStatus.queue_size}</strong> · В кэше:{' '}
              <strong>{providerStatus.cache_count}</strong>
              {providerStatus.last_error ? ` · Ошибка: ${providerStatus.last_error}` : ''}
            </p>
            <div className="settings-actions">
              <button type="button" className="btn-secondary" onClick={() => void onResumeStats()}>
                Возобновить парсер
              </button>
              <button type="button" className="btn-danger" onClick={() => void onClearCache()}>
                Очистить SQLite кэш
              </button>
            </div>
          </section>

          <section className="settings-group">
            <h3>Калибровка OCR таблицы Tab (16:9 координаты)</h3>
            <div className="roi-grid">
              <div className="roi-box">
                <h4>Левая колонка (Союзники)</h4>
                {(['x', 'y', 'w', 'h'] as const).map((field) => (
                  <label key={`l-${field}`} className="roi-slider">
                    <span>
                      {field.toUpperCase()}: {config.ocr_roi.left_nicks[field].toFixed(3)}
                    </span>
                    <input
                      type="range"
                      min={0.05}
                      max={0.85}
                      step={0.005}
                      value={config.ocr_roi.left_nicks[field]}
                      onChange={(e) => updateRoi('left_nicks', field, Number(e.target.value))}
                    />
                  </label>
                ))}
              </div>
              <div className="roi-box">
                <h4>Правая колонка (Противники)</h4>
                {(['x', 'y', 'w', 'h'] as const).map((field) => (
                  <label key={`r-${field}`} className="roi-slider">
                    <span>
                      {field.toUpperCase()}: {config.ocr_roi.right_nicks[field].toFixed(3)}
                    </span>
                    <input
                      type="range"
                      min={0.05}
                      max={0.85}
                      step={0.005}
                      value={config.ocr_roi.right_nicks[field]}
                      onChange={(e) => updateRoi('right_nicks', field, Number(e.target.value))}
                    />
                  </label>
                ))}
              </div>
            </div>
            <div className="settings-actions">
              <button type="button" className="btn-secondary" onClick={() => void loadPreview()}>
                Показать последний скриншот Tab
              </button>
              <button type="button" className="btn-secondary" onClick={() => void onResetOcrRoi()}>
                Сбросить ROI OCR
              </button>
            </div>
            {previewUrl && (
              <img className="ocr-preview-img" src={previewUrl} alt="Превью областей OCR" />
            )}
          </section>

          <section className="settings-group">
            <h3>Симулятор боя (проверка без игры)</h3>
            <div className="settings-actions">
              <button type="button" className="btn-secondary" onClick={() => void onStartMock('ru')}>
                Запустить RU лог (5x)
              </button>
              <button type="button" className="btn-secondary" onClick={() => void onStartMock('en')}>
                Запустить EN лог (5x)
              </button>
              {mockMode && (
                <button type="button" className="btn-danger" onClick={() => void onStopMock()}>
                  Остановить симуляцию
                </button>
              )}
            </div>
          </section>

          <section className="settings-group">
            <h3>Отладка и диагностика</h3>
            <div className="checkbox-grid">
              <label>
                <input
                  type="checkbox"
                  checked={config.debug_mode}
                  onChange={(e) => void onPatchConfig({ debug_mode: e.target.checked })}
                />
                <span>Режим отладки (запись Tab OCR, лога боя и статистики)</span>
              </label>
            </div>
            <div className="settings-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void fetch('/api/debug/open-folder', { method: 'POST' })}
              >
                Открыть папку отладки
              </button>
            </div>
          </section>
        </div>

        {onQuitApp && (
          <footer className="settings-footer">
            <button type="button" className="btn-danger" onClick={() => void onQuitApp()}>
              Закрыть программу (Выход)
            </button>
          </footer>
        )}
      </div>
    </dialog>
  )
}
