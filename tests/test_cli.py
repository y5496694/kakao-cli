import io
import json

import pytest

import kakaocli_win.backend as backend
from kakaocli_win.backend import KakaoError, WindowInfo
import kakaocli_win.cli as cli
from kakaocli_win.cli import build_parser


def test_send_dry_run_arguments():
    args = build_parser().parse_args(["--json", "send", "친구", "안녕", "--dry-run"])
    assert args.json is True
    assert args.command == "send"
    assert args.room == "친구"
    assert args.message == "안녕"
    assert args.dry_run is True


def test_send_requires_message():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["send", "친구"])


def test_send_self_dry_run_arguments():
    args = build_parser().parse_args(["--json", "send-self", "메모", "--dry-run"])
    assert args.command == "send-self"
    assert args.message == "메모"
    assert args.dry_run is True
    assert args.yes is False


def test_inspect_room_argument():
    args = build_parser().parse_args(["inspect", "--room", "테스트방"])
    assert args.room == "테스트방"


def test_friends_filter_arguments():
    args = build_parser().parse_args(["--json", "friends", "--contains", "vs"])
    assert args.command == "friends"
    assert args.contains == "vs"
    assert args.include_hidden is False


def test_chat_rooms_filter_arguments():
    args = build_parser().parse_args(
        ["--json", "chat-rooms", "--contains", "바다", "--type", "MultiChat", "--limit", "5"]
    )
    assert args.command == "chat-rooms"
    assert args.contains == "바다"
    assert args.room_type == "MultiChat"
    assert args.limit == 5
    assert args.include_ids is False


def test_export_room_contacts_arguments():
    args = build_parser().parse_args(
        [
            "--json",
            "export-room-contacts",
            "동창방",
            "--output",
            "contacts.csv",
            "--exact",
        ]
    )
    assert args.command == "export-room-contacts"
    assert args.room == "동창방"
    assert args.output == "contacts.csv"
    assert args.exact is True
    assert args.allow_partial is False


def make_window(title: str, hwnd: int) -> WindowInfo:
    return WindowInfo(hwnd, title, backend.CHAT_CLASS, 380, 640, True, "chat")


def test_find_room_rejects_ambiguous_partial_match(monkeypatch):
    monkeypatch.setattr(
        backend,
        "list_rooms",
        lambda: [make_window("개발팀", 1), make_window("개발팀 공지", 2)],
    )
    with pytest.raises(KakaoError, match="여러 개"):
        backend.find_room("개발팀")


def test_find_room_exact_match(monkeypatch):
    expected = make_window("개발팀", 1)
    monkeypatch.setattr(
        backend,
        "list_rooms",
        lambda: [expected, make_window("개발팀 공지", 2)],
    )
    assert backend.find_room("개발팀", exact=True) == expected


def test_open_room_propagates_search_failure(monkeypatch):
    monkeypatch.setattr(backend, "list_rooms", lambda: [])
    def unavailable(*args, **kwargs):
        raise KakaoError("검색 결과가 모호합니다.")
    monkeypatch.setattr("kakaocli_win.background.ensure_room", unavailable)
    with pytest.raises(KakaoError, match="검색 결과가 모호"):
        backend.open_room("개발팀", exact=True)


def test_set_text_falls_back_to_replace_selection(monkeypatch):
    state = {"text": ""}

    def send_message(_hwnd, message, _wparam, lparam):
        if message == backend.win32con.EM_REPLACESEL:
            state["text"] = lparam

    monkeypatch.setattr(backend.win32gui, "SendMessage", send_message)
    monkeypatch.setattr(backend, "_assert_target", lambda _hwnd: None)
    monkeypatch.setattr(backend, "_read_input_text", lambda _hwnd: state["text"])
    assert backend._set_text(1, "테스트") is True
    assert state["text"] == "테스트"


@pytest.mark.parametrize("argv", [
    ["--json", "send", "친구", "안녕", "--dry-run"],
    ["send", "--json", "친구", "안녕", "--dry-run"],
    ["send", "친구", "안녕", "--dry-run", "--json"],
])
def test_json_before_or_after_command(argv):
    args = build_parser().parse_args(argv)
    assert args.json is True
    assert args.exact is True


