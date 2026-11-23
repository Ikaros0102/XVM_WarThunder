export type BattleMode = 'rb' | 'ab' | 'sb';
export type LogLanguage = 'auto' | 'ru' | 'en';
export type AirAlertStyle = 'rect' | 'circle';
export type TeamSide = 'ally' | 'enemy' | 'unknown';
export type VehicleKind = 'plane' | 'helicopter' | 'recon_drone' | 'ground' | 'unknown';
export type StatsStatus = 'pending' | 'ok' | 'not_found' | 'hidden' | 'error';

export interface ModeStats {
  winrate: number | null;
  kd: number | null;
  battles: number | null;
  kpd: number | null;
}

export interface PlayerStatsRecord {
  canonical_key: string;
  nick: string;
  status: StatsStatus;
  ab: ModeStats;
  rb: ModeStats;
  sb: ModeStats;
  updated_at: string | null;
}

export interface PlayerState {
  canonical_key: string;
  display_nick: string;
  clan_tag: string | null;
  console_prefix: string | null;
  platform_hint: 'psn' | 'live' | 'console' | null;
  team: TeamSide;
  exact_nick_verified: boolean;
  current_vehicle: string | null;
  current_vehicle_kind: VehicleKind;
  is_alive: boolean;
  kills: number;
  deaths: number;
  fpe_count: number;
  disconnected: boolean;
  nuke_threat: boolean;
  stats: PlayerStatsRecord | null;
}

export interface AirAlertState {
  azimuth: number;
  altitude: number;
  sender: string;
  triggered_at: number;
  expires_at: number;
}

export interface EnemyAirSummary {
  planes: number;
  helicopters: number;
  plane_list: string[];
  helicopter_list: string[];
}

export interface ProviderStatus {
  name: string;
  state: 'idle' | 'active' | 'paused' | 'error';
  paused_until: number | null;
  last_error: string | null;
  queue_size: number;
  cache_count: number;
}

export interface OcrStatus {
  last_scan_at: number | null;
  cooldown_remaining: number;
  last_allies_count: number;
  last_enemies_count: number;
  last_error: string | null;
}

export interface SessionSnapshot {
  battle_active: boolean;
  wt_connected: boolean;
  mock_mode: boolean;
  battle_mode: BattleMode;
  allies: PlayerState[];
  enemies: PlayerState[];
  unassigned_count: number;
  air_alert: AirAlertState | null;
  enemy_air: EnemyAirSummary;
  provider_status: ProviderStatus;
  ocr_status: OcrStatus;
}

export interface WidgetLayout {
  enabled: boolean;
  x: number;
  y: number;
  scale: number;
  opacity: number;
  anchor_x?: 'left' | 'right';
}

export interface NormalizedRoi {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface WidgetsConfig {
  ally_panel: WidgetLayout;
  enemy_panel: WidgetLayout;
  air_alert: WidgetLayout;
  enemy_air_counter: WidgetLayout;
  show_fpe: boolean;
  show_nuke_threat: boolean;
  show_disconnect: boolean;
  show_vehicle: boolean;
  show_match_kd: boolean;
  show_wr: boolean;
  show_kd: boolean;
  show_battles: boolean;
}

export interface AppConfig {
  host: string;
  port: number;
  wt_api_url: string;
  wt_window_title: string;
  battle_mode: BattleMode;
  log_language: LogLanguage;
  air_alert_enabled: boolean;
  air_alert_duration_sec: number;
  air_alert_style: AirAlertStyle;
  tab_debounce_sec: number;
  tab_hold_delay_ms: number;
  stats_min_interval_sec: number;
  stats_jitter_sec: number;
  stats_cache_ttl_days: number;
  edit_layout: boolean;
  overlay_visible: boolean;
  toggle_overlay_hotkey: string;
  debug_mode: boolean;
  widgets: WidgetsConfig;
  ocr_roi: {
    left_nicks: NormalizedRoi;
    right_nicks: NormalizedRoi;
  };
}
