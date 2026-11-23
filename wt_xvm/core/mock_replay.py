"""Built-in battle replay simulator using the workspace RU/EN log and chat samples."""

from __future__ import annotations

import asyncio
import logging
import zlib
from pathlib import Path
from typing import Callable, Literal

from wt_xvm.core.battle_state import BattleSessionState
from wt_xvm.core.log_parser import LogParser, parse_md_chat_lines, parse_md_log_lines
from wt_xvm.models import ModeStats, PlayerStatsRecord
from wt_xvm.paths import get_bundle_dir

logger = logging.getLogger("wt_xvm.core.mock_replay")

_MOCK_ROSTERS: dict[str, tuple[list[str], list[str]]] = {
    "en": (
        [
            "Ikaros_02",
            "[AuPth] daniel_mosko1",
            "┾SYRAX┿ babou95",
            "[iCAT] TuGuZZZ",
            "[_1ST_] Mon1k3",
            "=T4T= ⋇ChaMaGo-",
            "QuantomKnight",
            "Fenixon777",
            "karuoce",
            "KDgame__PL_",
            "Marcieo_MP",
            "kouzelnicek_",
            "Keik323",
            "PastHornet84400",
            "cxdbb",
            "Petrix86",
        ],
        [
            "=PCIA= mehdiji",
            "SHADOW-",
            "=LIM5= ⋇Zbuntowany_ironm",
            "=KyMAR= -Н_П_Т-",
            "⋇RUS_Moscow_Bomj",
            "[QGS] worm96930",
            "[bXd] BaTHbIu_ZoV_OpKa",
            "=EXPLN= SirFynnster",
            "┺xALLx┻ Тaker",
            "[NKVDz] YourBro_10",
            "-ALSN- 弦_",
            "PereLucky",
            "[12RCC] P4SKUD4 блин",
            "-e6a- Дедушка Вова",
            "[BRCM] ⋇Dragon9993517",
            "-77MR- BELAZ Z Z",
            "[DHrs] ⋇kkomakbr",
            "_97e917jr3",
            "-VdeK- ⋇AzZuZoW-_-",
            "-COGAS- xXBlutrabeXx#1",
            "⋇davlet_092",
        ],
    ),
    "ru": (
        [
            "Ikaros_02",
            "[BALTS] ⋇rSemjonovs",
            "[1HACK] vovka716",
            "[1HACK] roma_chumeyko",
            "DenebHyde",
            "evilllllllllllll",
            "=REKET= ⋇WHITE_DEATHwtsn0",
            "sergboombomm",
            "tankwidow",
        ],
        [
            "[OGAST] SimonSimon1",
            "=CNWTP= Tinker_qoi",
            "⋇funny_bee265",
            "⋇Pluto027oceanic",
            "[HQ22] Duce67",
            "-HGFS- KernelPanic_0x0",
            "[GSRpl] TomixPl",
            "^OM2GD^ FARTYK#2",
            "-KBRN- Вливаю",
            "=OFN= 宵崎kanade",
            "=N3RFD= ⋇osaramp06",
            "[B1D] 卡特罗",
            "-EMCIS- Urıël",
            "=HeTu= Kangmiao",
            "Renamed#109",
        ],
    ),
}


