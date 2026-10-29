import json
import sys
import types

import pytest

from kakaocli_win import cli


@pytest.fixture
def routes(monkeypatch):
    calls = []
    acquisition = types.ModuleType("kakaocli_win.attachments")
    analysis = types.ModuleType("kakaocli_win.attachment_analysis")

    def route(name):
        def run(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {"operation": name, "background": True}
        return run

    for name in ("list_attachments", "capture_room", "download_attachment"):
        setattr(acquisition, name, route(name))
    analysis.analyze_attachment = route("analyze_attachment")
    monkeypatch.setitem(sys.modules, acquisition.__name__, acquisition)
    monkeypatch.setitem(sys.modules, analysis.__name__, analysis)
    return calls


def test_attachments_default_background_route(routes, capsys):
    assert cli.main(["attachments", "테스트 방", "--limit", "7", "--json"]) == 0
    assert routes == [("list_attachments", ("테스트 방",), {"limit": 7, "interactive": False})]
    assert json.loads(capsys.readouterr().out)["background"] is True


def test_capture_passes_scroll_without_foreground_optin(routes, capsys):
    assert cli.main(["capture", "테스트 방", "--output", "capture.png", "--scroll", "up", "--json"]) == 0
    assert routes == [("capture_room", ("테스트 방",), {"output": "capture.png", "scroll": "up"})]


@pytest.mark.parametrize("mode_args,mode,point", [
    ([], "viewer", None), (["--viewer"], "viewer", None),
    (["--dialog"], "dialog", None), (["--at", "123", "456"], "point", (123, 456)),
])
def test_download_modes_are_explicit_and_background_by_default(routes, mode_args, mode, point):
    assert cli.main(["download", "테스트 방", *mode_args, "--output-dir", "D:/out", "--filename", "한글.jpg", "--timeout", "9.5", "--json"]) == 0
    assert routes == [("download_attachment", ("테스트 방",), {
        "point": point, "mode": mode, "kind": "image", "output_directory": "D:/out",
        "filename": "한글.jpg", "timeout": 9.5, "interactive": False,
    })]


def test_download_interactive_requires_explicit_option(routes):
    assert cli.main(["download", "테스트 방", "--dialog", "--kind", "file", "--interactive", "--json"]) == 0
    assert routes[0][2]["interactive"] is True
    assert routes[0][2]["kind"] == "file"


def test_analyze_routes_local_file_and_limits(routes):
    assert cli.main(["--json", "analyze", "data.pdf", "--output-dir", "analysis", "--max-chars", "1000"]) == 0
    assert routes == [("analyze_attachment", ("data.pdf",), {"output_directory": "analysis", "max_chars": 1000})]


@pytest.mark.parametrize("args", [
    ["download", "방", "--viewer", "--dialog"],
    ["download", "방", "--at", "1", "2", "--viewer"],
    ["download", "방", "--timeout", "0"],
    ["download", "방", "--timeout", "nan"],
    ["download", "방", "--timeout", "inf"],
    ["attachments", "방", "--limit", "0"],
    ["analyze", "data.txt", "--max-chars", "0"],
    ["analyze", "data.txt", "--max-chars", "1000001"],
])
def test_invalid_acquisition_arguments_do_not_execute(routes, args):
    with pytest.raises(SystemExit):
        cli.main(args)
    assert routes == []


@pytest.mark.parametrize("args", [
    ["attachments", " "], ["capture", " ", "--output", "a.png"], ["download", " "],
])
def test_empty_attachment_target_is_rejected_before_routing(routes, capsys, args):
    assert cli.main([*args, "--json"]) == 1
    assert json.loads(capsys.readouterr().err)["ok"] is False
    assert routes == []
