import ctypes

import pytest

import kakaocli_win.backend as backend
from kakaocli_win.backend import KakaoError, WindowInfo


def window(title="개발팀", hwnd=1):
    return WindowInfo(hwnd, title, backend.CHAT_CLASS, 380, 640, True, "chat", pid=123)


def test_duplicate_exact_titles_are_rejected(monkeypatch):
    monkeypatch.setattr(backend, "list_rooms", lambda: [window(hwnd=1), window(hwnd=2)])
    with pytest.raises(KakaoError, match="동일한 제목"):
        backend.find_room("개발팀", exact=True)


@pytest.mark.parametrize("room", ["", " ", "\n"])
def test_empty_room_is_rejected_before_enumerating(monkeypatch, room):
    monkeypatch.setattr(backend, "list_rooms", lambda: pytest.fail("must not list all rooms"))
    with pytest.raises(KakaoError, match="비어"):
        backend.find_room(room)


def test_draft_is_preserved_before_any_mutating_message(monkeypatch):
    monkeypatch.setattr(backend, "_assert_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend, "_read_input_text", lambda _hwnd: "기존 초안")
    monkeypatch.setattr(backend.win32gui, "SendMessage", lambda *args: pytest.fail("must preserve draft"))
    with pytest.raises(KakaoError, match="기존 초안"):
        backend._set_text(2, "전송할 메시지")


def test_input_text_uses_marshaled_control_text_not_window_caption(monkeypatch):
    draft = "기존 초안"
    monkeypatch.setattr(backend.win32gui, "GetWindowText", lambda _hwnd: pytest.fail("cross-process caption is unreliable"))
    monkeypatch.setattr(backend.win32gui, "SendMessage", lambda *args: len(draft))

    def read(_hwnd, message, capacity, pointer):
        assert message == backend.win32con.WM_GETTEXT
        buffer = ctypes.create_unicode_buffer(draft)
        ctypes.memmove(pointer, buffer, ctypes.sizeof(buffer))
        return len(draft)

    monkeypatch.setattr(backend.ctypes.windll.user32, "SendMessageW", read)
    assert backend._read_input_text(2) == draft


def test_unreadable_nonempty_input_is_rejected(monkeypatch):
    monkeypatch.setattr(backend.win32gui, "SendMessage", lambda *args: 5)
    monkeypatch.setattr(backend.ctypes.windll.user32, "SendMessageW", lambda *args: 0)
    with pytest.raises(KakaoError, match="안전하게"):
        backend._read_input_text(2)


def test_long_input_readback_is_not_misidentified_as_truncated(monkeypatch):
    message = "가" * 2000
    monkeypatch.setattr(backend.win32gui, "SendMessage", lambda *args: len(message))

    def read(_hwnd, _message, _capacity, pointer):
        buffer = ctypes.create_unicode_buffer(message)
        ctypes.memmove(pointer, buffer, ctypes.sizeof(buffer))
        return len(message)

    monkeypatch.setattr(backend.ctypes.windll.user32, "SendMessageW", read)
    assert backend._read_input_text(2) == message


def test_message_input_prefers_rea_observed_control_id(monkeypatch):
    monkeypatch.setattr(backend, "enum_children", lambda _hwnd: [2, 3, 4])
    monkeypatch.setattr(backend.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(backend.win32gui, "GetClassName", lambda _hwnd: "RICHEDIT50W")
    monkeypatch.setattr(backend.win32gui, "GetDlgCtrlID", lambda hwnd: 1006 if hwnd == 3 else 1)
    assert backend._input_handles(1) == [3]


def test_transcript_uses_verified_visible_list_not_member_search_or_hidden_list(monkeypatch):
    monkeypatch.setattr(backend, "enum_children", lambda _hwnd: [2, 3, 4, 5])
    monkeypatch.setattr(backend.win32gui, "IsWindowVisible", lambda hwnd: hwnd != 5)
    monkeypatch.setattr(backend.win32gui, "GetClassName", lambda hwnd: "SearchListCtrl" if hwnd == 2 else backend.TRANSCRIPT_CLASS)
    monkeypatch.setattr(backend.win32gui, "GetDlgCtrlID", lambda hwnd: 101 if hwnd == 4 else 100)
    assert backend._transcript_handle(1) == 3


def test_duplicate_transcript_controls_are_rejected(monkeypatch):
    monkeypatch.setattr(backend, "enum_children", lambda _hwnd: [2, 3])
    monkeypatch.setattr(backend.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(backend.win32gui, "GetClassName", lambda _hwnd: backend.TRANSCRIPT_CLASS)
    monkeypatch.setattr(backend.win32gui, "GetDlgCtrlID", lambda _hwnd: 100)
    with pytest.raises(KakaoError, match="하나로 식별"):
        backend._transcript_handle(1)


@pytest.mark.parametrize("foreground,focused,process", [
    (99, 2, (123, "KakaoTalk.exe")),
    (1, 99, (123, "KakaoTalk.exe")),
    (1, 2, None),
    (1, 2, (456, "KakaoTalk.exe")),
])
def test_target_guard_rejects_focus_or_process_change(monkeypatch, foreground, focused, process):
    monkeypatch.setattr(backend.win32gui, "GetAncestor", lambda *args: 1)
    monkeypatch.setattr(backend.win32gui, "GetForegroundWindow", lambda: foreground)
    monkeypatch.setattr(backend, "_focused_handle", lambda _hwnd: focused)
    monkeypatch.setattr(backend, "_window_process", lambda _hwnd: process)
    with pytest.raises(KakaoError):
        backend._assert_target(2, root=1, pid=123)


def test_spoofed_kakao_window_is_excluded(monkeypatch):
    monkeypatch.setattr(backend.win32gui, "EnumWindows", lambda callback, _unused: callback(1, None))
    monkeypatch.setattr(backend.win32gui, "GetClassName", lambda _hwnd: backend.CHAT_CLASS)
    monkeypatch.setattr(backend.win32gui, "GetWindowText", lambda _hwnd: "카카오톡")
    monkeypatch.setattr(backend, "_window_process", lambda _hwnd: None)
    assert backend.list_windows() == []


def prepare_send(monkeypatch):
    state = {"text": "", "pressed": [], "assertions": 0}
    monkeypatch.setattr(backend, "_prepare_send", lambda room, *, exact: (window(room), 2) if exact else pytest.fail("must match exactly"))
    monkeypatch.setattr(backend, "_input_handles", lambda _hwnd: [2])
    monkeypatch.setattr(backend, "_focus", lambda _hwnd: None)
    monkeypatch.setattr(backend, "_assert_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend.win32gui, "GetWindowText", lambda _hwnd: "개발팀")
    monkeypatch.setattr(backend, "_read_input_text", lambda _hwnd: state["text"])
    monkeypatch.setattr(backend.time, "sleep", lambda _duration: None)

    def set_text(_hwnd, text):
        state["text"] = text
        return True

    def press(vk):
        state["pressed"].append(vk)
        state["text"] = ""

    monkeypatch.setattr(backend, "_set_text", set_text)
    monkeypatch.setattr(backend, "_press", press)
    return state


def test_send_exact_default_and_checked_content(monkeypatch):
    state = prepare_send(monkeypatch)
    assert backend.send_message("개발팀", "메시지", interactive=True).title == "개발팀"
    assert state["pressed"] == [backend.win32con.VK_RETURN]


def test_focus_change_before_enter_prevents_submission(monkeypatch):
    state = prepare_send(monkeypatch)

    def guard(*args, **kwargs):
        state["assertions"] += 1
        if state["assertions"] == 2:
            raise KakaoError("포커스가 바뀌었습니다")

    monkeypatch.setattr(backend, "_assert_target", guard)
    with pytest.raises(KakaoError, match="포커스"):
        backend.send_message("개발팀", "메시지", interactive=True)
    assert state["pressed"] == []
    assert state["text"] == "메시지"


def test_content_change_before_enter_prevents_submission(monkeypatch):
    state = prepare_send(monkeypatch)

    def changed_text(_hwnd, text):
        state["text"] = text + "다른 내용"
        return True

    monkeypatch.setattr(backend, "_set_text", changed_text)
    with pytest.raises(KakaoError, match="전송 직전에"):
        backend.send_message("개발팀", "메시지", interactive=True)
    assert state["pressed"] == []


def test_room_title_change_before_typing_preserves_empty_input(monkeypatch):
    state = prepare_send(monkeypatch)
    monkeypatch.setattr(backend.win32gui, "GetWindowText", lambda _hwnd: "다른 방")
    with pytest.raises(KakaoError, match="제목이 바뀌었습니다"):
        backend.send_message("개발팀", "메시지", interactive=True)
    assert state["pressed"] == []
    assert state["text"] == ""


def test_room_title_change_before_enter_prevents_submission(monkeypatch):
    state = prepare_send(monkeypatch)
    titles = iter(["개발팀", "다른 방"])
    monkeypatch.setattr(backend.win32gui, "GetWindowText", lambda _hwnd: next(titles))
    with pytest.raises(KakaoError, match="제목이 바뀌었습니다"):
        backend.send_message("개발팀", "메시지", interactive=True)
    assert state["pressed"] == []
    assert state["text"] == "메시지"


def test_rich_edit_line_ending_normalization_allows_multiline_content(monkeypatch):
    state = prepare_send(monkeypatch)

    def normalized(_hwnd, text):
        state["text"] = text.replace("\n", "\r\n")
        return True

    monkeypatch.setattr(backend, "_set_text", normalized)
    assert backend.send_message("개발팀", "첫째 줄\n둘째 줄", interactive=True).title == "개발팀"
    assert state["pressed"] == [backend.win32con.VK_RETURN]


def test_accepted_native_events_without_readback_cannot_trigger_enter(monkeypatch):
    monkeypatch.setattr(backend, "_assert_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(backend, "_read_input_text", lambda _hwnd: "")
    monkeypatch.setattr(backend.win32gui, "SendMessage", lambda *args: 0)
    typed = []
    monkeypatch.setattr(backend, "_send_unicode_input", lambda text: typed.append(text) or True)
    assert backend._set_text(2, "메시지") is False
    assert typed == ["메시지"]


def prepare_preview(monkeypatch, *, draft="", control_id=1006):
    monkeypatch.setattr(backend, "list_rooms", lambda: [window()])
    monkeypatch.setattr(backend, "_input_handles", lambda _hwnd: [2])
    monkeypatch.setattr(backend.win32gui, "GetDlgCtrlID", lambda _hwnd: control_id)
    monkeypatch.setattr(backend.win32gui, "GetWindowText", lambda _hwnd: "개발팀")
    monkeypatch.setattr(backend, "_assert_process_target", lambda *args, **kwargs: 1)
    monkeypatch.setattr(backend, "_read_input_text", lambda _hwnd: draft)
    monkeypatch.setattr(backend, "_focus", lambda *args: pytest.fail("preview must not change focus"))
    monkeypatch.setattr(backend, "_set_text", lambda *args: pytest.fail("preview must not type"))
    monkeypatch.setattr(backend, "_press", lambda *args: pytest.fail("preview must not press keys"))
    def unavailable(*args, **kwargs):
        raise KakaoError("직접 독립 창으로 열어 주세요.")
    monkeypatch.setattr("kakaocli_win.background.ensure_room", unavailable)


def test_preview_verifies_target_without_focusing_or_typing(monkeypatch):
    prepare_preview(monkeypatch)
    result = backend.preview_send("개발팀")
    assert result["target_ready"] is True
    assert result["draft_present"] is False
    assert result["input_hwnd"] == "0x2"
    assert result["target"]["pid"] == 123


def test_preview_rejects_draft_without_mutating_it(monkeypatch):
    prepare_preview(monkeypatch, draft="보관할 초안")
    with pytest.raises(KakaoError, match="기존 초안"):
        backend.preview_send("개발팀")


def test_preview_rejects_unverified_input_control_id(monkeypatch):
    prepare_preview(monkeypatch, control_id=1007)
    with pytest.raises(KakaoError, match="1006"):
        backend.preview_send("개발팀")


def test_preview_rejects_absent_exact_room(monkeypatch):
    prepare_preview(monkeypatch)
    with pytest.raises(KakaoError, match="직접 독립 창으로"):
        backend.preview_send("개발")
