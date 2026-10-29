import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kakaocli_win import backend as b, background as bg


def test_runtime_keeps_message_out_of_command_arguments(monkeypatch):
    window = SimpleNamespace(pid=123, hwnd=456, title='Example', kind='chat')
    monkeypatch.setattr(b, 'require_windows', lambda: None)
    monkeypatch.setattr(bg, '_any_room', lambda _: window)
    monkeypatch.setattr(b, 'find_main_window', lambda: None)
    monkeypatch.setattr(b, 'list_windows', lambda: [window])
    monkeypatch.setattr(bg, '_runtime', lambda: ('node', 'rea'))
    process = Mock(return_value=SimpleNamespace(stdout=json.dumps({'ok': True, 'result': {'submitted': True}})))
    monkeypatch.setattr(bg.subprocess, 'run', process)
    bg.run_action('Example', 'send', message='private body')
    positional, options = process.call_args
    assert all('private body' not in arg for arg in positional[0])
    assert json.loads(options['input'])['message'] == 'private body'


def test_canonical_search_result_is_verified(monkeypatch):
    window = SimpleNamespace(hwnd=100, title='School Example')
    monkeypatch.setattr(bg, '_any_room', lambda title: window if title == window.title else None)
    monkeypatch.setattr(bg, 'resolve_room', lambda _: {'hwnd': '0x64', 'title': window.title})
    assert bg.ensure_room('Example') is window
    monkeypatch.setattr(bg, 'resolve_room', lambda _: {'hwnd': '0x65', 'title': window.title})
    with pytest.raises(b.KakaoError):
        bg.ensure_room('Example')


def test_search_ambiguity_never_opens_row(monkeypatch):
    from kakaocli_win import attachments, windows_ocr
    monkeypatch.setattr(attachments, '_capture', lambda *a: None)
    line = lambda text, y: {'words': [{'text': text, 'x': 100, 'y': y, 'width': 100, 'height': 15}]}
    monkeypatch.setattr(windows_ocr, 'ocr_image', lambda _: {'width': 400, 'lines': [line('Example A', 100), line('Example B', 160)]})
    calls = []
    def action(room, op, **kwargs):
        calls.append((op, kwargs))
        return {'main': '0x1', 'previous_search': 'old'}
    monkeypatch.setattr(bg, 'run_action', action)
    with pytest.raises(b.KakaoError, match='후보'):
        bg.resolve_room('Example')
    assert [op for op, _ in calls] == ['search-probe', 'search-probe']
    assert calls[-1][1]['query'] == 'old'
