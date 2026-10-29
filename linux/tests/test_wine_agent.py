import importlib.util
import io
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


AGENT_PATH = Path(__file__).resolve().parents[1] / "wine_agent.py"
spec = importlib.util.spec_from_file_location("wine_agent", AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def test_isolation_flag_is_mandatory_before_loading_wine(monkeypatch):
    monkeypatch.delenv("KAKAOCLI_ISOLATED_DESKTOP", raising=False)
    monkeypatch.setattr(agent.ctypes, "WinDLL", lambda _: pytest.fail("Must check isolation first"), raising=False)
    with pytest.raises(agent.AgentError) as caught:
        agent._require_isolated_wine()
    assert caught.value.code == "isolation_required"


def test_native_windows_is_rejected_even_with_flag(monkeypatch):
    monkeypatch.setenv("KAKAOCLI_ISOLATED_DESKTOP", "1")
    monkeypatch.setattr(agent.ctypes, "WinDLL", lambda _: SimpleNamespace(), raising=False)
    with pytest.raises(agent.AgentError) as caught:
        agent._require_isolated_wine()
    assert caught.value.code == "wine_required"


def test_real_wine_export_is_called(monkeypatch):
    monkeypatch.setenv("KAKAOCLI_ISOLATED_DESKTOP", "1")
    class Version:
        def __call__(self):
            return b"9.0"
    probe = Version()
    monkeypatch.setattr(agent.ctypes, "WinDLL", lambda name: SimpleNamespace(wine_get_version=probe), raising=False)
    assert agent._require_isolated_wine() == "9.0"
    assert probe.argtypes == []


def test_guard_prevents_any_backend_import(monkeypatch):
    monkeypatch.setattr(agent, "_require_isolated_wine", lambda: (_ for _ in ()).throw(agent.AgentError("wine_required", "Refused")))
    monkeypatch.setattr(agent, "_load_modules", lambda: pytest.fail("Must not load Win32 modules"))
    with pytest.raises(agent.AgentError):
        agent.handle_request({"operation": "command", "argv": ["send", "홍길동", "예시", "--yes"]})


def test_unknown_operation_does_not_import_backend(monkeypatch):
    monkeypatch.setattr(agent, "_require_isolated_wine", lambda: "9.0")
    monkeypatch.setattr(agent, "_load_modules", lambda: pytest.fail("Unknown operation must not import backend"))
    with pytest.raises(agent.AgentError):
        agent.handle_request({"operation": "run-shell"})


@pytest.fixture
def modules(monkeypatch):
    from kakaocli_win import cli
    from kakaocli_win.transcript import loaded_messages
    calls = []
    window = SimpleNamespace(hwnd=20, title="홍길동", kind="chat", pid=100, to_dict=lambda: {"title": "홍길동", "hwnd": "0x14"})
    resolver = lambda *args, **kwargs: pytest.fail("Original resolver must never be called")
    b = SimpleNamespace(
        _resolve_room=resolver, list_windows=lambda: [window], list_rooms=lambda: [window],
        _assert_room_title=lambda item: None, _assert_window_enabled=lambda hwnd: None,
        doctor=lambda: {"ok": True},
        find_main_window=lambda: None, enum_children=lambda hwnd: [],
        read_visible=lambda *args, **kwargs: calls.append(("read", args, kwargs)) or (window, "[홍길동] [오전 9:00] 예시 대화"),
        send_message=lambda *args, **kwargs: calls.append(("send", args, kwargs)) or window,
        preview_send=lambda *args, **kwargs: {"target_ready": True, "draft_present": False},
        win32gui=SimpleNamespace(GetClassName=lambda hwnd: "EVA_Window_Dblclk", IsWindowVisible=lambda hwnd: True),
    )
    a = SimpleNamespace(_windows=lambda pid: [])
    monkeypatch.setattr(agent, "_require_isolated_wine", lambda: "9.0")
    monkeypatch.setattr(agent, "_load_modules", lambda: (b, a, cli, loaded_messages))
    return b, a, calls, resolver


def test_command_rooms_returns_plain_window_list(modules):
    assert agent.handle_request({"operation": "command", "argv": ["rooms", "--json"]}) == [{"title": "홍길동", "hwnd": "0x14"}]


@pytest.mark.parametrize("child_title,visible,enabled,available,ready", [
    ("InitialAgreementView", True, True, False, False),
    ("LoginView", True, True, False, False),
    ("ChatRoomListView_example", True, True, True, True),
    ("ChatRoomListView_example", False, True, False, False),
    ("ChatRoomListView_example", True, False, True, False),
])
def test_doctor_distinguishes_started_app_from_chat_ui(modules, child_title, visible, enabled, available, ready):
    b, _, calls, _ = modules
    b.doctor = lambda: {"ok": True, "running": True, "main_window_found": True, "next": "준비 완료"}
    b.find_main_window = lambda: SimpleNamespace(hwnd=10)
    b.enum_children = lambda hwnd: [11]
    b.win32gui.GetWindowText = lambda hwnd: child_title
    b.win32gui.IsWindowVisible = lambda hwnd: visible
    b.win32gui.IsWindowEnabled = lambda hwnd: enabled
    result = agent.handle_request({"operation": "command", "argv": ["doctor", "--json"]})
    assert result["ok"] is True
    assert result["chat_list_available"] is available
    assert result["ready_for_chats"] is ready
    assert result["login_state"] == ("chat_list_available" if available else "setup_or_login_required")
    assert result["server_authentication_verified"] is False
    assert result["next"] != "준비 완료"
    assert calls == []


def test_doctor_preserves_failed_app_probe_and_reports_not_ready(modules):
    b, _, _, _ = modules
    b.doctor = lambda: {"ok": False, "running": False, "main_window_found": False}
    result = agent.handle_request({"operation": "command", "argv": ["doctor"]})
    assert result["ok"] is False
    assert result["ready_for_chats"] is False
    assert result["chat_list_available"] is False
    assert result["login_state"] == "setup_or_login_required"


def test_read_explicitly_uses_interactive_backend_only_inside_wine(modules):
    b, _, calls, resolver = modules
    result = agent.handle_request({"operation": "command", "argv": ["read", "홍길동"]})
    assert result["coverage"] == "loaded_ui_only"
    assert calls[0][2] == {"exact": True, "interactive": True}
    assert b._resolve_room is resolver


def test_send_requires_yes_and_preserves_preview(modules):
    _, _, calls, _ = modules
    with pytest.raises(agent.AgentError) as caught:
        agent.handle_request({"operation": "command", "argv": ["send", "홍길동", "예시"]})
    assert caught.value.code == "confirmation_required"
    assert calls == []
    result = agent.handle_request({"operation": "command", "argv": ["send", "홍길동", "예시", "--dry-run"]})
    assert result["submitted"] is False
    assert calls == []


def test_send_yes_still_reports_delivery_unconfirmed(modules):
    _, _, calls, _ = modules
    result = agent.handle_request({"operation": "command", "argv": ["send", "홍길동", "예시", "--yes"]})
    assert result["status"] == "submitted_unconfirmed"
    assert result["delivery_confirmed"] is False
    assert calls[0][2]["interactive"] is True


def test_missing_room_never_falls_back_to_frida(modules):
    b, _, calls, _ = modules
    b.list_windows = lambda: []
    with pytest.raises(agent.AgentError) as caught:
        agent.handle_request({"operation": "command", "argv": ["read", "홍길동"]})
    assert caught.value.code == "room_not_open"
    assert calls == []


def test_db_commands_are_not_exposed_by_wine_agent(modules):
    with pytest.raises(agent.AgentError) as caught:
        agent.handle_request({"operation": "command", "argv": ["recover-key"]})
    assert caught.value.code == "unsupported_command"


@pytest.mark.parametrize("point", [[True, 1], [float("nan"), 1], [1], "1,2"])
def test_bad_coordinates_are_rejected(point):
    with pytest.raises(agent.AgentError):
        agent._point(point)


def test_click_translates_window_pixels_and_targets_only_list():
    events = []
    gui = SimpleNamespace(GetWindowRect=lambda hwnd: (100, 200, 500, 800),
                          ScreenToClient=lambda hwnd, point: (point[0] - 110, point[1] - 230),
                          GetClientRect=lambda hwnd: (0, 0, 380, 550),
                          PostMessage=lambda *args: events.append(args))
    b = SimpleNamespace(win32gui=gui, _focus=lambda hwnd: events.append(("focus", hwnd)))
    agent._click(b, 10, 11, [30, 50], double=True)
    assert events[0] == ("focus", 11)
    assert [event[1] for event in events[1:]] == [0x201, 0x202, 0x203, 0x202]
    assert all(event[0] == 11 and event[3] == (20 << 16 | 20) for event in events[1:])


def test_click_outside_transcript_never_focuses():
    gui = SimpleNamespace(GetWindowRect=lambda hwnd: (0, 0, 400, 600),
                          ScreenToClient=lambda hwnd, point: (-1, 2), GetClientRect=lambda hwnd: (0, 0, 300, 500))
    b = SimpleNamespace(win32gui=gui, _focus=lambda hwnd: pytest.fail("Bad point must not focus"))
    with pytest.raises(agent.AgentError) as caught:
        agent._click(b, 10, 11, [1, 2])
    assert caught.value.code == "point_outside_control"


def test_search_probe_restores_query_when_capture_fails(monkeypatch, tmp_path):
    values = {5: "previous"}
    main = SimpleNamespace(hwnd=1)
    b = SimpleNamespace(_read_input_text=lambda hwnd: values[hwnd], _focus=lambda hwnd: None)
    a = SimpleNamespace(_capture=lambda *args: (_ for _ in ()).throw(RuntimeError("capture failed")))
    monkeypatch.setattr(agent, "_main_controls", lambda *args: (main, 2, 5))
    monkeypatch.setattr(agent, "_set_search", lambda b, hwnd, text: values.__setitem__(hwnd, text))
    monkeypatch.setattr(agent.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="capture failed"):
        agent._search_probe({"query": "홍길동", "output": str(tmp_path / "probe.png")}, b, a)
    assert values[5] == "previous"


def test_open_selected_refuses_changed_search_before_click(monkeypatch):
    monkeypatch.setattr(agent, "_main_controls", lambda *args: (SimpleNamespace(hwnd=1), 2, 3))
    b = SimpleNamespace(_read_input_text=lambda hwnd: "changed")
    monkeypatch.setattr(agent, "_click", lambda *args, **kwargs: pytest.fail("Must not click changed results"))
    with pytest.raises(agent.AgentError) as caught:
        agent._open_selected({"query": "홍길동", "expected_title": "예시학교 홍길동", "point": [30, 80]}, b, None)
    assert caught.value.code == "search_changed"


def test_request_limit_is_checked_before_any_control(monkeypatch, capsys):
    monkeypatch.setattr(agent.sys, "stdin", io.TextIOWrapper(io.BytesIO(b" " * (agent.REQUEST_LIMIT + 1)), encoding="utf-8"))
    monkeypatch.setattr(agent, "handle_request", lambda _: pytest.fail("Oversized input must be rejected"))
    assert agent.main() == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "request_too_large"


def test_main_protocol_returns_one_json_object(monkeypatch, capsys):
    monkeypatch.setattr(agent.sys, "stdin", io.StringIO('{"operation":"command","argv":["doctor"]}'))
    monkeypatch.setattr(agent, "handle_request", lambda _: {"runtime": "wine"})
    assert agent.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "result": {"runtime": "wine"}}


