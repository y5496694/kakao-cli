from types import SimpleNamespace
from contextlib import nullcontext

import pytest

from kakaocli_win import attachments as a, backend as b


class WindowState:
    def __init__(self, *, visible=False, iconic=False):
        self.visible, self.iconic = visible, iconic
        self.foreground = 200
        self.normal = (100, 150, 600, 850)
        self.rect = (-32000, -32000, -31840, -31972) if iconic else self.normal
        self.placement = (0, 2 if iconic else 1 if visible else 0, (-1, -1), (-1, -1), self.normal)
        self.calls = []

    def GetForegroundWindow(self):
        return self.foreground

    def IsWindowVisible(self, hwnd):
        return self.visible

    def IsIconic(self, hwnd):
        return self.iconic

    def GetWindowPlacement(self, hwnd):
        return self.placement

    def GetWindowRect(self, hwnd):
        return self.rect

    def ShowWindow(self, hwnd, command):
        self.calls.append(('show', command))
        self.visible = command != b.win32con.SW_HIDE
        if command == b.win32con.SW_SHOWNOACTIVATE:
            self.iconic = False

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):
        assert flags & b.win32con.SWP_NOACTIVATE
        assert flags & b.win32con.SWP_NOZORDER
        self.calls.append(('position', x, y, width, height))
        self.rect = (x, y, x+width, y+height)

    def SetWindowPlacement(self, hwnd, placement):
        self.calls.append(('placement', placement))
        command = placement[1]
        self.iconic = command == b.win32con.SW_SHOWMINNOACTIVE
        self.visible = command != b.win32con.SW_HIDE
        self.placement = (placement[0], 2 if self.iconic else command, *placement[2:])

    def RedrawWindow(self, *args):
        self.calls.append(('redraw',))


@pytest.fixture
def window_environment(monkeypatch):
    def prepare(**kwargs):
        state = WindowState(**kwargs)
        monkeypatch.setattr(b, 'win32gui', state)
        monkeypatch.setattr(b, '_window_process', lambda _: (100, 'KakaoTalk.exe'))
        monkeypatch.setattr(a, '_window_dpi_context', lambda hwnd: nullcontext())
        monkeypatch.setattr(a.time, 'sleep', lambda _: None)
        return state
    return prepare


def test_hidden_capture_renders_offscreen_and_restores_placement(window_environment, monkeypatch, tmp_path):
    state = window_environment()
    original = state.placement, state.rect
    def capture(hwnd, destination):
        assert state.visible and not state.iconic
        assert state.rect == (-32000, -32000, -31500, -31300)
        destination.write_bytes(b'capture')
        return list(state.rect)
    monkeypatch.setattr(a, '_capture', capture)
    assert a._capture_rendered(100, tmp_path/'capture.png') == [-32000, -32000, -31500, -31300]
    assert not state.visible and not state.iconic
    assert (state.placement, state.rect) == original
    assert state.foreground == 200


def test_minimized_capture_restores_without_activation(window_environment, monkeypatch, tmp_path):
    state = window_environment(visible=True, iconic=True)
    original_placement = state.placement
    monkeypatch.setattr(a, '_capture', lambda *args: list(state.rect))
    a._capture_rendered(100, tmp_path/'capture.png')
    assert state.visible and state.iconic
    assert state.placement == original_placement
    assert any(call[0] == 'placement' and call[1][1] == b.win32con.SW_SHOWMINNOACTIVE for call in state.calls)
    assert not any(call[0] == 'show' and call[1] in (b.win32con.SW_SHOWMAXIMIZED, b.win32con.SW_RESTORE) for call in state.calls)


def test_capture_failure_restores_hidden_state_and_removes_partial_output(window_environment, monkeypatch, tmp_path):
    state = window_environment()
    original = state.placement, state.rect
    destination = tmp_path/'partial.png'
    def fail(hwnd, path):
        path.write_bytes(b'partial')
        raise ValueError('capture failed')
    monkeypatch.setattr(a, '_capture', fail)
    with pytest.raises(ValueError, match='capture failed'):
        a._capture_rendered(100, destination)
    assert not state.visible
    assert (state.placement, state.rect) == original
    assert not destination.exists()


