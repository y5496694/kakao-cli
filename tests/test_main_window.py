import json
from types import SimpleNamespace

import pytest

from kakaocli_win import backend as b, attachments, cli


@pytest.fixture
def controls(monkeypatch):
    rows = {}
    monkeypatch.setattr(b, '_input_handles', lambda _: [])
    monkeypatch.setattr(b, 'enum_children', lambda hwnd: list(rows.get(hwnd, {})))
    def control(hwnd):
        return next(value for children in rows.values() for child, value in children.items() if child == hwnd)
    monkeypatch.setattr(b, 'win32gui', SimpleNamespace(
        GetDlgCtrlID=lambda hwnd: control(hwnd)[0],
        GetClassName=lambda hwnd: control(hwnd)[1],
        GetWindowText=lambda hwnd: control(hwnd)[2],
    ), raising=False)
    return rows


def window(hwnd, title, width, height, kind='main'):
    return b.WindowInfo(hwnd, title, b.CHAT_CLASS, width, height, True, kind, pid=123)


def test_empty_title_ad_is_not_a_main_window(controls):
    assert b.classify_window(1, '', b.CHAT_CLASS) == 'popup'


@pytest.mark.parametrize('title', ['카카오톡', 'KakaoTalk'])
def test_named_login_root_is_still_discoverable_without_online_view(controls, title):
    assert b.classify_window(1, title, b.CHAT_CLASS) == 'main'


def test_verified_online_view_identifies_untitled_main_root(controls):
    controls[1] = {2: (132, 'EVA_ChildWindow', 'OnlineMainView_1')}
    assert b.classify_window(1, '', b.CHAT_CLASS) == 'main'


@pytest.mark.parametrize('child', [
    (133, 'EVA_ChildWindow', 'OnlineMainView_1'),
    (132, 'EVA_Window', 'OnlineMainView_1'),
    (132, 'EVA_ChildWindow', 'Advertisement_1'),
])
def test_partial_online_view_signature_does_not_identify_a_main_window(controls, child):
    controls[1] = {2: child}
    assert b.classify_window(1, '', b.CHAT_CLASS) == 'popup'


def test_chat_input_takes_precedence_over_main_like_title(controls, monkeypatch):
    monkeypatch.setattr(b, '_input_handles', lambda _: [2])
    assert b.classify_window(1, '카카오톡', b.CHAT_CLASS) == 'chat'


def test_minimized_named_main_wins_over_larger_empty_ad(controls, monkeypatch):
    main, ad = window(1, '카카오톡', 199, 34), window(2, '', 476, 113)
    monkeypatch.setattr(b, 'list_windows', lambda: [ad, main])
    assert b.find_main_window() is main


def test_online_main_wins_over_larger_login_root(controls, monkeypatch):
    main, login = window(1, '', 199, 34), window(3, '카카오톡', 500, 800)
    controls[1] = {2: (132, 'EVA_ChildWindow', 'OnlineMainView_1')}
    monkeypatch.setattr(b, 'list_windows', lambda: [login, main])
    assert b.find_main_window() is main


def test_only_unnamed_advertising_roots_do_not_produce_main(controls, monkeypatch):
    monkeypatch.setattr(b, 'list_windows', lambda: [window(1, '', 476, 113)])
    assert b.find_main_window() is None


def test_capture_main_uses_rendering_helper(monkeypatch, tmp_path, capsys):
    main = window(1, '카카오톡', 199, 34)
    monkeypatch.setattr(b, 'find_main_window', lambda: main)
    calls = []
    monkeypatch.setattr(attachments, '_capture_rendered', lambda hwnd, path: calls.append((hwnd, path)))
    destination = tmp_path/'main.png'
    assert cli.main(['capture-main', '--output', str(destination), '--json']) == 0
    assert calls == [(main.hwnd, destination.resolve())]
    assert json.loads(capsys.readouterr().out)['foreground_unchanged'] is True


def test_capture_main_preserves_existing_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(b, 'find_main_window', lambda: window(1, '카카오톡', 199, 34))
    monkeypatch.setattr(attachments, '_capture_rendered', lambda *args: pytest.fail('must preserve existing output'))
    destination = tmp_path/'existing.png'
    destination.write_bytes(b'original')
    assert cli.main(['capture-main', '--output', str(destination), '--json']) == 1
    assert destination.read_bytes() == b'original'
