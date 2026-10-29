import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest


MACOS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kakao_macos_cli", MACOS / "kakao.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")


def test_subprocess_uses_structured_stdin_not_script_interpolation(monkeypatch, darwin):
    observed = []
    def run(argv, **kwargs):
        observed.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"status":"preview"}', "")
    monkeypatch.setattr(cli.subprocess, "run", run)
    message = "한글\n\"$(touch /tmp/unwanted)\""
    cli.call_adapter({"operation": "send", "room": "홍길동", "message": message})
    argv, kwargs = observed[0]
    assert argv == ["/usr/bin/osascript", "-l", "JavaScript", str(MACOS / "accessibility.js")]
    assert message not in " ".join(argv)
    assert json.loads(kwargs["input"])["message"] == message
    assert "shell" not in kwargs


@pytest.mark.parametrize("stdout", ["", "not json", "[]"])
def test_invalid_transport_output_is_an_error(monkeypatch, darwin, stdout):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout, ""))
    with pytest.raises(cli.KakaoMacError) as caught:
        cli.call_adapter({"operation": "send", "yes": True})
    assert caught.value.result["error"]["code"] == "invalid_adapter_output"
    assert caught.value.result["side_effect_uncertain"] is True


def test_write_timeout_does_not_claim_safe_retry(monkeypatch, darwin):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 45)
    monkeypatch.setattr(cli.subprocess, "run", timeout)
    with pytest.raises(cli.KakaoMacError) as caught:
        cli.call_adapter({"operation": "download", "yes": True})
    assert caught.value.result["error"]["code"] == "adapter_timeout"
    assert caught.value.result["side_effect_uncertain"] is True


def test_adapter_error_preserves_modal_details(monkeypatch, darwin):
    payload = {"status": "error", "error": {"code": "pending_modal", "message": "Resolve Save"}, "pending_modals": [{"parent": "홍길동"}]}
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(payload), ""))
    with pytest.raises(cli.KakaoMacError) as caught:
        cli.call_adapter({"operation": "snapshot"})
    assert caught.value.result["pending_modals"] == payload["pending_modals"]


def test_send_defaults_to_preview_and_dry_run_overrides_yes(monkeypatch):
    requests = []
    monkeypatch.setattr(cli, "call_adapter", lambda request: requests.append(request) or {"status": "preview"})
    cli.execute(cli.build_parser().parse_args(["send", "홍길동", "안녕"]))
    cli.execute(cli.build_parser().parse_args(["send", "홍길동", "안녕", "--yes", "--dry-run"]))
    assert all(request["yes"] is False for request in requests)


def test_accessible_records_do_not_include_composer_or_duplicate_container():
    records, truncated = cli.accessible_records([
        {"role": "AXTextArea", "value": "읽는 대화", "editable": False, "path": [0]},
        {"role": "AXStaticText", "value": "읽는 대화", "path": [0, 0]},
        {"role": "AXTextArea", "value": "비밀 초안", "editable": True, "path": [1]},
        {"role": "AXImage", "description": "사진 1장", "path": [2]},
    ])
    assert [record["text"] for record in records] == ["읽는 대화", "사진 1장"]
    assert records[1]["kind"] == "image_marker"
    assert truncated is False


def test_record_text_is_bounded():
    records, truncated = cli.accessible_records([{"role": "AXStaticText", "value": "abcdef", "path": [0]}], max_chars=3)
    assert records[0]["text"] == "abc"
    assert truncated is True


def test_mac_analyze_reuses_shared_extractor_without_windows_backend(tmp_path):
    source = tmp_path / "received.txt"
    source.write_text("받은 파일 내용", encoding="utf-8")
    result = cli.execute(cli.build_parser().parse_args(["analyze", str(source)]))
    assert result["text"] == "받은 파일 내용"
    assert result["status"] == "ok"