def test_foreground_change_rejects_capture_and_restores_window(window_environment, monkeypatch, tmp_path):
    state = window_environment()
    original = state.placement, state.rect
    destination = tmp_path/'unsafe.png'
    def changed(hwnd, path):
        path.write_bytes(b'capture')
        state.foreground = 999
        return list(state.rect)
    monkeypatch.setattr(a, '_capture', changed)
    with pytest.raises(b.KakaoError, match='전경 창'):
        a._capture_rendered(100, destination)
    assert not state.visible
    assert (state.placement, state.rect) == original
    assert not destination.exists()


def test_visible_window_capture_does_not_move_or_hide_window(window_environment, monkeypatch, tmp_path):
    state = window_environment(visible=True)
    original = state.placement, state.rect
    monkeypatch.setattr(a, '_capture', lambda *args: list(state.rect))
    a._capture_rendered(100, tmp_path/'visible.png')
    assert state.visible
    assert (state.placement, state.rect) == original
    assert state.calls == [('redraw',)]


@pytest.mark.parametrize('visible, iconic, outcome', [
    (True, True, 'offscreen'),
    (True, True, 'ocr_failure'),
    (True, True, 'onscreen'),
    (False, False, 'onscreen'),
    (False, False, 'no_match'),
])
def test_search_restores_window_and_previous_query(window_environment, monkeypatch, visible, iconic, outcome):
    from PIL import Image
    from kakaocli_win import background as bg, windows_ocr
    state = window_environment(visible=visible, iconic=iconic)
    original_placement, original_rect = state.placement, state.rect
    monkeypatch.setattr(b, 'find_main_window', lambda: SimpleNamespace(hwnd=100, visible=visible))
    calls = []
    offscreen_rect = (-32000, -32000, -31500, -31300)

    def assert_rendered():
        assert state.visible and not state.iconic
        assert state.rect in (offscreen_rect, state.normal)

    def action(room, operation, **kwargs):
        assert_rendered()
        calls.append((operation, state.rect, kwargs))
        if operation == 'search-probe':
            return {'main': '0x64', 'previous_search': 'old',
                    'render_size': [500, 700], 'result_bounds': [80, 150, 480, 650]}
        return {'title': 'Example', 'hwnd': '0x65'}

    def capture(hwnd, destination):
        assert_rendered()
        Image.new('RGB', (500, 700)).save(destination)
        return list(state.rect)

    def ocr(path):
        assert_rendered()
        if outcome == 'ocr_failure':
            raise windows_ocr.WindowsOcrError('synthetic OCR failure')
        has_match = outcome == 'offscreen' or (outcome == 'onscreen' and state.rect == state.normal)
        line = {'words': [{'text': 'Example', 'x': 200, 'y': 400, 'width': 200, 'height': 30}]}
        return {'width': 1000, 'height': 1400, 'lines': [line] if has_match else []}

    monkeypatch.setattr(bg, 'run_action', action)
    monkeypatch.setattr(a, '_capture', capture)
    monkeypatch.setattr(windows_ocr, 'ocr_image', ocr)
    if outcome == 'ocr_failure':
        with pytest.raises(b.KakaoError, match='synthetic OCR failure'):
            bg.resolve_room('Example')
    elif outcome == 'no_match':
        with pytest.raises(b.KakaoError, match='후보'):
            bg.resolve_room('Example')
    else:
        assert bg.resolve_room('Example') == {'title': 'Example', 'hwnd': '0x65'}
    assert (state.visible, state.iconic) == (visible, iconic)
    assert state.placement == original_placement
    if not iconic:
        assert state.rect == original_rect
    assert state.foreground == 200
    attempts = [offscreen_rect, state.normal] if outcome in ('onscreen', 'no_match') else [offscreen_rect]
    assert [(rect, kwargs['query']) for op, rect, kwargs in calls if op == 'search-probe'] == [
        (rect, query) for rect in attempts for query in ('Example', 'old')
    ]
    opened = [(rect, kwargs) for op, rect, kwargs in calls if op == 'open-selected']
    if outcome in ('offscreen', 'onscreen'):
        [(rect, selected)] = opened
        assert rect == attempts[-1]
        assert selected['capture_size'] == [1000, 1400]
        assert calls[-2][0] == 'open-selected'
    else:
        assert not opened
