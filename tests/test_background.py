import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kakaocli_win import backend as b, background as bg


@pytest.fixture
def rendered_search(monkeypatch):
    from kakaocli_win import attachments
    monkeypatch.setattr(b, 'find_main_window', lambda: SimpleNamespace(hwnd=1))
    monkeypatch.setattr(attachments, '_rendered_window', lambda hwnd: nullcontext())


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


def test_search_ambiguity_never_opens_row(monkeypatch, rendered_search):
    from kakaocli_win import attachments, windows_ocr
    from PIL import Image
    monkeypatch.setattr(attachments, '_capture_rendered', lambda hwnd, path: Image.new('RGB', (400, 600)).save(path))
    line = lambda text, y: {'words': [{'text': text, 'x': 100, 'y': y, 'width': 100, 'height': 15}]}
    monkeypatch.setattr(windows_ocr, 'ocr_image', lambda _: {'width': 400, 'height': 600, 'lines': [line('Example A', 100), line('Example B', 160)]})
    calls = []
    def action(room, op, **kwargs):
        calls.append((op, kwargs))
        return {'main': '0x1', 'previous_search': 'old'}
    monkeypatch.setattr(bg, 'run_action', action)
    with pytest.raises(b.KakaoError, match='후보'):
        bg.resolve_room('Example')
    assert [op for op, _ in calls] == ['search-probe', 'search-probe']
    assert calls[-1][1]['query'] == 'old'


def test_search_uses_doubled_ocr_and_excludes_search_box(monkeypatch, rendered_search):
    from kakaocli_win import attachments, windows_ocr
    from PIL import Image
    monkeypatch.setattr(attachments, '_capture_rendered', lambda hwnd, path: Image.new('RGB', (400, 600)).save(path))
    def line(text, x, y):
        return {'words': [{'text': text, 'x': x, 'y': y, 'width': 100, 'height': 30}]}
    def ocr(path):
        with Image.open(path) as picture:
            assert picture.size == (800, 1200)
        return {'width': 800, 'height': 1200,
                'lines': [line('Example', 200, 120), line('Example', 200, 400)]}
    monkeypatch.setattr(windows_ocr, 'ocr_image', ocr)
    calls = []
    def action(room, operation, **kwargs):
        calls.append((operation, kwargs))
        if operation == 'search-probe':
            return {'main': '0x1', 'previous_search': '',
                    'result_bounds': [80, 150, 380, 550], 'render_size': [400, 600]}
        return {'title': kwargs['expected_title'], 'hwnd': '0x2'}
    monkeypatch.setattr(bg, 'run_action', action)
    assert bg.resolve_room('Example') == {'title': 'Example', 'hwnd': '0x2'}
    selected = next(kwargs for operation, kwargs in calls if operation == 'open-selected')
    assert selected['point'] == [250, 415]
    assert selected['capture_size'] == [800, 1200]


def test_search_box_match_never_opens_a_recipient(monkeypatch, rendered_search):
    from kakaocli_win import attachments, windows_ocr
    from PIL import Image
    monkeypatch.setattr(attachments, '_capture_rendered', lambda hwnd, path: Image.new('RGB', (400, 600)).save(path))
    monkeypatch.setattr(windows_ocr, 'ocr_image', lambda _: {
        'width': 800, 'height': 1200,
        'lines': [{'words': [{'text': 'Example', 'x': 200, 'y': 120, 'width': 100, 'height': 30}]}],
    })
    calls = []
    def action(room, operation, **kwargs):
        calls.append(operation)
        return {'main': '0x1', 'previous_search': '',
                'result_bounds': [80, 150, 380, 550], 'render_size': [400, 600]}
    monkeypatch.setattr(bg, 'run_action', action)
    with pytest.raises(b.KakaoError, match='후보'):
        bg.resolve_room('Example')
    assert calls == ['search-probe', 'search-probe']
