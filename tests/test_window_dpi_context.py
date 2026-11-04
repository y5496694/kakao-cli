import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from kakaocli_win import attachments as a, backend as b


class NativeFunction:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


@pytest.fixture
def dpi_api(monkeypatch):
    # High bits exercise HANDLE-sized values on the 64-bit Python host.
    state = {'current': 0x1234567800000001 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12340001,
             'target': ctypes.c_void_p(-4).value,
             'calls': []}
    def set_context(value):
        state['calls'].append(value)
        previous, state['current'] = state['current'], value
        return previous
    library = SimpleNamespace(
        SetThreadDpiAwarenessContext=NativeFunction(set_context),
    )
    monkeypatch.setattr(a.ctypes, 'WinDLL', lambda name, use_last_error: library)
    return state, library


def test_window_dpi_context_uses_pointer_sized_handles_and_restores(dpi_api):
    state, library = dpi_api
    original = state['current']
    with a._window_dpi_context(123):
        assert state['current'] == state['target']
    assert state['current'] == original
    assert state['calls'] == [state['target'], original]
    assert library.SetThreadDpiAwarenessContext.argtypes == [wintypes.HANDLE]
    assert library.SetThreadDpiAwarenessContext.restype is wintypes.HANDLE


def test_nested_contexts_restore_original_context_after_exception(dpi_api):
    state, _ = dpi_api
    original = state['current']
    with pytest.raises(ValueError, match='operation failed'):
        with a._window_dpi_context(123):
            with a._window_dpi_context(123):
                assert state['current'] == state['target']
                raise ValueError('operation failed')
    assert state['current'] == original
    assert state['calls'] == [state['target'], state['target'], state['target'], original]


def test_failed_thread_dpi_context_does_not_enter_operation(dpi_api):
    state, library = dpi_api
    library.SetThreadDpiAwarenessContext.implementation = lambda context: None
    with pytest.raises(b.KakaoError, match='변경하지 못했습니다'):
        with a._window_dpi_context(123):
            pytest.fail('must not enter without a target context')
    assert state['calls'] == []


def native_conversion(state, library, monkeypatch):
    calls = []
    def convert(hwnd, pointer):
        assert state['current'] == state['target']
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        calls.append((hwnd, point.x, point.y))
        point.x, point.y = round(point.x*1.25), round(point.y*1.25)
        return True
    library.PhysicalToLogicalPointForPerMonitorDPI = NativeFunction(convert)
    return calls


def test_cross_process_window_rect_uses_target_point_transform(dpi_api, monkeypatch):
    state, library = dpi_api
    original = state['current']
    calls = native_conversion(state, library, monkeypatch)
    monkeypatch.setattr(b.win32gui, 'GetWindowRect', lambda hwnd: (-25600, -25600, -25220, -24960))
    assert a._native_window_rect(123) == (-32000, -32000, -31525, -31200)
    assert calls == [(123, -25600, -25600), (123, -25220, -24960)]
    assert state['current'] == original
    convert = library.PhysicalToLogicalPointForPerMonitorDPI
    assert convert.argtypes == [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    assert convert.restype is wintypes.BOOL


def test_cross_process_client_size_converts_screen_corners(dpi_api, monkeypatch):
    state, library = dpi_api
    original = state['current']
    calls = native_conversion(state, library, monkeypatch)
    monkeypatch.setattr(b.win32gui, 'GetClientRect', lambda hwnd: (0, 0, 380, 640))
    monkeypatch.setattr(b.win32gui, 'ClientToScreen', lambda hwnd, point: (-25600+point[0], -25600+point[1]))
    assert a._native_client_size(123) == (475, 800)
    assert calls == [(123, -25600, -25600), (123, -25220, -24960)]
    assert state['current'] == original


def test_point_conversion_failure_restores_thread_context(dpi_api, monkeypatch):
    state, library = dpi_api
    original = state['current']
    library.PhysicalToLogicalPointForPerMonitorDPI = NativeFunction(lambda *args: False)
    monkeypatch.setattr(b.win32gui, 'GetWindowRect', lambda hwnd: (0, 0, 380, 640))
    with pytest.raises(b.KakaoError, match='좌표 변환'):
        a._native_window_rect(123)
    assert state['current'] == original


def test_dpi_restoration_failure_is_reported(dpi_api):
    state, library = dpi_api
    with pytest.raises(b.KakaoError, match='복원하지 못했습니다'):
        with a._window_dpi_context(123):
            library.SetThreadDpiAwarenessContext.implementation = lambda value: None


def test_capture_queries_geometry_with_target_dpi_context(dpi_api, monkeypatch, tmp_path):
    state, _ = dpi_api
    original = state['current']
    def capture(hwnd, destination):
        assert state['current'] == state['target']
        return [0, 0, 475, 800]
    monkeypatch.setattr(a, '_capture_in_context', capture)
    assert a._capture(123, tmp_path/'capture.png') == [0, 0, 475, 800]
    assert state['current'] == original


def test_rendered_window_keeps_target_dpi_context_for_entire_lifetime(dpi_api, monkeypatch):
    state, _ = dpi_api
    original = state['current']
    events = []
    @contextmanager
    def rendered(hwnd):
        assert state['current'] == state['target']
        events.append('render')
        try:
            yield
        finally:
            assert state['current'] == state['target']
            events.append('restore-window')
    monkeypatch.setattr(a, '_rendered_window_in_context', rendered)
    with a._rendered_window(123):
        assert state['current'] == state['target']
        events.append('operation')
    assert events == ['render', 'operation', 'restore-window']
    assert state['current'] == original
