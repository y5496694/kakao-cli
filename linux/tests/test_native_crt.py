import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "native_crt.py"
spec = importlib.util.spec_from_file_location("kakao_native_crt", MODULE_PATH)
crt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crt)


def pe(marker=b"fixture", *, machine=0x8664, magic=0x20B, dll=True):
    """Synthetic headers exercise the validator without executable payloads."""
    data = bytearray(0x100)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    struct.pack_into("<H", data, 0x96, 0x2000 if dll else 0)
    struct.pack_into("<H", data, 0x98, magic)
    return bytes(data) + marker


def digest(data):
    return hashlib.sha256(data).hexdigest()


def cabinet_mock(monkeypatch, installer, cabinets):
    """Model cabextract listing/stdout output; never execute an EXE or shell."""
    calls = []
    containers = {name: b"MSCF" + name.encode("ascii") for name in cabinets}
    def listing(members):
        return ("Viewing cabinet\n" + "\n".join(
            f" {len(data)} | 01.01.2026 12:00:00 | {name}" for name, data in members.items()
        ) + "\n").encode("utf-8")
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0] == "cabextract", "The redistributable must never be launched"
        assert "shell" not in kwargs
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 30
        archive = Path(argv[-1])
        members = containers if archive == installer else cabinets[archive.name]
        if argv[1] == "-l":
            assert kwargs["env"]["LC_ALL"] == "C"
            return subprocess.CompletedProcess(argv, 0, listing(members), b"")
        assert argv[1:3] == ["-p", "-F"]
        assert len(argv) == 5
        kwargs["stdout"].write(members[argv[3]])
        return subprocess.CompletedProcess(argv, 0, None, b"")
    monkeypatch.setattr(crt.subprocess, "run", run)
    return calls


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    installer = tmp_path / "official-fixture.exe"
    installer.write_bytes(pe(b"synthetic Burn container", dll=False))
    payloads = {name: pe(name.encode("ascii")) for name in crt.DLLS}
    cabinets = {"a0": {name + "_amd64": data for name, data in payloads.items()}}
    calls = cabinet_mock(monkeypatch, installer, cabinets)
    return installer, tmp_path / "cache", payloads, cabinets, calls


@pytest.mark.parametrize("machine,magic,dll", [
    (0x14C, 0x10B, True), (0x14C, 0x20B, True), (0x8664, 0x10B, True), (0x8664, 0x20B, False),
])
def test_validator_rejects_x86_pe32_and_non_dll(machine, magic, dll):
    with pytest.raises(ValueError, match="AMD64 PE32"):
        crt.validate_pe(pe(machine=machine, magic=magic, dll=dll))


def test_validator_accepts_amd64_dll_and_explicit_executable():
    assert crt.validate_pe(pe()) is None
    assert crt.validate_pe(pe(dll=False), dll=False) is None


@pytest.mark.parametrize("data", [b"", b"not a PE", b"MZ" + b"\0" * 80])
def test_validator_rejects_truncated_or_invalid_headers(data):
    with pytest.raises(ValueError):
        crt.validate_pe(data)


def test_validator_checks_file_bound_before_header(monkeypatch):
    payload = pe()
    monkeypatch.setattr(crt, "MAX_FILE", len(payload) - 1)
    with pytest.raises(ValueError, match="bounded"):
        crt.validate_pe(payload)


def test_extracts_exactly_six_amd64_members_using_cabextract_only(extraction):
    installer, cache, payloads, _, calls = extraction
    manifest = crt.extract_runtime(installer, cache)
    assert manifest["installer_sha256"] == digest(installer.read_bytes())
    assert manifest["dll_sha256"] == {name: digest(data) for name, data in payloads.items()}
    assert all((cache / name).read_bytes() == data for name, data in payloads.items())
    assert json.loads((cache / "manifest.json").read_text(encoding="utf-8")) == manifest
    assert all(argv[0] == "cabextract" and str(installer) != argv[0] for argv, _ in calls)
    assert {path.name for path in cache.iterdir()} == {*crt.DLLS, "manifest.json"}


