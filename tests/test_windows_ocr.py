import json
import subprocess

import pytest

from kakaocli_win import windows_ocr


def sample():
    return {"text": "가나다학교 홍길동 선생님", "language": "ko-KR", "lines": [
        {"text": "가나다학교 홍길동 선생님", "words": [
            {"text": "홍길동", "x": 42.5, "y": 81, "width": 50, "height": 18},
        ]},
    ]}


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_ocr.sys, "platform", "win32")
    if not hasattr(subprocess, "STARTUPINFO"):
        class StartupInfo:
            dwFlags = 0
            wShowWindow = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", StartupInfo, raising=False)
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
        monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    path = tmp_path / "한글 이름 'quoted' $(ignored).png"
    path.write_bytes(b"fixture image data; subprocess is mocked")
    return path


def test_ocr_uses_hidden_powershell_and_separate_literal_path_argument(source, monkeypatch):
    seen = []
    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(sample(), ensure_ascii=False), "")
    monkeypatch.setattr(windows_ocr.subprocess, "run", run)
    result = windows_ocr.ocr_image(source)
    assert result == sample()
    argv, kwargs = seen[0]
    assert argv[-2:] == ["-ImagePath", str(source.resolve())]
    assert "-File" in argv and "-Command" not in argv
    assert "shell" not in kwargs
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert kwargs["encoding"] == "utf-8-sig"


def test_ocr_timeout_is_reported_without_retry(source, monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args[0], 40)
    monkeypatch.setattr(windows_ocr.subprocess, "run", timeout)
    with pytest.raises(windows_ocr.WindowsOcrError) as caught:
        windows_ocr.ocr_image(source)
    assert caught.value.code == "ocr_timeout"
    assert len(calls) == 1


def test_recognizer_error_is_propagated_as_structured_error(source, monkeypatch):
    payload = {"error": {"code": "ocr_failed", "message": "No installed recognizer"}}
    monkeypatch.setattr(windows_ocr.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, json.dumps(payload), ""))
    with pytest.raises(windows_ocr.WindowsOcrError, match="No installed recognizer") as caught:
        windows_ocr.ocr_image(source)
    assert caught.value.code == "ocr_failed"


@pytest.mark.parametrize("payload", [None, [], {}, {"text": "", "language": "ko-KR", "lines": None}])
def test_malformed_result_is_rejected(payload):
    with pytest.raises(windows_ocr.WindowsOcrError) as caught:
        windows_ocr._validate_result(payload)
    assert caught.value.code == "invalid_ocr_output"


@pytest.mark.parametrize("key,value", [("x", float("nan")), ("height", -1), ("width", "50"), ("y", True)])
def test_invalid_word_rectangles_are_rejected(key, value):
    payload = sample()
    payload["lines"][0]["words"][0][key] = value
    with pytest.raises(windows_ocr.WindowsOcrError):
        windows_ocr._validate_result(payload)


def test_non_windows_fails_before_subprocess(monkeypatch):
    monkeypatch.setattr(windows_ocr.sys, "platform", "darwin")
    monkeypatch.setattr(windows_ocr.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected subprocess"))
    with pytest.raises(windows_ocr.WindowsOcrError) as caught:
        windows_ocr.ocr_image("not-used.png")
    assert caught.value.code == "windows_required"


def test_missing_image_fails_before_subprocess(source, monkeypatch):
    monkeypatch.setattr(windows_ocr.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected subprocess"))
    with pytest.raises(windows_ocr.WindowsOcrError) as caught:
        windows_ocr.ocr_image(source.with_name("missing.png"))
    assert caught.value.code == "image_missing"
