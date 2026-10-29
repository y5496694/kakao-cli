"""Prepare app-local AMD64 CRT DLLs from an official Microsoft redistributable.

The Burn setup executable is never run. cabextract reads its embedded cabinets;
only the six named AMD64 DLLs are deployed inside the selected Wine prefix.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
        "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")
MAX_FILE = 64 * 1024 * 1024


def validate_pe(data, *, dll=True):
    if len(data) < 64 or len(data) > MAX_FILE or data[:2] != b"MZ":
        raise ValueError("CRT payload is not a bounded PE file")
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset + 26 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("CRT payload has an invalid PE header")
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    magic = struct.unpack_from("<H", data, offset + 24)[0]
    flags = struct.unpack_from("<H", data, offset + 22)[0]
    if machine != 0x8664 or magic != 0x20b or (dll and not flags & 0x2000):
        raise ValueError("An AMD64 PE32+ DLL is required")


def _read(path):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE:
        raise ValueError(f"Missing or oversized runtime input: {path.name}")
    return path.read_bytes()


def _entries(archive):
    result = subprocess.run(["cabextract", "-l", str(archive)], capture_output=True,
                            timeout=30, check=True, env={**os.environ, "LC_ALL": "C"})
    entries = []
    for line in result.stdout.decode("utf-8", errors="strict").splitlines():
        match = re.fullmatch(r"\s*(\d+)\s*\|[^|]+\|\s*(.+?)\s*", line)
        if match:
            size, name = int(match[1]), match[2]
            entries.append((name, size))
    if not entries or len(entries) > 512:
        raise ValueError("Unsupported redistributable cabinet layout")
    return entries


def _member(archive, name, size):
    # Print a selected member to a private file, never extract archive paths.
    if not 0 < size <= MAX_FILE or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Unsupported or oversized cabinet member")
    with tempfile.TemporaryFile() as output:
        subprocess.run(["cabextract", "-p", "-F", name, str(archive)], stdout=output,
                       stderr=subprocess.PIPE, timeout=30, check=True)
        if output.tell() != size:
            raise ValueError("Cabinet member size did not match its directory")
        output.seek(0)
        return output.read(MAX_FILE + 1)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def extract_runtime(installer, cache):
    source_hash = _digest(_read(installer))
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache.chmod(0o700)
    manifest_path = cache / "manifest.json"
    if manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            if old["installer_sha256"] == source_hash:
                for name in DLLS:
                    data = _read(cache / name)
                    validate_pe(data)
                    if _digest(data) != old["dll_sha256"][name]:
                        raise ValueError("Cached DLL digest changed")
                return old
        except (OSError, ValueError, KeyError, TypeError):
            pass
    selected = {}
    with tempfile.TemporaryDirectory(prefix="cab-", dir=cache) as temporary:
        for name, size in _entries(installer):
            if not re.fullmatch(r"a\d{1,3}", name):
                continue
            container = _member(installer, name, size)
            if container[:4] != b"MSCF":
                continue
            archive = Path(temporary) / name
            archive.write_bytes(container)
            for member, member_size in _entries(archive):
                matching = [dll for dll in DLLS if member.casefold() == dll + "_amd64"]
                if not matching:
                    continue
                data = _member(archive, member, member_size)
                validate_pe(data)
                dll = matching[0]
                if dll in selected and selected[dll] != data:
                    raise ValueError("Conflicting AMD64 CRT payloads")
                selected[dll] = data
    if set(selected) != set(DLLS):
        raise ValueError("Official redistributable does not contain all six expected AMD64 DLLs")
    for name, data in selected.items():
        target = cache / name
        if target.is_symlink():
            raise ValueError("Runtime cache DLL must not be a symlink")
        target.write_bytes(data)
        target.chmod(0o600)
    manifest = {"installer_sha256": source_hash,
                "dll_sha256": {name: _digest(selected[name]) for name in DLLS}}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    return manifest


def deploy_runtime(cache, prefix):
    prefix = prefix.resolve()
    candidates = [prefix / "drive_c" / folder / "Kakao/KakaoTalk"
                  for folder in ("Program Files", "Program Files (x86)")]
    candidates = [folder for folder in candidates if (folder / "KakaoTalk.exe").is_file()]
    if not candidates:
        return {"status": "cache_only", "next": "Run the installer again with --installer to install KakaoTalk"}
    if len(candidates) != 1:
        raise ValueError("More than one Kakao installation exists in this Wine prefix")
    folder = candidates[0].resolve()
    if not folder.is_relative_to(prefix):
        raise ValueError("Kakao installation resolves outside the dedicated Wine prefix")
    validate_pe(_read(folder / "KakaoTalk.exe"), dll=False)
    # Validate every source and destination before replacing the first DLL.
    payloads = {}
    for name in DLLS:
        payloads[name] = _read(cache / name)
        validate_pe(payloads[name])
        target = folder / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("App-local runtime target must be a regular file")
    for name, data in payloads.items():
        target = folder / name
        if target.exists():
            previous = _read(target)
            if previous == data:
                continue
            backup = cache / "backups" / (_digest(previous) + "-" + name)
            backup.parent.mkdir(exist_ok=True, mode=0o700)
            if not backup.exists():
                shutil.copy2(target, backup)
                backup.chmod(0o600)
        with tempfile.NamedTemporaryFile(prefix=".crt-", dir=folder, delete=False) as output:
            staged = Path(output.name)
            output.write(data)
        try:
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)
    return {"status": "deployed", "directory": str(folder), "dlls": list(DLLS)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = extract_runtime(args.installer, args.cache)
        result = deploy_runtime(args.cache, args.prefix) if args.deploy else {"status": "cached"}
        print(json.dumps({**result, **manifest}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Native CRT preparation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
