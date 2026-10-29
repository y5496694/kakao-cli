import pytest

from kakaocli_win import backend
from kakaocli_win.backend import KakaoError, WindowInfo


def clock(monkeypatch):
    state = {"now": 0.0}
    monkeypatch.setattr(backend.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(backend.time, "sleep", lambda seconds: state.update(now=state["now"] + seconds))
    monkeypatch.setattr(backend, "_assert_process_target", lambda *args, **kwargs: 1)
    monkeypatch.setattr(backend.win32gui, "IsWindowEnabled", lambda _hwnd: True)
    return state


def test_delayed_activation_and_focus_settle_before_input(monkeypatch):
    state = clock(monkeypatch)
    monkeypatch.setattr(backend.win32gui, "GetForegroundWindow", lambda: 99 if state["now"] < .05 else 1)
    monkeypatch.setattr(backend, "_focused_handle", lambda _hwnd: 99 if state["now"] < .1 else 2)
    backend._wait_for_focus(2, root=1)
    assert .15 <= state["now"] <= .8


def test_focus_settling_is_bounded(monkeypatch):
    state = clock(monkeypatch)
    monkeypatch.setattr(backend.win32gui, "GetForegroundWindow", lambda: 99)
    monkeypatch.setattr(backend, "_focused_handle", lambda _hwnd: 99)
    with pytest.raises(KakaoError, match="안정되지"):
        backend._wait_for_focus(2, root=1)
    assert state["now"] == pytest.approx(.8)


def test_disabled_modal_owner_is_never_activated(monkeypatch):
    monkeypatch.setattr(backend.win32gui, "GetAncestor", lambda *args: 1)
    monkeypatch.setattr(backend.win32gui, "IsWindowEnabled", lambda _hwnd: False)
    monkeypatch.setattr(backend.win32gui, "ShowWindow", lambda *args: pytest.fail("must not disturb app modal dialog"))
    with pytest.raises(KakaoError, match="모달"):
        backend._focus(2)


def test_default_read_uses_background_without_focus(monkeypatch):
    calls = []
    window = WindowInfo(1, "테스트 방", backend.CHAT_CLASS, 100, 100, False, "chat")
    def read(room, **kwargs):
        calls.append((room, kwargs))
        return window, "fixture transcript"
    monkeypatch.setattr("kakaocli_win.background.read_visible", read)
    monkeypatch.setattr(backend, "_focus", lambda *args: pytest.fail("background must not focus"))
    assert backend.read_visible("테스트 방", exact=True) == (window, "fixture transcript")
    assert calls == [("테스트 방", {"exact": True, "timeout": 1.5})]


def test_default_send_uses_background_without_global_input(monkeypatch):
    calls = []
    window = WindowInfo(1, "테스트 방", backend.CHAT_CLASS, 100, 100, False, "chat")
    def send(room, message, **kwargs):
        calls.append((room, message, kwargs))
        return window
    monkeypatch.setattr("kakaocli_win.background.send_message", send)
    monkeypatch.setattr(backend, "_focus", lambda *args: pytest.fail("background must not focus"))
    monkeypatch.setattr(backend, "_press", lambda *args: pytest.fail("background must not inject global keys"))
    assert backend.send_message("테스트 방", "메시지") == window
    assert calls == [("테스트 방", "메시지", {"exact": True})]