def _demo_stats_for_player(canonical_key: str, nick: str) -> PlayerStatsRecord:
    """Generate deterministic, realistic demo stats across the XVM spectrum without hitting network."""
    if canonical_key == "ikaros_02":
        return PlayerStatsRecord(
            canonical_key=canonical_key,
            nick=nick,
            status="ok",
            ab=ModeStats(winrate=52.31, kd=1.6, battles=843, kpd=65.43),
            rb=ModeStats(winrate=59.16, kd=2.4, battles=18260, kpd=74.95),
            sb=ModeStats(winrate=None, kd=None, battles=0, kpd=None),
        )
    if canonical_key in ("renamed#109", "sergboombomm"):
        return PlayerStatsRecord(canonical_key=canonical_key, nick=nick, status="not_found")

    h = zlib.crc32(canonical_key.encode("utf-8"))
    tier = h % 6
    wr_bands = (44.2, 48.6, 51.4, 54.8, 59.5, 64.3)
    kd_bands = (0.48, 0.78, 1.12, 1.58, 2.25, 3.05)
    btl_bands = (650, 1900, 4800, 11200, 21500, 36400)

    wr_rb = round(wr_bands[tier] + ((h >> 4) % 15) * 0.1, 2)
    kd_rb = round(kd_bands[tier] + ((h >> 8) % 10) * 0.02, 2)
    btl_rb = btl_bands[tier] + ((h >> 12) % 900)

    return PlayerStatsRecord(
        canonical_key=canonical_key,
        nick=nick,
        status="ok",
        ab=ModeStats(winrate=round(wr_rb - 1.5, 2), kd=round(max(0.3, kd_rb - 0.2), 2), battles=btl_rb // 4),
        rb=ModeStats(winrate=wr_rb, kd=kd_rb, battles=btl_rb),
        sb=ModeStats(winrate=round(wr_rb - 3.0, 2), kd=round(max(0.3, kd_rb - 0.3), 2), battles=max(0, (btl_rb // 25) - 40)),
    )


class MockReplayController:
    """Plays back EN or RU real battle logs into BattleSessionState for testing/UI preview."""

    def __init__(
        self,
        state: BattleSessionState,
        parser: LogParser,
        on_change: Callable[[], None] | None = None,
        data_root: Path | None = None,
    ) -> None:
        self.state = state
        self.parser = parser
        self.on_change = on_change
        self.data_root = data_root or get_bundle_dir()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    async def start(self, lang: Literal["ru", "en"] = "ru", speed: float = 5.0) -> None:
        await self.stop()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(lang=lang, speed=max(0.5, min(20.0, speed))))

    async def stop(self) -> None:
        if self._task is not None:
            self._stop_event.set()
            if not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        if self.state.mock_mode:
            self.state.mock_mode = False
            self.state.battle_active = False
            self.state.reset_session()
            self._notify()

    async def _run(self, lang: Literal["ru", "en"], speed: float) -> None:
        prefix = "RU" if lang == "ru" else "EN"
        log_file = self.data_root / f"{prefix}_Log.md"
        chat_file = self.data_root / f"{prefix}_Chat.md"

        if not log_file.exists():
            # Fallback to current working directory when running from editable source
            cwd = Path.cwd()
            log_file = cwd / f"{prefix}_Log.md"
            chat_file = cwd / f"{prefix}_Chat.md"

        if not log_file.exists():
            logger.warning("Mock log file %s not found.", log_file)
            return

        self.state.reset_session()
        self.state.mock_mode = True
        self.state.wt_connected = True
        self.state.battle_active = True

        allies, enemies = _MOCK_ROSTERS[lang]
        # Temporarily suppress network stats queue during mock replay & assign deterministic demo stats
        saved_cb = self.state._stats_enqueue_cb
        self.state.set_stats_enqueue_callback(None)
        try:
            self.state.apply_tab_ocr(allies, enemies)
            for p in self.state.players.values():
                self.state.apply_stats_result(_demo_stats_for_player(p.canonical_key, p.display_nick))
            self._notify()

            timeline: list[tuple[float, str, tuple]] = []
            for ts, msg in parse_md_log_lines(log_file.read_text(encoding="utf-8")):
                timeline.append((ts, "hud", (msg,)))
            if chat_file.exists():
                for ts, mode, sender, msg in parse_md_chat_lines(chat_file.read_text(encoding="utf-8")):
                    timeline.append((ts, "chat", (mode, sender, msg)))
            timeline.sort(key=lambda item: item[0])

            prev_ts = timeline[0][0] if timeline else 0.0
            for ts, kind, payload in timeline:
                if self._stop_event.is_set():
                    break
                delta = max(0.0, ts - prev_ts)
                prev_ts = ts
                # Compress long quiet gaps so preview remains snappy while preserving Air Alert timing
                wait_sec = min(delta, 4.0) / speed
                if wait_sec > 0.02:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=wait_sec)
                        break
                    except asyncio.TimeoutError:
                        pass

                if kind == "hud":
                    ev = self.parser.parse_hud_message(payload[0], timestamp=ts, language=lang)
                    if ev is not None:
                        self.state.apply_hud_event(ev)
                else:
                    mode, sender, msg = payload
                    ev_c = self.parser.parse_chat_message(
                        msg, sender=sender, mode=mode, enemy=False, timestamp=ts, language=lang
                    )
                    if ev_c is not None:
                        self.state.apply_chat_event(ev_c)

                for p in self.state.players.values():
                    if p.stats is None:
                        self.state.apply_stats_result(_demo_stats_for_player(p.canonical_key, p.display_nick))

                self._notify()

            # Keep battle active while ticking down air alert expiration cleanly
            while not self._stop_event.is_set() and self.state.mock_mode:
                if self.state.purge_expired_air_alert():
                    self._notify()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("MockReplay error: %s", exc)
        finally:
            self.state.set_stats_enqueue_callback(saved_cb)
