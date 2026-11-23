"""Integration tests for FastAPI REST endpoints, WebSocket hub, config persistence, and Mock Replay."""

from fastapi.testclient import TestClient

from wt_xvm.api.server import RuntimeContext, create_app
from wt_xvm.api.ws_hub import WebSocketHub
from wt_xvm.config import AppConfig
from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.log_parser import LogParser
from wt_xvm.core.mock_replay import MockReplayController
from wt_xvm.core.poller import WarThunderPoller
from wt_xvm.core.vehicle_db import VehicleDB
from wt_xvm.ocr.tab_watcher import TabOcrWatcher
from wt_xvm.stats.cache import StatsCache
from wt_xvm.stats.providers import ProviderResult, ThunderSkillProvider
from wt_xvm.stats.service import StatsService


class _NoopProvider(ThunderSkillProvider):
    async def fetch(self, canonical_key: str, query_nick: str) -> ProviderResult:
        return ProviderResult(outcome="not_found")


def _build_test_app(tmp_path):
    cfg = AppConfig()
    vdb = VehicleDB()
    parser = LogParser()
    state = BattleSessionState(cfg, vdb)
    holder = {}
    hub = WebSocketHub(lambda: holder["ctx"].snapshot_dict())
    stats = StatsService(cfg, state, cache=StatsCache(tmp_path / "test.db"), provider=_NoopProvider())
    watcher = TabOcrWatcher(cfg, state)
    poller = WarThunderPoller(cfg, state, parser)
    mock = MockReplayController(state, parser, on_change=hub.schedule_state_broadcast)
    ctx = RuntimeContext(
        config=cfg,
        state=state,
        parser=parser,
        stats_service=stats,
        tab_watcher=watcher,
        poller=poller,
        mock_replay=mock,
        ws_hub=hub,
        config_path=tmp_path / "config.json",
    )
    holder["ctx"] = ctx
    return create_app(ctx, start_background_tasks=False)


def test_rest_and_websocket_lifecycle(tmp_path):
    app = _build_test_app(tmp_path)
    with TestClient(app) as client:
        # Health & defaults
        h = client.get("/api/health")
        assert h.status_code == 200
        assert h.json()["ok"] is True

        cfg = client.get("/api/config").json()
        assert cfg["battle_mode"] == "rb"

        # Mode switch (RB -> AB -> SB -> RB)
        assert client.post("/api/mode/ab").json()["battle_mode"] == "ab"
        assert client.get("/api/state").json()["battle_mode"] == "ab"
        assert client.post("/api/mode/sb").json()["battle_mode"] == "sb"
        assert client.post("/api/mode/rb").json()["battle_mode"] == "rb"

        # Partial widget layout patch persisted to config.json
        patched = client.patch(
            "/api/config",
            json={"air_alert_duration_sec": 12, "widgets": {"ally_panel": {"x": 55.0, "scale": 1.2}}},
        ).json()
        assert patched["air_alert_duration_sec"] == 12
        assert patched["widgets"]["ally_panel"]["x"] == 55.0
        assert patched["widgets"]["ally_panel"]["scale"] == 1.2
        assert (tmp_path / "config.json").exists()

        # Reset layout
        reset = client.post("/api/config/reset-layout").json()
        assert reset["widgets"]["ally_panel"]["x"] == 16.0

        # Mock start & stop
        assert client.post("/api/mock/start?lang=ru&speed=20").status_code == 200
        assert client.post("/api/mock/stop").status_code == 200

        # Built SPA served at /
        home = client.get("/")
        assert home.status_code == 200
        assert "WT-XVM Tactical Overlay" in home.text

        # WebSocket init + ping/pong
        with client.websocket_connect("/ws") as ws:
            init_msg = ws.receive_json()
            assert init_msg["type"] == "init"
            assert "state" in init_msg and "config" in init_msg
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}


def test_air_alert_style_and_quit_endpoint(tmp_path):
    called = []
    cfg = AppConfig()
    vdb = VehicleDB()
    parser = LogParser()
    state = BattleSessionState(cfg, vdb)
    holder = {}
    hub = WebSocketHub(lambda: holder["ctx"].snapshot_dict())
    stats = StatsService(cfg, state, cache=StatsCache(tmp_path / "test_quit.db"), provider=_NoopProvider())
    watcher = TabOcrWatcher(cfg, state)
    poller = WarThunderPoller(cfg, state, parser)
    mock = MockReplayController(state, parser, on_change=hub.schedule_state_broadcast)
    ctx = RuntimeContext(
        config=cfg,
        state=state,
        parser=parser,
        stats_service=stats,
        tab_watcher=watcher,
        poller=poller,
        mock_replay=mock,
        ws_hub=hub,
        config_path=tmp_path / "config_quit.json",
        on_quit=lambda: called.append(True),
    )
    holder["ctx"] = ctx
    app = create_app(ctx, start_background_tasks=False)

    with TestClient(app) as client:
        assert client.get("/api/config").json()["air_alert_style"] == "rect"
        patched = client.patch("/api/config", json={"air_alert_style": "circle"})
        assert patched.status_code == 200
        assert patched.json()["air_alert_style"] == "circle"

        resp = client.post("/api/app/quit")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    import time

    deadline = time.monotonic() + 1.0
    while not called and time.monotonic() < deadline:
        time.sleep(0.05)
    assert called == [True]


