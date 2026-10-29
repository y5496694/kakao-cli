from types import SimpleNamespace

import pytest

from kakaocli_win import attachments as a, backend as b, background as bg


def test_download_verifies_original_bytes_without_dialog(monkeypatch, tmp_path):
    window = SimpleNamespace(title='Example')
    monkeypatch.setattr(a, '_room', lambda _: window)
    monkeypatch.setattr(a, '_save_dialogs', lambda _: [])
    monkeypatch.setattr(a.time, 'sleep', lambda _: None)
    def action(room, operation, **kwargs):
        assert operation == 'download'
        file = tmp_path / 'original.pdf'
        file.write_bytes(b'%PDF-example')
        return {'path': str(file), 'dialog_bypassed': True, 'foreground_unchanged': True}
    monkeypatch.setattr(bg, 'run_action', action)
    result = a.download_attachment('Example', point=(100, 100), mode='point', kind='file', output_directory=tmp_path)
    assert result['saved'] and result['save_dialog_opened'] is False
    assert result['bytes'] == 12
    assert len(result['sha256']) == 64


def test_modal_blocks_other_attachment_clicks(monkeypatch):
    monkeypatch.setattr(a, '_room', lambda _: object())
    monkeypatch.setattr(a, '_save_dialogs', lambda _: [1])
    action = lambda *args, **kwargs: pytest.fail('Must not click while modal')
    monkeypatch.setattr(bg, 'run_action', action)
    with pytest.raises(b.KakaoError, match='기존 저장 팝업'):
        a.download_attachment('Example', point=(1, 2), mode='point')


def test_download_rejects_path_outside_output_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(a, '_room', lambda _: SimpleNamespace(title='Example'))
    monkeypatch.setattr(a, '_save_dialogs', lambda _: [])
    monkeypatch.setattr(bg, 'run_action', lambda *args, **kwargs: {'path': str(tmp_path / 'escaped.pdf')})
    with pytest.raises(b.KakaoError, match='벗어났습니다'):
        a.download_attachment('Example', output_directory=tmp_path / 'output')


@pytest.mark.parametrize('filename', ['../bad.jpg', 'bad:name.jpg', '.', '..'])
def test_invalid_filename_rejected_before_runtime(monkeypatch, filename):
    monkeypatch.setattr(a, '_room', lambda _: SimpleNamespace(title='Example'))
    monkeypatch.setattr(a, '_save_dialogs', lambda _: [])
    with pytest.raises(b.KakaoError, match='이름'):
        a.download_attachment('Example', filename=filename)
