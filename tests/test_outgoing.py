import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from kakaocli_win import cli, outgoing, windows_ocr
from kakaocli_win.backend import KakaoError


def test_image_validation_rejects_text_disguised_as_png(tmp_path):
    path = tmp_path / 'photo.png'
    path.write_text('not an image', encoding='utf-8')
    with pytest.raises(KakaoError, match='이미지'):
        outgoing.attachment_source(path, 'image')


@pytest.mark.parametrize('exists', [False, True])
def test_missing_or_empty_source_is_rejected(tmp_path, exists):
    path = tmp_path / 'empty.txt'
    if exists:
        path.touch()
    with pytest.raises(KakaoError, match='로컬 파일'):
        outgoing.attachment_source(path, 'file')


def test_animated_image_requires_original_file_command(tmp_path):
    path = tmp_path / 'animated.gif'
    Image.new('RGB', (2, 2), 'white').save(path, save_all=True,
        append_images=[Image.new('RGB', (2, 2), 'black')], duration=100)
    with pytest.raises(KakaoError, match='send-file'):
        outgoing.attachment_source(path, 'image')


@pytest.mark.parametrize('command', ['send-image', 'send-file'])
def test_dry_run_never_changes_clipboard_or_submits(tmp_path, monkeypatch, capsys, command):
    path = tmp_path / 'image.png'
    Image.new('RGB', (2, 2), 'white').save(path)
    monkeypatch.setattr(outgoing, '_target', lambda room: SimpleNamespace(title=room))
    monkeypatch.setattr(outgoing, '_image_clipboard', lambda *a: pytest.fail('dry-run changed clipboard'))
    monkeypatch.setattr(outgoing, 'send_attachment', lambda *a, **k: pytest.fail('dry-run submitted'))
    assert cli.main([command, 'Example', str(path), '--dry-run', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['submitted'] is False
    assert result['attachment']['sha256']


def test_attachment_requires_yes_before_any_staging(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'file.txt'
    path.write_text('example', encoding='utf-8')
    monkeypatch.setattr(outgoing, '_target', lambda room: SimpleNamespace(title=room))
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO())
    monkeypatch.setattr(outgoing, 'send_attachment', lambda *a, **k: pytest.fail('submitted without approval'))
    assert cli.main(['send-file', 'Example', str(path), '--json']) == 1
    assert '--yes' in json.loads(capsys.readouterr().err)['error']


@pytest.fixture
def preview_environment(tmp_path, monkeypatch):
    b = outgoing.b
    monkeypatch.setattr(b, '_assert_process_target', lambda *a, **k: None)
    monkeypatch.setattr(b.win32gui, 'GetWindow', lambda *a: 2)
    monkeypatch.setattr(b.win32gui, 'GetWindowRect', lambda *a: (-32000, -32000, -31700, -31815))
    monkeypatch.setattr(b.win32gui, 'GetForegroundWindow', lambda: 99)
    monkeypatch.setattr(b.win32gui, 'ShowWindow', lambda *a: None)
    monkeypatch.setattr(b.win32gui, 'RedrawWindow', lambda *a: None)
    monkeypatch.setattr(outgoing.time, 'sleep', lambda *a: None)
    def capture(hwnd, path):
        image = Image.new('RGB', (300, 185), 'white')
        image.putpixel((1, 1), (0, 0, 0))
        image.save(path)
        return [-32000, -32000, -31700, -31815]
    monkeypatch.setattr(outgoing, '_capture', capture)
    return {'preview': '0x1', 'room_hwnd': '0x2', 'pid': 123, 'kind': 'file',
            'source_path': str(tmp_path / 'example.txt'), 'preview_directory': str(tmp_path)}


def test_wrong_popup_owner_is_rejected_before_capture(preview_environment, monkeypatch):
    monkeypatch.setattr(outgoing.b.win32gui, 'GetWindow', lambda *a: 3)
    monkeypatch.setattr(outgoing, '_capture', lambda *a: pytest.fail('captured wrong owner'))
    with pytest.raises(KakaoError, match='소유'):
        outgoing.inspect_preview(preview_environment)


def test_changed_source_is_rejected_before_preview(preview_environment, monkeypatch):
    preview_environment['source_sha256'] = 'old'
    monkeypatch.setattr(outgoing, '_sha256', lambda *a: 'new')
    monkeypatch.setattr(outgoing, '_capture', lambda *a: pytest.fail('captured changed source'))
    with pytest.raises(KakaoError, match='바뀌었습니다'):
        outgoing.inspect_preview(preview_environment)


def test_wrong_filename_cannot_reach_send_button(preview_environment, monkeypatch):
    monkeypatch.setattr(windows_ocr, 'ocr_image', lambda *a: {'text': '파일 전송 other.txt', 'lines': []})
    with pytest.raises(KakaoError, match='파일 이름'):
        outgoing.inspect_preview(preview_environment)


def test_multiple_send_buttons_are_rejected(preview_environment, monkeypatch):
    line = {'text': '전송', 'words': [{'x': 100, 'y': 100, 'width': 80, 'height': 40}]}
    results = iter([{'text': '파일 전송 example.txt', 'lines': []}, {'lines': [line, line]}])
    monkeypatch.setattr(windows_ocr, 'ocr_image', lambda *a: next(results))
    with pytest.raises(KakaoError, match='하나로'):
        outgoing.inspect_preview(preview_environment)


def test_verified_button_maps_back_to_original_popup(preview_environment, monkeypatch):
    results = iter([{'text': '파일 전송 example.txt', 'lines': []}, {'lines': [
        {'text': '1개 전송', 'words': [{'x': 400, 'y': 80, 'width': 160, 'height': 40}]}]}])
    monkeypatch.setattr(windows_ocr, 'ocr_image', lambda *a: next(results))
    result = outgoing.inspect_preview(preview_environment)
    assert result['verified'] is True
    assert result['point'] == [120, 145]
    assert (result['width'], result['height']) == (300, 185)