def test_widget_opacity_and_toggle_hotkey_config(tmp_path):
    from wt_xvm.desktop.overlay_window import resolve_vk_code

    assert resolve_vk_code("F8") == 0x77
    assert resolve_vk_code("f12") == 0x7B
    assert resolve_vk_code("INSERT") == 0x2D
    assert resolve_vk_code("backquote") == 0xC0
    assert resolve_vk_code("o") == ord("O")
    assert resolve_vk_code("numpad1") == 0x61
    assert resolve_vk_code("NONE") is None

    hotkey_updates = []
    cfg = AppConfig()
    vdb = VehicleDB()
    parser = LogParser()
    state = BattleSessionState(cfg, vdb)
    holder = {}
    hub = WebSocketHub(lambda: holder["ctx"].snapshot_dict())
    stats = StatsService(cfg, state, cache=StatsCache(tmp_path / "test_hk.db"), provider=_NoopProvider())
    watcher = TabOcrWatcher(cfg, state)
    poller = WarThunderPoller(cfg, state, parser)
    mock = MockReplayController(state, parser, on_change=hub.schedule_state_broadcast)
    ctx = RuntimeContext(
        config=cfg,
        state=state,
        parser=parser,
        stats_service=stats,
        tab_watcher=watcher,
        poller=poller,
        mock_replay=mock,
        ws_hub=hub,
        config_path=tmp_path / "config_hk.json",
        on_toggle_hotkey_changed=hotkey_updates.append,
    )
    holder["ctx"] = ctx
    app = create_app(ctx, start_background_tasks=False)

    with TestClient(app) as client:
        initial = client.get("/api/config").json()
        assert initial["toggle_overlay_hotkey"] == "F8"
        for wid in ("ally_panel", "enemy_panel", "air_alert", "enemy_air_counter"):
            assert initial["widgets"][wid]["opacity"] == 1.0

        patched = client.patch(
            "/api/config",
            json={
                "toggle_overlay_hotkey": "insert",
                "widgets": {
                    "ally_panel": {"opacity": 0.45},
                    "enemy_panel": {"opacity": 0.01},
                    "air_alert": {"opacity": 0.75},
                    "enemy_air_counter": {"opacity": 0.6},
                },
            },
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["toggle_overlay_hotkey"] == "INSERT"
        assert body["widgets"]["ally_panel"]["opacity"] == 0.45
        assert body["widgets"]["enemy_panel"]["opacity"] == 0.1
        assert body["widgets"]["air_alert"]["opacity"] == 0.75
        assert body["widgets"]["enemy_air_counter"]["opacity"] == 0.6

    assert hotkey_updates == ["INSERT"]


def test_enemy_panel_right_anchor_default_and_legacy_migration(tmp_path):
    from wt_xvm.config import load_config

    # Default config anchors enemy_panel to the right edge
    cfg = AppConfig()
    assert cfg.widgets.enemy_panel.anchor_x == "right"
    assert cfg.widgets.enemy_panel.x == 16.0
    assert cfg.widgets.ally_panel.anchor_x == "left"

    # Legacy config.json (no anchor_x, x=2096) migrates enemy_panel to anchor_x="right", x=16.0, preserving y
    legacy_path = tmp_path / "legacy_config.json"
    legacy_path.write_text(
        '{"widgets": {"enemy_panel": {"enabled": true, "x": 2096.0, "y": 280.0, "scale": 1.0, "opacity": 0.9}}}',
        encoding="utf-8",
    )
    migrated = load_config(legacy_path)
    assert migrated.widgets.enemy_panel.anchor_x == "right"
    assert migrated.widgets.enemy_panel.x == 16.0
    assert migrated.widgets.enemy_panel.y == 280.0
    assert migrated.widgets.enemy_panel.opacity == 0.9

    app = _build_test_app(tmp_path)
    with TestClient(app) as client:
        res = client.post("/api/config/reset-layout")
        assert res.status_code == 200
        enemy = res.json()["widgets"]["enemy_panel"]
        assert enemy["anchor_x"] == "right"
        assert enemy["x"] == 16.0
