from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from kakaocli_win import attachments, outgoing
from kakaocli_win.backend import KakaoError


@pytest.mark.parametrize('dpi,size,expected', [
    (96, (380, 640), [88, 615]),
    (120, (475, 800), [110, 769]),
])
def test_file_button_uses_target_coordinates_not_caller_geometry(monkeypatch, dpi, size, expected):
    monkeypatch.setattr(attachments, '_window_dpi_context', lambda _: nullcontext())
    monkeypatch.setattr(attachments, '_native_client_size', lambda _: size)
    monkeypatch.setattr(outgoing.ctypes.windll.user32, 'GetDpiForWindow', lambda _: dpi)
    # The caller's physical geometry differs from Kakao's cached system DPI.
    window = SimpleNamespace(hwnd=10, width=380, height=640)
    assert outgoing._file_button(window) == expected


@pytest.mark.parametrize('dpi,size', [(144, (600, 900)), (120, (380, 450)), (96, (250, 400))])
def test_file_button_rejects_unverified_scale_or_layout(monkeypatch, dpi, size):
    monkeypatch.setattr(attachments, '_window_dpi_context', lambda _: nullcontext())
    monkeypatch.setattr(attachments, '_native_client_size', lambda _: size)
    monkeypatch.setattr(outgoing.ctypes.windll.user32, 'GetDpiForWindow', lambda _: dpi)
    with pytest.raises(KakaoError, match='배율'):
        outgoing._file_button(SimpleNamespace(hwnd=10))
