"""Tests for Gemini Live desktop screen sharing."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import types as pytypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from google.genai import types as genai_types  # noqa: E402

import main as app_main  # noqa: E402
from actions import screen_processor  # noqa: E402


class _FakeUI:
    """Minimal thread-safe UI stub for headless JarvisLive tests."""

    def __init__(self) -> None:
        self.muted = False
        self.current_file: str | None = None
        self.logs: list[str] = []
        self.states: list[str] = []
        self.screen_sharing_states: list[bool] = []
        self.on_push_to_talk = None
        self.ptt_hold = None
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.on_voice_change = None
        self.on_audio_device_change = None
        self.on_screen_share_toggle = None
        self.get_plugins = None
        self.get_plugin_settings = None
        self.tool_catalog = None
        self.run_tool = None
        self.wake_is_ready = None
        self.wake_get_state = None
        self.on_wake_toggle = None
        self.on_wake_manual = None
        self.on_wake_install = None
        self.request_say = None

    def write_log(self, text: str) -> None:
        self.logs.append(text)

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_screen_sharing(self, active: bool) -> None:
        self.screen_sharing_states.append(bool(active))

    def show_content(self, title: str, text: str) -> None:
        pass

    def show_confirm(self, title: str, detail: str) -> None:
        pass

    def hide_confirm(self) -> None:
        pass

    def start_camera_stream(self) -> None:
        pass

    def stop_camera_stream(self) -> None:
        pass


def _make_jarvis(ui: _FakeUI | None = None) -> app_main.JarvisLive:
    ui_obj = ui or _FakeUI()
    with (
        patch.object(app_main, "get_wake_word_enabled", return_value=False),
        patch.object(app_main, "get_push_to_talk_enabled", return_value=False),
    ):
        jarvis = app_main.JarvisLive(ui_obj)  # type: ignore[arg-type]
    return jarvis


class _MockLiveSession:
    """Fake Gemini Live session that mirrors AsyncSession.send_realtime_input rules."""

    def __init__(self, fail_video: Exception | None = None) -> None:
        self.audio_calls: list[genai_types.Blob] = []
        self.video_calls: list[genai_types.Blob] = []
        self.client_contents: list[dict] = []
        self.tool_responses: list[list] = []
        self.fail_video = fail_video
        self._in_flight = 0
        self.max_concurrent_sends = 0

    async def send_realtime_input(self, **kwargs) -> None:
        if len(kwargs) != 1:
            raise ValueError(f"Only one argument can be set, got {list(kwargs.keys())}")
        self._in_flight += 1
        self.max_concurrent_sends = max(self.max_concurrent_sends, self._in_flight)
        try:
            await asyncio.sleep(0.005)
            if "audio" in kwargs and kwargs["audio"] is not None:
                self.audio_calls.append(kwargs["audio"])
            elif "video" in kwargs and kwargs["video"] is not None:
                if self.fail_video is not None:
                    raise self.fail_video
                self.video_calls.append(kwargs["video"])
            else:
                raise AssertionError(f"Unexpected realtime_input keys: {list(kwargs.keys())}")
        finally:
            self._in_flight -= 1

    async def send_client_content(self, **kwargs) -> None:
        self.client_contents.append(kwargs)

    async def send_tool_response(self, *, function_responses) -> None:
        self.tool_responses.append(list(function_responses))

    async def receive(self):
        if False:
            yield None


def test_capture_screen_uses_mss_in_memory_without_disk_writes(tmp_path: Path) -> None:
    rgb = bytes([30, 120, 210]) * (16 * 12)
    fake_shot = SimpleNamespace(rgb=rgb, size=(16, 12))
    fake_sct = MagicMock()
    fake_sct.monitors = [
        {"left": 0, "top": 0, "width": 16, "height": 12},
        {"left": 0, "top": 0, "width": 16, "height": 12},
    ]
    fake_sct.grab.return_value = fake_shot

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_sct
    fake_ctx.__exit__.return_value = False

    fake_mss = pytypes.SimpleNamespace(
        mss=MagicMock(return_value=fake_ctx),
        tools=pytypes.SimpleNamespace(
            to_png=MagicMock(side_effect=AssertionError("PNG roundtrip skipped"))
        ),
    )

    before_files = set(tmp_path.iterdir())
    with (
        patch.object(screen_processor, "_MSS", True),
        patch.object(screen_processor, "_PIL", True),
        patch.object(screen_processor, "mss", fake_mss, create=True),
    ):
        data, mime = screen_processor._capture_screen()

    assert mime == "image/jpeg"
    assert data.startswith(b"\xff\xd8\xff")
    assert set(tmp_path.iterdir()) == before_files
    fake_sct.grab.assert_called_once()


def test_capture_screen_raises_when_mss_missing() -> None:
    with patch.object(screen_processor, "_MSS", False):
        with pytest.raises(RuntimeError, match="mss is not installed"):
            screen_processor._capture_screen()


def test_screen_sharing_disabled_by_default_and_guards() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)

    assert jarvis._screen_sharing is False

    # Disconnected
    ok, msg = jarvis.start_screen_sharing(source="ui")
    assert ok is False
    assert "disconnected" in msg.lower()
    assert jarvis._screen_sharing is False

    # Pipeline mode rejected & tool withheld
    jarvis._engine_caps = {"vision": True, "tools": True}
    jarvis.session = _MockLiveSession()
    ok_pipe, msg_pipe = jarvis.start_screen_sharing(source="ui")
    assert ok_pipe is False
    assert "gemini live" in msg_pipe.lower()
    cfg = jarvis._build_config()
    decl_names = {
        d.name
        for grp in (cfg.tools or [])
        for d in (getattr(grp, "function_declarations", None) or [])
        if getattr(d, "name", None)
    }
    assert "screen_share" not in decl_names
    assert "screen_process" in decl_names


def test_start_screen_sharing_respects_platform_and_wake_word() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        jarvis.session = _MockLiveSession()

        # Asleep in wake-word mode
        jarvis._wake_enabled = True
        jarvis._awake = False
        ok_sleep, _ = jarvis.start_screen_sharing(source="ui")
        assert ok_sleep is False
        assert jarvis._screen_sharing is False

        # Platform forbids screen capture (e.g. Wayland)
        jarvis._wake_enabled = False
        jarvis._awake = True
        with patch(
            "core.platform_support.detect",
            return_value=SimpleNamespace(can_capture_screen=False),
        ):
            ok_plat, msg_plat = jarvis.start_screen_sharing(source="ui")
        assert ok_plat is False
        assert "not permitted" in msg_plat.lower()
        assert jarvis._screen_sharing is False

    asyncio.run(_run())


def test_stream_video_frames_simultaneous_audio_and_stop_immediately() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.03

    counter = {"n": 0}

    def _fake_capture() -> tuple[bytes, str]:
        counter["n"] += 1
        return f"jpeg-frame-{counter['n']}".encode("ascii"), "image/jpeg"

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        jarvis._loop = loop
        session = _MockLiveSession()
        jarvis.session = session
        jarvis.out_queue = asyncio.Queue(maxsize=50)
        jarvis._realtime_send_lock = asyncio.Lock()
        assert jarvis.out_queue is not None

        send_task = asyncio.create_task(jarvis._send_realtime())
        try:
            for idx in range(5):
                jarvis.out_queue.put_nowait(
                    {"data": f"pcm-{idx}".encode(), "mime_type": "audio/pcm"}
                )

            with (
                patch.object(app_main, "_capture_screen", side_effect=_fake_capture),
                patch(
                    "core.platform_support.detect",
                    return_value=SimpleNamespace(can_capture_screen=True),
                ),
            ):
                ok, _ = jarvis.start_screen_sharing(source="ui")
                assert ok is True
                assert ui.screen_sharing_states[-1] is True

                # Wait until at least 2 video frames and all audio chunks arrive
                for _ in range(40):
                    if len(session.video_calls) >= 2 and len(session.audio_calls) >= 5:
                        break
                    await asyncio.sleep(0.02)

                assert len(session.video_calls) >= 2
                assert len(session.audio_calls) == 5
                assert session.max_concurrent_sends == 1
                assert all(
                    isinstance(b, genai_types.Blob) and b.mime_type == "image/jpeg"
                    for b in session.video_calls
                )

                # Stop sharing and verify NO frames are sent afterwards
                stopped = jarvis.stop_screen_sharing(reason="test stop")
                assert stopped is True
                assert jarvis._screen_sharing is False
                assert ui.screen_sharing_states[-1] is False

                count_at_stop = len(session.video_calls)
                captures_at_stop = counter["n"]
                await asyncio.sleep(0.15)
                assert len(session.video_calls) == count_at_stop
                assert counter["n"] == captures_at_stop
        finally:
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)

    asyncio.run(_run())


def test_identical_frames_are_deduplicated() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.02

    frames = [b"same-frame", b"same-frame", b"same-frame", b"changed-frame", b"changed-frame"]
    idx = {"i": 0}

    def _fake_capture() -> tuple[bytes, str]:
        val = frames[min(idx["i"], len(frames) - 1)]
        idx["i"] += 1
        return val, "image/jpeg"

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        session = _MockLiveSession()
        jarvis.session = session
        jarvis._realtime_send_lock = asyncio.Lock()

        with (
            patch.object(app_main, "_capture_screen", side_effect=_fake_capture),
            patch(
                "core.platform_support.detect",
                return_value=SimpleNamespace(can_capture_screen=True),
            ),
        ):
            ok, _ = jarvis.start_screen_sharing(source="ui")
            assert ok is True
            for _ in range(40):
                if idx["i"] >= len(frames):
                    break
                await asyncio.sleep(0.02)
            jarvis.stop_screen_sharing(reason="done")

        sent_payloads = [b.data for b in session.video_calls]
        assert sent_payloads == [b"same-frame", b"changed-frame"]

    asyncio.run(_run())


def test_stop_during_inflight_capture_prevents_sending() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    entered = threading.Event()
    release = threading.Event()

    def _blocking_capture() -> tuple[bytes, str]:
        entered.set()
        release.wait(timeout=2.0)
        return b"stale-frame", "image/jpeg"

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        session = _MockLiveSession()
        jarvis.session = session
        jarvis._realtime_send_lock = asyncio.Lock()

        with (
            patch.object(app_main, "_capture_screen", side_effect=_blocking_capture),
            patch(
                "core.platform_support.detect",
                return_value=SimpleNamespace(can_capture_screen=True),
            ),
        ):
            ok, _ = jarvis.start_screen_sharing(source="ui")
            assert ok is True
            await asyncio.to_thread(entered.wait, 2.0)
            jarvis.stop_screen_sharing(reason="stopped mid-capture")
            release.set()
            await asyncio.sleep(0.05)

        assert session.video_calls == []

    asyncio.run(_run())


def test_capture_error_recovery_and_auto_stop() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.02

    outcomes: list[object] = [
        RuntimeError("transient glitch"),
        (b"recovered-frame", "image/jpeg"),
        RuntimeError("err1"),
        RuntimeError("err2"),
        RuntimeError("err3"),
    ]

    def _flaky_capture() -> tuple[bytes, str]:
        nxt = outcomes.pop(0) if outcomes else RuntimeError("err-extra")
        if isinstance(nxt, Exception):
            raise nxt
        return nxt  # type: ignore[return-value]

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        session = _MockLiveSession()
        jarvis.session = session
        jarvis._realtime_send_lock = asyncio.Lock()

        with (
            patch.object(app_main, "_capture_screen", side_effect=_flaky_capture),
            patch(
                "core.platform_support.detect",
                return_value=SimpleNamespace(can_capture_screen=True),
            ),
        ):
            ok, _ = jarvis.start_screen_sharing(source="ui")
            assert ok is True
            for _ in range(60):
                if not jarvis._screen_sharing:
                    break
                await asyncio.sleep(0.02)

        assert [b.data for b in session.video_calls] == [b"recovered-frame"]
        assert jarvis._screen_sharing is False
        assert ui.screen_sharing_states[-1] is False
        assert any("capture failed" in line.lower() for line in ui.logs)

    asyncio.run(_run())


def test_api_send_error_stops_sharing_safely_without_sensitive_logging() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.02
    secret_bytes = b"TOP_SECRET_SCREEN_PIXEL_CONTENT"

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        session = _MockLiveSession(fail_video=RuntimeError("1007 unsupported frame"))
        jarvis.session = session
        jarvis._realtime_send_lock = asyncio.Lock()

        with (
            patch.object(app_main, "_capture_screen", return_value=(secret_bytes, "image/jpeg")),
            patch(
                "core.platform_support.detect",
                return_value=SimpleNamespace(can_capture_screen=True),
            ),
        ):
            ok, _ = jarvis.start_screen_sharing(source="ui")
            assert ok is True
            for _ in range(40):
                if not jarvis._screen_sharing:
                    break
                await asyncio.sleep(0.02)

        assert jarvis._screen_sharing is False
        assert ui.screen_sharing_states[-1] is False
        assert all("TOP_SECRET_SCREEN_PIXEL_CONTENT" not in line for line in ui.logs)

    asyncio.run(_run())


def test_session_disconnection_and_sleep_stop_screen_sharing() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.02

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        session = _MockLiveSession()
        jarvis.session = session
        jarvis._realtime_send_lock = asyncio.Lock()

        with (
            patch.object(app_main, "_capture_screen", return_value=(b"frame", "image/jpeg")),
            patch(
                "core.platform_support.detect",
                return_value=SimpleNamespace(can_capture_screen=True),
            ),
        ):
            # Sleep stops sharing
            ok, _ = jarvis.start_screen_sharing(source="ui")
            assert ok is True
            jarvis._awake = True
            jarvis.sleep(reason="timeout")
            assert jarvis._screen_sharing is False
            assert ui.screen_sharing_states[-1] is False

            # Finish stops sharing
            jarvis._awake = True
            ok2, _ = jarvis.start_screen_sharing(source="ui")
            assert ok2 is True
            jarvis.finish()
            assert jarvis._screen_sharing is False
            assert ui.screen_sharing_states[-1] is False

    asyncio.run(_run())


def test_run_loop_stops_screen_sharing_on_session_disconnect() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.02
    session_box: dict[str, _MockLiveSession] = {}

    class _FakeSessionCtx:
        async def __aenter__(self):
            s = _MockLiveSession()
            session_box["session"] = s
            return s

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _DisconnectError(Exception):
        pass

    async def _fake_recv():
        # Wait until screen sharing starts and sends a frame, then disconnect
        for _ in range(40):
            if jarvis.session is not None and not jarvis._screen_sharing:
                with (
                    patch.object(app_main, "_capture_screen", return_value=(b"f1", "image/jpeg")),
                    patch(
                        "core.platform_support.detect",
                        return_value=SimpleNamespace(can_capture_screen=True),
                    ),
                ):
                    jarvis.start_screen_sharing(source="ui")
            if session_box.get("session") and session_box["session"].video_calls:
                break
            await asyncio.sleep(0.02)
        raise _DisconnectError("ws closed")

    async def _idle_task():
        while True:
            await asyncio.sleep(0.1)

    async def _run() -> None:
        jarvis._conn_backoff = 0
        with (
            patch.object(app_main, "_gemini_usable", return_value=True),
            patch.object(app_main.audio_devices, "configure"),
            patch.object(app_main.audio_devices, "prefetch"),
            patch.object(jarvis, "_resolve_engine_caps", return_value=None),
            patch.object(jarvis, "_build_config", return_value=MagicMock()),
            patch.object(
                jarvis,
                "_open_session",
                side_effect=[_FakeSessionCtx(), KeyboardInterrupt()],
            ),
            patch.object(jarvis, "_listen_audio", side_effect=_idle_task),
            patch.object(jarvis, "_play_audio", side_effect=_idle_task),
            patch.object(jarvis, "_receive_audio", side_effect=_fake_recv),
            patch.object(jarvis, "_run_system_monitor", side_effect=_idle_task),
            patch.object(jarvis, "_run_background_monitor", side_effect=_idle_task),
            patch.object(jarvis, "_run_proactive_mode", side_effect=_idle_task),
            patch.object(jarvis, "_run_sleep_watch", side_effect=_idle_task),
            patch.object(app_main, "get_brief_enabled", return_value=False),
        ):
            with pytest.raises(KeyboardInterrupt):
                await jarvis.run()

        assert jarvis.session is None
        assert jarvis._screen_sharing is False
        assert ui.screen_sharing_states[-1] is False

    asyncio.run(_run())


def test_voice_tool_screen_share_and_existing_screen_process_preserved() -> None:
    ui = _FakeUI()
    jarvis = _make_jarvis(ui)
    jarvis._screen_share_interval = 0.05

    async def _run() -> None:
        jarvis._loop = asyncio.get_running_loop()
        session = _MockLiveSession()
        jarvis.session = session
        jarvis._realtime_send_lock = asyncio.Lock()

        with (
            patch.object(app_main, "_capture_screen", return_value=(b"img", "image/jpeg")),
            patch(
                "core.platform_support.detect",
                return_value=SimpleNamespace(can_capture_screen=True),
            ),
        ):
            fr_start = await jarvis._execute_tool(
                SimpleNamespace(id="1", name="screen_share", args={"action": "start"})
            )
            assert fr_start.response is not None
            assert "started" in str(fr_start.response["result"]).lower()
            assert jarvis._screen_sharing is True

            fr_status = await jarvis._execute_tool(
                SimpleNamespace(id="2", name="screen_share", args={"action": "status"})
            )
            assert fr_status.response is not None
            assert "active" in str(fr_status.response["result"]).lower()

            fr_stop = await jarvis._execute_tool(
                SimpleNamespace(id="3", name="screen_share", args={"action": "stop"})
            )
            assert fr_stop.response is not None
            assert "stopped" in str(fr_stop.response["result"]).lower()
            assert jarvis._screen_sharing is False

            # Existing one-shot screen_process remains intact
            fr_one = await jarvis._execute_tool(
                SimpleNamespace(
                    id="4",
                    name="screen_process",
                    args={"angle": "screen", "text": "What is open?"},
                )
            )
            assert fr_one.response is not None
            assert "[VISION_ACTIVE]" in str(fr_one.response["result"])
            assert await jarvis._flush_pending_vision() is True
            assert len(session.client_contents) == 1

    asyncio.run(_run())

    limits = app_main._describe_limits(has_vision=True, has_mic=True)
    assert "explicitly enabled live screen sharing" in limits


def test_ui_controls_and_visible_sharing_indicator() -> None:
    from PyQt6.QtWidgets import QApplication

    import ui as ui_mod

    app = QApplication.instance() or QApplication(sys.argv)
    face_png = str(Path(__file__).resolve().parent.parent / "config" / "openagent.png")

    with patch("gui.window.MainWindow._check_config", return_value=True):
        jarvis_ui = ui_mod.JarvisUI(face_png)

    win = jarvis_ui._win
    try:
        assert win._screen_sharing is False
        assert win.hud.screen_sharing is False
        assert win._screen_share_btn.text() == "SHARE SCREEN"
        assert ("SCREEN · SHARING", "ACC") not in win._status_strip._items

        toggle_calls: list[bool] = []

        def _on_toggle(enable: bool) -> tuple[bool, str]:
            toggle_calls.append(enable)
            return enable, "ok"

        jarvis_ui.on_screen_share_toggle = _on_toggle
        win._screen_share_btn.click()

        assert toggle_calls == [True]
        assert win._screen_sharing is True
        assert win.hud.screen_sharing is True
        assert win._screen_share_btn.text() == "STOP SCREEN SHARE"
        assert ("SCREEN · SHARING", "ACC") in win._status_strip._items
        assert "ON" in win._ctrl_screen_btn.text()

        # Thread-safe signal reset (e.g. session disconnect)
        jarvis_ui.set_screen_sharing(False)
        app.processEvents()
        assert win._screen_sharing is False
        assert win.hud.screen_sharing is False
        assert win._screen_share_btn.text() == "SHARE SCREEN"
        assert ("SCREEN · SHARING", "ACC") not in win._status_strip._items
    finally:
        win.close()
