import pytest

from kakaocli_win import background as bg
from kakaocli_win.backend import KakaoError


def runtime_environment(monkeypatch, tmp_path):
    monkeypatch.delenv('KAKAOCLI_REA_ENTRY', raising=False)
    monkeypatch.setenv('KAKAOCLI_REA_NODE', 'node')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bg, '__file__', str(tmp_path / 'project/src/kakaocli_win/background.py'))


def test_project_runtime_is_found_without_machine_specific_paths(monkeypatch, tmp_path):
    runtime_environment(monkeypatch, tmp_path)
    entry = tmp_path / 'project/work/rea/node_modules/rea-agents/scripts/rea.mjs'
    entry.parent.mkdir(parents=True)
    entry.touch()
    assert bg._runtime() == ('node', str(entry.resolve().parents[1]))


def test_explicit_runtime_wins_and_invalid_override_does_not_fallback(monkeypatch, tmp_path):
    runtime_environment(monkeypatch, tmp_path)
    local = tmp_path / 'project/work/rea/node_modules/rea-agents/scripts/rea.mjs'
    local.parent.mkdir(parents=True)
    local.touch()
    override = tmp_path / 'external/rea-agents/scripts/rea.mjs'
    override.parent.mkdir(parents=True)
    override.touch()
    monkeypatch.setenv('KAKAOCLI_REA_ENTRY', str(override))
    assert bg._runtime() == ('node', str(override.resolve().parents[1]))
    monkeypatch.setenv('KAKAOCLI_REA_ENTRY', str(tmp_path / 'missing.mjs'))
    with pytest.raises(KakaoError, match='InstallRea'):
        bg._runtime()