def test_send_message_file_preserves_utf8_and_shell_characters(tmp_path, monkeypatch, capsys):
    message = '한글 "따옴표" & $(표현식)\n둘째 줄'
    path = tmp_path / "message.txt"
    path.write_text(message, encoding="utf-8-sig")
    monkeypatch.setattr(cli, "preview_send", lambda *args, **kwargs: {"target_ready": True, "draft_present": False})
    assert cli.main(["send", "친구", "--message-file", str(path), "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["message"] == message
    assert preview["exact"] is True
    assert preview["sent"] is False


def test_send_stdin_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("한글\n둘째 줄"))
    monkeypatch.setattr(cli, "preview_send", lambda *args, **kwargs: {"target_ready": True, "draft_present": False})
    assert cli.main(["send", "친구", "--stdin", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["message"] == "한글\n둘째 줄"


@pytest.mark.parametrize("extra", [["text", "--stdin"], ["--message-file", "x", "--stdin"]])
def test_send_requires_exactly_one_source(extra):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["send", "친구", *extra])


def test_noninteractive_send_requires_explicit_yes(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("yes\n"))
    calls = []
    monkeypatch.setattr(cli, "send_message", lambda *args, **kwargs: calls.append(args))
    assert cli.main(["send", "친구", "메시지", "--json"]) == 1
    assert "--yes" in json.loads(capsys.readouterr().err)["error"]
    assert calls == []


def test_input_submission_does_not_claim_delivery(monkeypatch, capsys):
    calls = []

    def submit(room, message, *, exact, interactive):
        calls.append((room, message, exact, interactive))
        return make_window(room, 1)

    monkeypatch.setattr(cli, "send_message", submit)
    assert cli.main(["send", "친구", "메시지", "--yes", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [("친구", "메시지", True, False)]
    assert result["submitted"] is True
    assert result["delivery_confirmed"] is False
    assert "sent" not in result


def test_dry_run_requires_readonly_target_preflight(monkeypatch, capsys):
    calls = []

    def preview(room, *, exact):
        calls.append((room, exact))
        return {"target_ready": True, "draft_present": False}

    monkeypatch.setattr(cli, "preview_send", preview)
    monkeypatch.setattr(cli, "send_message", lambda *args, **kwargs: pytest.fail("dry-run must not submit"))
    assert cli.main(["send", "친구", "메시지", "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target_ready"] is True
    assert result["draft_present"] is False
    assert calls == [("친구", True)]


@pytest.mark.parametrize("room,message", [(" ", "메시지"), ("친구", " \n "), ("친구", "a\x00b")])
def test_dry_run_rejects_invalid_recipient_or_content(room, message, capsys):
    assert cli.main(["send", room, message, "--dry-run", "--json"]) == 1
    assert json.loads(capsys.readouterr().err)["ok"] is False


@pytest.mark.parametrize("command", ["messages", "search"])
def test_structured_loaded_messages_command(monkeypatch, capsys, command):
    monkeypatch.setattr(cli, "read_visible", lambda *args, **kwargs: (
        make_window("개발팀", 1), "[친구] [오전 9:00] 첫 메시지\n[나] [오전 9:01] 점심?",
    ))
    arguments = [command, "개발팀", "--limit", "1", "--json"]
    arguments += ["--contains", "점심"] if command == "messages" else ["점심"]
    assert cli.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"] == "loaded_ui_only"
    assert result["complete_history"] is False
    assert result["loaded_records"] == 2
    assert result["returned_records"] == 1
    assert result["records"][0]["sender"] == "나"


def test_messages_limit_must_be_positive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["messages", "개발팀", "--limit", "0"])


def test_expected_win32_errors_use_structured_json_path(monkeypatch, capsys):
    import pywintypes

    def invalid_window(*args, **kwargs):
        raise pywintypes.error(1400, "GetWindowText", "Invalid window handle")

    monkeypatch.setattr(cli, "preview_send", invalid_window)
    assert cli.main(["send", "친구", "메시지", "--dry-run", "--json"]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result["ok"] is False
    assert "1400" in result["error"]


def test_programming_errors_are_not_hidden_as_window_errors(monkeypatch):
    def bug(*args, **kwargs):
        raise TypeError("unexpected implementation bug")

    monkeypatch.setattr(cli, "preview_send", bug)
    with pytest.raises(TypeError, match="unexpected implementation bug"):
        cli.main(["send", "친구", "메시지", "--dry-run", "--json"])