def test_matching_cache_is_hash_verified_and_reused_without_subprocess(extraction):
    installer, cache, _, _, calls = extraction
    expected = crt.extract_runtime(installer, cache)
    calls.clear()
    assert crt.extract_runtime(installer, cache) == expected
    assert calls == []


@pytest.mark.parametrize("damage", ["hash", "missing", "invalid_pe", "manifest"])
def test_damaged_cache_is_reextracted_before_reuse(extraction, damage):
    installer, cache, payloads, _, calls = extraction
    expected = crt.extract_runtime(installer, cache)
    name = crt.DLLS[0]
    if damage == "hash":
        (cache / name).write_bytes(pe(b"changed but still PE32+"))
    elif damage == "missing":
        (cache / name).unlink()
    elif damage == "invalid_pe":
        (cache / name).write_bytes(b"damaged")
    else:
        (cache / "manifest.json").write_text("not JSON", encoding="utf-8")
    calls.clear()
    assert crt.extract_runtime(installer, cache) == expected
    assert calls
    assert (cache / name).read_bytes() == payloads[name]


def test_missing_payload_is_rejected_before_any_cached_dll_write(extraction):
    installer, cache, _, cabinets, _ = extraction
    del cabinets["a0"][crt.DLLS[-1] + "_amd64"]
    with pytest.raises(ValueError, match="all six"):
        crt.extract_runtime(installer, cache)
    assert not list(cache.glob("*.dll"))


def test_x86_payload_is_rejected_before_any_cached_dll_write(extraction):
    installer, cache, _, cabinets, _ = extraction
    cabinets["a0"][crt.DLLS[-1] + "_amd64"] = pe(machine=0x14C, magic=0x10B)
    with pytest.raises(ValueError, match="AMD64"):
        crt.extract_runtime(installer, cache)
    assert not list(cache.glob("*.dll"))


def test_conflicting_duplicate_payload_is_rejected(extraction, monkeypatch):
    installer, cache, _, cabinets, _ = extraction
    cabinets["a1"] = {crt.DLLS[0] + "_amd64": pe(b"different payload")}
    cabinet_mock(monkeypatch, installer, cabinets)
    with pytest.raises(ValueError, match="Conflicting"):
        crt.extract_runtime(installer, cache)
    assert not list(cache.glob("*.dll"))


def test_identical_duplicate_payload_is_allowed(extraction, monkeypatch):
    installer, cache, payloads, cabinets, _ = extraction
    name = crt.DLLS[0]
    cabinets["a1"] = {name + "_amd64": payloads[name]}
    cabinet_mock(monkeypatch, installer, cabinets)
    assert crt.extract_runtime(installer, cache)["dll_sha256"][name] == digest(payloads[name])