@pytest.mark.parametrize("needs_focus_fallback", [False, True])
def test_original_save_checks_path_file_stability_and_hash(tmp_path, monkeypatch, needs_focus_fallback):
    destination = tmp_path / "received.bin"
    state = {"text": "original.bin", "dialog": True}
    clock, events = [0.0], []
    window = SimpleNamespace(hwnd=20, title="홍길동", pid=100)
    def commit():
        destination.write_bytes(b"original attachment data")
        state["dialog"] = False
    def post(hwnd, message, *args):
        events.append(("post", hwnd, message))
        if not needs_focus_fallback:
            commit()
    def press(key):
        events.append(("press", key))
        commit()
    gui = SimpleNamespace(
        GetClassName=lambda hwnd: "Edit", GetDlgCtrlID=lambda hwnd: 1001,
        GetDlgItem=lambda hwnd, ident: 32, GetWindowText=lambda hwnd: "Save",
        SendMessage=lambda hwnd, msg, zero, text: state.__setitem__("text", text),
        PostMessage=post, IsWindow=lambda hwnd: state["dialog"],
    )
    b = SimpleNamespace(win32gui=gui, enum_children=lambda hwnd: [31],
                        win32con=SimpleNamespace(WM_SETTEXT=0xC, BM_CLICK=0xF5, VK_SPACE=32),
                        _read_input_text=lambda hwnd: state["text"],
                        _focus=lambda hwnd: events.append(("focus", hwnd)),
                        _assert_target=lambda *args, **kwargs: None, _press=press)
    a = SimpleNamespace(_owned_by_room=lambda *args: True,
                        _destination=lambda *args: destination,
                        _sha256=lambda path: hashlib.sha256(path.read_bytes()).hexdigest())
    args = SimpleNamespace(kind="file", output_dir=str(tmp_path), filename=None, timeout=3)
    monkeypatch.setattr(agent.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = agent._save_pending(window, 30, args, b, a)
    assert result["saved"] is True
    assert result["bytes"] == len(b"original attachment data")
    assert result["sha256"] == hashlib.sha256(b"original attachment data").hexdigest()
    assert result["save_dialog_closed"] is True
    assert any(event[0] == "press" for event in events) is needs_focus_fallback


def test_download_existing_unrelated_modal_blocks_attachment_click(monkeypatch):
    b = SimpleNamespace()
    a = SimpleNamespace(_save_dialogs=lambda window: [])
    monkeypatch.setattr(agent, "_modal_windows", lambda *args: [99])
    args = SimpleNamespace(at=[20, 80], mode="viewer")
    with pytest.raises(agent.AgentError) as caught:
        agent._download(args, SimpleNamespace(hwnd=20), b, a)
    assert caught.value.code == "pending_modal"
