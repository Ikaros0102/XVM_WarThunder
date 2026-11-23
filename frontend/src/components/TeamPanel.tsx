import React from 'react'
import { BattleMode, PlayerState, WidgetsConfig } from '../types'
import {
  extractModeStats,
  formatBattles,
  getBattlesColor,
  getKdColor,
  getWinrateColor,
} from '../utils/xvmColors'

interface TeamPanelProps {
  title: string
  side: 'ally' | 'enemy'
  players: PlayerState[]
  battleMode: BattleMode
  widgetsConfig: WidgetsConfig
  isOverlay: boolean
}

const FpePills: React.FC<{ count: number }> = ({ count }) => {
  return (
    <span className="fpe-pills" aria-label={`Огнетушители: ${count} из 2`}>
      <span className={`fpe-dot ${count >= 1 ? 'fpe-active' : 'fpe-spent'}`} title="Огнетушитель 1" />
      <span className={`fpe-dot ${count >= 2 ? 'fpe-active' : 'fpe-spent'}`} title="Огнетушитель 2" />
      <span className="fpe-num">{count}</span>
    </span>
  )
}

export const TeamPanel: React.FC<TeamPanelProps> = ({
  title,
  side,
  players,
  battleMode,
  widgetsConfig,
  isOverlay,
}) => {
  const captionId = `team-caption-${side}`

  return (
    <section
      className={`team-panel team-${side} ${isOverlay ? 'glass-overlay' : 'glass-board'}`}
      tabIndex={0}
      role="region"
      aria-labelledby={captionId}
    >
      <header className="team-header">
        <span id={captionId} className="team-title">
          {title}
        </span>
        <span className="team-count">{players.length}</span>
      </header>

      {players.length === 0 ? (
        <div className="team-empty">Нажмите Tab в бою для сканирования состава</div>
      ) : (
        <div className="table-wrapper">
          <table className="team-table">
            <thead>
              <tr>
                <th scope="col" className="col-nick">
                  Игрок
                </th>
                {widgetsConfig.show_vehicle && (
                  <th scope="col" className="col-veh">
                    Техника
                  </th>
                )}
                {widgetsConfig.show_wr && (
                  <th scope="col" className="col-num">
                    WR
                  </th>
                )}
                {widgetsConfig.show_kd && (
                  <th scope="col" className="col-num">
                    K/D
                  </th>
                )}
                {widgetsConfig.show_battles && (
                  <th scope="col" className="col-num">
                    Бои
                  </th>
                )}
                {widgetsConfig.show_match_kd && (
                  <th scope="col" className="col-num">
                    Бой
                  </th>
                )}
                {widgetsConfig.show_fpe && (
                  <th scope="col" className="col-fpe">
                    Огн
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {players.map((p) => {
                const ms = extractModeStats(p.stats, battleMode)
                const wrText = ms?.winrate !== null && ms?.winrate !== undefined ? `${ms.winrate.toFixed(1)}%` : '—'
                const kdText = ms?.kd !== null && ms?.kd !== undefined ? ms.kd.toFixed(2) : '—'
                const battlesText = formatBattles(ms?.battles, p.stats?.status)
                const isDisconnected = widgetsConfig.show_disconnect && p.disconnected

                return (
                  <tr
                    key={p.canonical_key}
                    className={`${isDisconnected ? 'row-disconnected' : ''} ${
                      !p.is_alive && !isDisconnected ? 'row-dead' : ''
                    }`}
                  >
                    <th scope="row" className="cell-nick">
                      <span className="nick-wrap">
                        {widgetsConfig.show_nuke_threat && p.nuke_threat && (
                          <span className="badge-nuke" title="Угроза ядерного удара!">
                            ☢
                          </span>
                        )}
                        {isDisconnected && (
                          <span className="badge-dc" title="Вышел из боя">
                            ⏻
                          </span>
                        )}
                        
                        <span className="nick-text" title={p.display_nick}>
                          {p.display_nick}
                        </span>
                        <a
                          className="nick-profile-link"
                          href={`https://warthunder.com/ru/community/userinfo/?nick=${encodeURIComponent(p.display_nick)}`}
                          target="_blank"
                          rel="noreferrer"
                          title={`Открыть профиль ${p.display_nick} на warthunder.com`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          ↗
                        </a>
                      </span>
                    </th>

                    {widgetsConfig.show_vehicle && (
                      <td className="cell-veh" title={p.current_vehicle ?? undefined}>
                        {p.current_vehicle ?? '—'}
                      </td>
                    )}

                    {widgetsConfig.show_wr && (
                      <td className="cell-num" style={{ color: getWinrateColor(ms?.winrate) }}>
                        {wrText}
                      </td>
                    )}

                    {widgetsConfig.show_kd && (
                      <td className="cell-num" style={{ color: getKdColor(ms?.kd) }}>
                        {kdText}
                      </td>
                    )}

                    {widgetsConfig.show_battles && (
                      <td className="cell-num" style={{ color: getBattlesColor(ms?.battles) }}>
                        {battlesText}
                      </td>
                    )}

                    {widgetsConfig.show_match_kd && (
                      <td className="cell-num cell-match-kd">
                        {p.kills}/{p.deaths}
                      </td>
                    )}

                    {widgetsConfig.show_fpe && (
                      <td className="cell-fpe">
                        <FpePills count={p.fpe_count} />
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