def test_member_path_traversal_is_refused_without_calling_cabextract(tmp_path, monkeypatch):
    monkeypatch.setattr(crt.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unsafe member must not be extracted"))
    with pytest.raises(ValueError, match="Unsupported"):
        crt._member(tmp_path / "cabinet", "../outside.dll", 20)


def test_member_size_mismatch_is_not_accepted(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        kwargs["stdout"].write(b"short")
    monkeypatch.setattr(crt.subprocess, "run", run)
    with pytest.raises(ValueError, match="size did not match"):
        crt._member(tmp_path / "cabinet", "member", 10)


@pytest.fixture
def deployment(tmp_path):
    cache, prefix = tmp_path / "cache", tmp_path / "prefix"
    cache.mkdir()
    folder = prefix / "drive_c" / "Program Files" / "Kakao" / "KakaoTalk"
    folder.mkdir(parents=True)
    (folder / "KakaoTalk.exe").write_bytes(pe(b"synthetic application", dll=False))
    payloads = {name: pe(name.encode("ascii")) for name in crt.DLLS}
    for name, data in payloads.items():
        (cache / name).write_bytes(data)
    return cache, prefix, folder, payloads


def test_app_missing_returns_cache_only_without_creating_app_directory(tmp_path):
    prefix = tmp_path / "not-installed"
    result = crt.deploy_runtime(tmp_path / "cache", prefix)
    assert result["status"] == "cache_only"
    assert not prefix.exists()


def test_deploy_rejects_resolved_app_folder_outside_prefix(deployment, tmp_path, monkeypatch):
    cache, prefix, folder, _ = deployment
    outside = tmp_path / "outside"
    actual_resolve = type(folder).resolve
    def resolve(path, *args, **kwargs):
        return outside if path == folder else actual_resolve(path, *args, **kwargs)
    monkeypatch.setattr(type(folder), "resolve", resolve)
    with pytest.raises(ValueError, match="outside"):
        crt.deploy_runtime(cache, prefix)
    assert not outside.exists()
    assert not list(folder.glob("*.dll"))


def test_multiple_app_installations_are_rejected(deployment):
    cache, prefix, folder, _ = deployment
    second = prefix / "drive_c" / "Program Files (x86)" / "Kakao" / "KakaoTalk"
    second.mkdir(parents=True)
    (second / "KakaoTalk.exe").write_bytes(pe(dll=False))
    with pytest.raises(ValueError, match="More than one"):
        crt.deploy_runtime(cache, prefix)
    assert not list(folder.glob("*.dll"))


def test_all_six_sources_are_validated_before_replacing_first_target(deployment):
    cache, prefix, folder, _ = deployment
    first = folder / crt.DLLS[0]
    first.write_bytes(b"previous app-local runtime")
    (cache / crt.DLLS[-1]).write_bytes(b"invalid PE")
    with pytest.raises(ValueError):
        crt.deploy_runtime(cache, prefix)
    assert first.read_bytes() == b"previous app-local runtime"
    assert len(list(folder.glob("*.dll"))) == 1
    assert not (cache / "backups").exists()


def test_all_destinations_are_checked_before_replacing_first_target(deployment):
    cache, prefix, folder, _ = deployment
    first = folder / crt.DLLS[0]
    first.write_bytes(b"previous app-local runtime")
    (folder / crt.DLLS[-1]).mkdir()
    with pytest.raises(ValueError, match="regular file"):
        crt.deploy_runtime(cache, prefix)
    assert first.read_bytes() == b"previous app-local runtime"
    assert not (cache / "backups").exists()


def test_changed_app_local_dll_is_backed_up_before_replacement(deployment):
    cache, prefix, folder, payloads = deployment
    name = crt.DLLS[0]
    previous = b"previous app-local runtime"
    (folder / name).write_bytes(previous)
    result = crt.deploy_runtime(cache, prefix)
    assert result["status"] == "deployed"
    assert result["dlls"] == list(crt.DLLS)
    assert (cache / "backups" / (digest(previous) + "-" + name)).read_bytes() == previous
    assert all((folder / item).read_bytes() == data for item, data in payloads.items())
    assert not list(folder.glob(".crt-*"))


def test_identical_app_local_dlls_are_not_rewritten_or_backed_up(deployment, monkeypatch):
    cache, prefix, folder, payloads = deployment
    for name, data in payloads.items():
        (folder / name).write_bytes(data)
    before = {name: (folder / name).stat().st_mtime_ns for name in crt.DLLS}
    monkeypatch.setattr(crt.tempfile, "NamedTemporaryFile", lambda *args, **kwargs: pytest.fail("Identical DLLs must not be staged"))
    monkeypatch.setattr(crt.shutil, "copy2", lambda *args, **kwargs: pytest.fail("Identical DLLs must not need backup"))
    assert crt.deploy_runtime(cache, prefix)["status"] == "deployed"
    assert {name: (folder / name).stat().st_mtime_ns for name in crt.DLLS} == before
    assert not (cache / "backups").exists()