def test_non_macos_fails_without_running_osascript(monkeypatch, capsys):
    monkeypatch.setattr(cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must not invoke osascript"))
    assert cli.main(["doctor"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "macos_required"


def test_source_has_no_foreground_or_global_input_calls():
    source = (MACOS / "accessibility.js").read_text(encoding="utf-8")
    for forbidden in (".activate(", ".frontmost", ".keystroke(", ".keyCode(", ".click(", "AXRaise", "doShellScript"):
        assert forbidden not in source


def ax(request, windows, running=True):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for mocked JXA execution")
    completed = subprocess.run(
        [node, str(MACOS / "tests" / "ax_fixture.js")],
        input=json.dumps({"source": str(MACOS / "accessibility.js"), "request": request, "windows": windows, "running": running}),
        text=True, encoding="utf-8", capture_output=True, timeout=10, check=True,
    )
    return json.loads(completed.stdout)


def room(draft="", *, buttons=1, sheets=None):
    return {"title": "홍길동", "sheets": sheets or [], "children": [
        {"role": "AXStaticText", "value": "사진 보냈어요"},
        {"role": "AXTextArea", "identifier": "messageInput", "editable": True, "value": draft},
    ] + [{"role": "AXButton", "title": "보내기"} for _ in range(buttons)]}


def save_sheet(*, path_field=False):
    fields = [{"role": "AXTextField", "identifier": "saveAsNameTextField", "editable": True, "value": "original.jpg"}]
    if path_field:
        fields = [{"role": "AXTextField", "identifier": "fullPath", "editable": True, "value": "/tmp/original.jpg"}]
    return {"role": "AXSheet", "title": "Save", "children": fields + [{"role": "AXButton", "title": "Save"}]}


def test_jxa_send_preview_does_not_write():
    result = ax({"operation": "send", "room": "홍길동", "message": "답장", "yes": False}, [room()])
    assert result["result"]["status"] == "preview"
    assert result["events"] == []


def test_jxa_explicit_send_sets_only_composer_and_presses_send():
    result = ax({"operation": "send", "room": "홍길동", "message": "답장", "yes": True}, [room()])
    assert result["result"]["status"] == "submitted_unconfirmed"
    assert result["result"]["delivery_confirmed"] is False
    assert result["events"] == [{"operation": "set_value", "identifier": "messageInput", "value": "답장"}, {"operation": "press", "title": "보내기"}]


@pytest.mark.parametrize("draft", ["작성하던 초안", "  "])
def test_jxa_existing_draft_including_whitespace_is_preserved(draft):
    result = ax({"operation": "send", "room": "홍길동", "message": "답장", "yes": True}, [room(draft)])
    assert result["error"]["code"] == "existing_draft"
    assert result["events"] == []


@pytest.mark.parametrize("buttons", [0, 2])
def test_jxa_missing_or_ambiguous_send_button_blocks_writes(buttons):
    result = ax({"operation": "send", "room": "홍길동", "message": "답장", "yes": True}, [room(buttons=buttons)])
    assert result["error"]["code"] == "send_button_not_unique"
    assert result["events"] == []


def test_jxa_missing_axpress_blocks_before_typing():
    window = room()
    window["children"][-1]["press"] = False
    result = ax({"operation": "send", "room": "홍길동", "message": "답장", "yes": True}, [window])
    assert result["error"]["code"] == "axpress_unavailable"
    assert result["events"] == []


def test_jxa_duplicate_exact_window_is_rejected():
    result = ax({"operation": "snapshot", "room": "홍길동"}, [room(), room()])
    assert result["error"]["code"] == "room_not_unique"


@pytest.mark.parametrize("operation", ["snapshot", "send"])
def test_jxa_pending_save_sheet_blocks_other_ui(operation):
    result = ax({"operation": operation, "room": "홍길동", "message": "답장", "yes": True}, [room(sheets=[save_sheet()])])
    assert result["error"]["code"] == "pending_modal"
    assert result["events"] == []


def test_jxa_save_filename_uses_only_owned_sheet():
    result = ax({"operation": "download", "room": "홍길동", "filename": "photo.jpg", "yes": True}, [room(sheets=[save_sheet()])])
    assert result["result"]["status"] == "save_requested_unconfirmed"
    assert result["result"]["directory_verified"] is False
    assert result["events"] == [{"operation": "set_value", "identifier": "saveAsNameTextField", "value": "photo.jpg"}, {"operation": "press", "title": "Save"}]


def test_jxa_save_full_path_requires_a_path_control():
    result = ax({"operation": "download", "room": "홍길동", "output": "/tmp/photo.jpg", "yes": True}, [room(sheets=[save_sheet()])])
    assert result["error"]["code"] == "path_control_unavailable"
    assert result["events"] == []


def test_jxa_save_full_path_writes_the_exposed_path_control():
    result = ax({"operation": "download", "room": "홍길동", "output": "/tmp/photo.jpg", "yes": True}, [room(sheets=[save_sheet(path_field=True)])])
    assert result["result"]["save_requested"] is True
    assert result["events"][0]["value"] == "/tmp/photo.jpg"


def test_jxa_no_save_sheet_never_launches_one():
    result = ax({"operation": "download", "room": "홍길동", "filename": "photo.jpg", "yes": True}, [room()])
    assert result["error"]["code"] == "save_dialog_not_unique"
    assert result["events"] == []


def test_jxa_wrong_room_sheet_is_rejected():
    result = ax({"operation": "download", "room": "다른 방", "filename": "photo.jpg", "yes": True}, [room(sheets=[save_sheet()])])
    assert result["error"]["code"] == "sheet_room_mismatch"
    assert result["events"] == []


def test_jxa_doctor_reports_unverified_macos_and_no_running_process():
    result = ax({"operation": "doctor"}, [], running=False)
    assert result["result"]["running"] is False
    assert result["result"]["live_macos_verified"] is False
