from contextlib import contextmanager, nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).parents[1]
sys.path.insert(0, str(SOURCE))
import runtime
spec = importlib.util.spec_from_file_location("kakao_linux_local", SOURCE / "kakao.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


@pytest.fixture
def config(tmp_path):
    return runtime.Config(tmp_path / "private prefix", tmp_path / "state")


def test_runtime_never_reuses_another_display_or_prefix(monkeypatch, config):
    manager = runtime.Runtime(config)
    monkeypatch.setattr(manager, '_read', lambda: {'display': ':94', 'prefix': str(config.prefix),
        'processes': {name: {'pid': 100, 'start': 'one'} for name in ('xvfb', 'openbox', 'kakao')}})
    monkeypatch.setattr(manager, '_alive', lambda _: True)
    result = manager.status()
    assert result['configuration_matches'] is False
    assert result['running'] is False


def test_agent_uses_local_wine_and_json_stdin_not_ssh(monkeypatch, config):
    calls = []
    body = '한글 "문안" & $(no shell)\n둘째 줄'
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=b'{"ok":true,"result":{"submitted":true}}', stderr=b"", returncode=0)
    monkeypatch.setattr(client.subprocess, "run", run)
    assert client.WineAgent(config).command(["send", "친구", body, "--yes"])["submitted"] is True
    command, options = calls[0]
    assert command[0] == "wine"
    assert command[1] == r"C:\Python311\python.exe"
    assert command[2].endswith("wine_agent.py")
    assert body not in command
    assert json.loads(options["input"]) == {"operation": "command", "argv": ["send", "친구", body, "--yes"]}
    assert options["env"]["DISPLAY"] == ":93"
    assert options["env"]["WINEPREFIX"] == str(config.prefix)
    assert options["env"]["KAKAOCLI_ISOLATED_DESKTOP"] == "1"
    assert "shell" not in options


def test_timed_out_send_is_never_retried(monkeypatch, config):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired("wine", 1)
    monkeypatch.setattr(client.subprocess, "run", fail)
    with pytest.raises(client.AgentError, match="Do not automatically repeat"):
        client.WineAgent(config).command(["send", "친구", "문안", "--yes"])
    assert len(calls) == 1


def test_exact_existing_room_needs_no_search(monkeypatch, config):
    agent = client.WineAgent(config)
    monkeypatch.setattr(agent, "command", lambda args: [{"title": "친구"}])
    monkeypatch.setattr(agent, "request", lambda *args: pytest.fail("must not search an exact existing room"))
    assert agent.ensure_room("친구") == "친구"


def test_duplicate_exact_room_is_rejected(monkeypatch, config):
    agent = client.WineAgent(config)
    monkeypatch.setattr(agent, "command", lambda args: [{"title": "친구"}, {"title": "친구"}])
    with pytest.raises(client.AgentError, match="Multiple room"):
        agent.ensure_room("친구")


def test_search_restores_previous_text_when_ocr_fails(monkeypatch, config):
    agent, calls = client.WineAgent(config), []
    monkeypatch.setattr(agent, "command", lambda args: [])
    def request(payload):
        calls.append(payload)
        if payload["operation"] == "search-probe":
            return {"previous_search": "원래 검색", "width": 400, "height": 800}
        return {"restored": True}
    def ambiguous(*args):
        raise client.AgentError("ambiguous")
    monkeypatch.setattr(agent, "request", request)
    monkeypatch.setattr(client, "choose_search_row", ambiguous)
    with pytest.raises(client.AgentError, match="ambiguous"):
        agent.ensure_room("친구")
    assert calls[-1] == {"operation": "restore-search", "query": "원래 검색"}
    assert not any(call["operation"] == "open-selected" for call in calls)


def tsv(rows):
    return ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" + "\n".join(rows)).encode()


def test_ocr_selects_unique_title_and_ignores_header_sidebar(monkeypatch):
    data = tsv([
        "5\t1\t1\t1\t1\t1\t10\t10\t30\t20\t99\t홍길동",
        "5\t1\t1\t1\t2\t1\t70\t100\t60\t20\t90\t홍길동",
    ])
    monkeypatch.setattr(client.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=data, returncode=0))
    assert client.choose_search_row("probe.png", "홍길동", 400, 800) == {"title": "홍길동", "point": [100.0, 110.0]}


@pytest.mark.parametrize("rows,match", [
    (["5\t1\t1\t1\t1\t1\t70\t100\t60\t20\t20\t친구"], "confidence"),
    (["5\t1\t1\t1\t1\t1\t70\t100\t60\t20\t90\t친구", "5\t1\t1\t1\t2\t1\t70\t200\t60\t20\t90\t친구"], "exactly one"),
])
def test_ocr_ambiguity_or_low_confidence_never_selects(monkeypatch, rows, match):
    monkeypatch.setattr(client.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=tsv(rows), returncode=0))
    with pytest.raises(client.AgentError, match=match):
        client.choose_search_row("probe.png", "친구", 400, 800)


def test_result_path_conversion_does_not_rewrite_message_content():
    result = client.host_paths({"path": r"Z:\tmp\사진.jpg", "message": r"Z:\literal", "visual_paths": [r"Z:\tmp\사진.jpg"]})
    assert result["path"] == "/tmp/사진.jpg"
    assert result["message"] == r"Z:\literal"
    assert result["visual_paths"] == ["/tmp/사진.jpg"]


def test_runtime_configuration_never_reuses_desktop_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("KAKAOCLI_DISPLAY", raising=False)
    assert runtime.Config.from_environment().display == ":93"
    with pytest.raises(runtime.RuntimeError, match="private"):
        runtime.Config(Path("prefix"), Path("state"), display=":0")


def test_stop_refuses_reused_pid_without_signalling(monkeypatch, config):
    manager = runtime.Runtime(config)
    monkeypatch.setattr(runtime, "process_identity", lambda pid: "new-process")
    monkeypatch.setattr(runtime.os, "killpg", lambda *args: pytest.fail("must not kill reused PID"), raising=False)
    with pytest.raises(runtime.RuntimeError, match="another process"):
        manager._stop({"prefix": str(config.prefix), "processes": {"kakao": {"pid": 123, "start": "original-process"}}})


def test_vnc_view_is_loopback_and_always_requires_password(monkeypatch, config, tmp_path):
    manager, calls = runtime.Runtime(config), []
    monkeypatch.setattr(manager, "_lock", lambda: nullcontext())
    monkeypatch.setattr(manager, "require_running", lambda: None)
    monkeypatch.setattr(manager, "_read", lambda: {"processes": {}})
    monkeypatch.setattr(manager, "_spawn", lambda name, command, state: calls.append(command))
    monkeypatch.setattr(runtime.shutil, "which", lambda command: "/usr/bin/" + command)
    monkeypatch.setattr(runtime.os, "getuid", lambda: 123, raising=False)
    password = tmp_path / "vnc.pass"
    password.write_bytes(b"12345678")
    original_stat = Path.stat
    def stat(path, **kwargs):
        if path == password:
            return SimpleNamespace(st_mode=0o100600, st_uid=123, st_size=8)
        return original_stat(path, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    result = manager.login_view(password)
    assert "-localhost" in calls[0]
    assert calls[0][calls[0].index("-rfbauth") + 1] == str(password)
    assert result["authentication_required"] is True
    assert result["host"] == "127.0.0.1"


def test_local_analysis_requires_no_runtime(monkeypatch, tmp_path, capsys):
    path = tmp_path / "note.txt"
    path.write_text("Ubuntu 자체 파일", encoding="utf-8")
    monkeypatch.setattr(client, "WineAgent", lambda *args, **kwargs: pytest.fail("no Wine call for local analysis"))
    assert client.main(["analyze", str(path), "--json"]) == 0
    assert "Ubuntu 자체 파일" in json.loads(capsys.readouterr().out)["text"]


def test_old_ssh_options_are_not_supported():
    with pytest.raises(SystemExit):
        client.build_parser().parse_args(["--host", "windows", "rooms"])


def test_install_script_has_unix_newlines_and_scoped_system_install():
    script = (SOURCE / "install.sh").read_bytes()
    assert b"\r\n" not in script
    assert b'if ((install_system)); then\n  configure_system_command "$EUID"\n  source /etc/os-release' in script
    assert b"-nolisten tcp -auth" in script
    assert b"-localhost -rfbauth" in script
    assert b"--no-deps" in script


def test_private_login_screenshot_does_not_use_host_display(monkeypatch, config, tmp_path):
    manager, calls = runtime.Runtime(config), []
    monkeypatch.setattr(manager, "require_running", lambda: None)
    monkeypatch.setattr(runtime.shutil, "which", lambda command: "/usr/bin/" + command)
    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    result = manager.screenshot(tmp_path / "login.png")
    assert Path(result["path"]).read_bytes().startswith(b"\x89PNG")
    assert calls[0][1]["env"]["DISPLAY"] == ":93"
    assert calls[0][1]["umask"] == 0o077
    with pytest.raises(runtime.RuntimeError, match="already exists"):
        manager.screenshot(tmp_path / "login.png")


def test_room_resolution_and_command_share_runtime_lifecycle_lock(monkeypatch):
    events, locked = [], {"value": False}
    class Manager:
        def __init__(self, config):
            pass
        @contextmanager
        def _lock(self):
            locked["value"] = True
            events.append("lock")
            try:
                yield
            finally:
                locked["value"] = False
                events.append("unlock")
        def require_running(self):
            assert locked["value"]
            events.append("require")
    class Agent:
        def __init__(self, *args, **kwargs):
            pass
        def ensure_room(self, query):
            assert locked["value"]
            events.append("resolve")
            return "정확한 방"
        def command(self, arguments):
            assert locked["value"]
            assert arguments == ["messages", "정확한 방", "--json"]
            events.append("command")
            return {"records": []}
    monkeypatch.setattr(client, "Runtime", Manager)
    monkeypatch.setattr(client, "WineAgent", Agent)
    assert client.main(["messages", "친구", "--json"]) == 0
    assert events == ["lock", "require", "resolve", "command", "unlock"]
    assert locked["value"] is False


def test_installer_stops_only_its_wineprefix_with_bounded_wait():
    script = (SOURCE / "install.sh").read_text()
    assert 'WINEPREFIX="$prefix" timeout 10 "$wine_server" -k' in script
    assert 'WINEPREFIX="$prefix" timeout 15 "$wine_server" -w' in script


def test_headless_prefix_initialization_blocks_mono_only_during_bootstrap(monkeypatch, tmp_path):
    selected = runtime.Config(tmp_path / "prefix", tmp_path / "state", wine="/opt/wine11/bin/wine")
    selected.state.mkdir()
    manager, calls = runtime.Runtime(selected), []
    monkeypatch.delenv("WINEDLLOVERRIDES", raising=False)
    monkeypatch.setattr(runtime.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    manager._initialize_prefix()
    command, options = calls[0]
    assert command == ["/opt/wine11/bin/wine", "wineboot.exe", "-u"]
    assert options["timeout"] == 120
    assert options["check"] is True
    assert options["env"]["WINEDLLOVERRIDES"] == "mscoree,mshtml="
    assert "WINEDLLOVERRIDES" not in selected.environment()
    assert (selected.state / "wineboot.log").is_file()


def test_failed_prefix_initialization_never_succeeds_silently(monkeypatch, config):
    config.state.mkdir()
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("wineboot.exe", 120)
    monkeypatch.setattr(runtime.subprocess, "run", fail)
    with pytest.raises(runtime.RuntimeError, match="initialization failed"):
        runtime.Runtime(config)._initialize_prefix()


def test_completed_prefix_does_not_force_wineboot_update(monkeypatch, config):
    for relative in ("system.reg", "user.reg", "drive_c/windows/system32/kernel32.dll"):
        path = config.prefix / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("completed prefix must not run wineboot -u"))
    runtime.Runtime(config)._initialize_prefix()
    assert not (config.state / "wineboot.log").exists()


@pytest.mark.parametrize("missing", [None, "system.reg", "user.reg", "drive_c/windows/system32/kernel32.dll"])
def test_installer_bootstraps_only_incomplete_prefixes(tmp_path, missing):
    import shutil
    bash = "C:/Program Files/Git/bin/bash.exe" if sys.platform == "win32" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash is unavailable")
    for relative in ("system.reg", "user.reg", "drive_c/windows/system32/kernel32.dll"):
        if relative != missing:
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    script = (SOURCE / "install.sh").read_text()
    guard = next(line for line in script.splitlines() if line.startswith("if [[ ! -f $prefix/system.reg"))
    expression = guard.removeprefix("if ").removesuffix("; then")
    result = subprocess.run([bash, "-c", 'prefix=$1\n' + expression, "test", tmp_path.as_posix()], timeout=10)
    assert (result.returncode == 0) is (missing is not None)


def test_installer_uses_selected_wine_and_scopes_bootstrap_override():
    script = (SOURCE / "install.sh").read_text()
    assert 'wine_cmd=${KAKAOCLI_WINE:-wine}' in script
    assert 'WINEDLLOVERRIDES=\'mscoree,mshtml=\' timeout 120 "$wine_cmd" wineboot.exe -u' in script
    assert 'wine_server="$(dirname -- "$wine_executable")/wineserver"' in script
    assert '\n"$wine_cmd" \'C:\\Python311\\python.exe\' -m pip install' in script


@pytest.mark.parametrize("bootstrap_fails", [False, True])
def test_app_starts_only_after_successful_prefix_bootstrap(monkeypatch, config, bootstrap_fails):
    config.prefix.mkdir()
    config.state.mkdir()
    manager, events = runtime.Runtime(config), []
    monkeypatch.setattr(manager, "_lock", lambda: nullcontext())
    monkeypatch.setattr(manager, "_read", lambda: {"processes": {}})
    monkeypatch.setattr(manager, "status", lambda: {"running": False})
    monkeypatch.setattr(manager, "_kakao_executable", lambda: "KakaoTalk.exe")
    monkeypatch.setattr(manager, "_display_ready", lambda: True)
    monkeypatch.setattr(manager, "_spawn", lambda name, *args: events.append(name))
    monkeypatch.setattr(manager, "_stop", lambda state: events.append("cleanup"))
    monkeypatch.setattr(runtime.shutil, "which", lambda name: name)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: None)
    def bootstrap():
        events.append("bootstrap")
        if bootstrap_fails:
            raise runtime.RuntimeError("bootstrap failed")
    monkeypatch.setattr(manager, "_initialize_prefix", bootstrap)
    if bootstrap_fails:
        with pytest.raises(runtime.RuntimeError, match="bootstrap failed"):
            manager.start()
        assert events == ["xvfb", "openbox", "bootstrap", "cleanup"]
    else:
        manager.start()
        assert events == ["xvfb", "openbox", "bootstrap", "kakao"]


def bash_function_result(name, argument, *, setup="", suffix=""):
    import shutil
    bash = shutil.which("bash")
    if sys.platform == "win32" and Path("C:/Program Files/Git/bin/bash.exe").is_file():
        bash = "C:/Program Files/Git/bin/bash.exe"
    if bash is None:
        pytest.skip("Bash is unavailable")
    script = (SOURCE / "install.sh").read_text()
    start = script.index(name + "() {\n")
    end = script.index("\n}\n", start) + 3
    definition = script[start:end]
    return subprocess.run([bash, "-c", definition + '\n' + setup + '\n' + name + ' "$1"' + suffix, "test", argument],
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("version,supported", [
    ("wine-9.0 (Ubuntu 9.0)", False), ("wine-10.20", False), ("wine-11.0", True),
    ("wine-11.0 (Staging)", True), ("wine-12.1", True), ("unknown", False),
])
def test_installer_refuses_unsupported_wine_before_prefix_changes(version, supported):
    result = bash_function_result("require_wine11", version)
    assert (result.returncode == 0) is supported
    if not supported:
        assert "Wine 11 or newer" in result.stderr


def test_system_install_uses_official_winehq_and_respects_custom_wine():
    script = (SOURCE / "install.sh").read_text()
    assert '${ID:-} != ubuntu' in script
    assert '${VERSION_CODENAME:-} =~ ^(noble|jammy)$' in script
    assert '$(dpkg --print-architecture) != amd64' in script
    assert 'if [[ -z ${KAKAOCLI_WINE:-} ]]; then' in script
    assert 'https://dl.winehq.org/wine-builds/winehq.key' in script
    assert 'https://dl.winehq.org/wine-builds/ubuntu/dists/$VERSION_CODENAME/winehq-$VERSION_CODENAME.sources' in script
    assert '"${system_command[@]}" apt-get install -y --install-recommends winehq-stable' in script
    assert 'apt-get install -y wine64' not in script


def test_vc_runtime_uses_official_installer_and_app_specific_dll_overrides():
    script = (SOURCE / "install.sh").read_text()
    assert '--vc-runtime) vc_runtime=' in script
    assert 'https://aka.ms/vc14/vc_redist.x64.exe' in script
    assert '"$wine_cmd" "$vc_runtime"' not in script
    assert 'vc_install_succeeded' not in script
    assert 'for dll in msvcp140 msvcp140_1 msvcp140_2 vcruntime140 vcruntime140_1 concrt140; do' in script
    assert "'HKCU\\Software\\Wine\\AppDefaults\\KakaoTalk.exe\\DllOverrides'" in script
    assert '/d native,builtin /f' in script
    assert "'HKCU\\Software\\Wine\\DllOverrides'" not in script


def test_native_crt_cache_precedes_python_and_deployment_follows_kakao_setup():
    script = (SOURCE / "install.sh").read_text()
    helper = 'python3 "$repo/linux/native_crt.py" --installer "$vc_runtime" --prefix "$prefix" --cache "$state/native-crt"'
    assert helper + '\n' in script
    assert helper + ' --deploy\n' in script
    assert script.index(helper + '\n') < script.index('timeout 300 "$wine_cmd" "$python_installer"')
    assert script.index(helper + ' --deploy\n') > script.index('"$wine_cmd" "$installer" "${installer_arguments[@]}"')
    assert 'apt-get install -y ca-certificates cabextract' in script
    assert 'python3 cabextract curl timeout; do' in script


def test_root_system_install_needs_no_sudo_and_executes_directly():
    result = bash_function_result("configure_system_command", "0", setup="PATH=", suffix=' && printf "%s" "${#system_command[@]}"')
    assert result.returncode == 0
    assert result.stdout == "0"


def test_nonroot_system_install_uses_available_sudo():
    result = bash_function_result("configure_system_command", "1000", setup="sudo() { :; }", suffix=' && printf "%s" "${system_command[*]}"')
    assert result.returncode == 0
    assert result.stdout == "sudo"


def test_nonroot_system_install_fails_clearly_without_sudo():
    result = bash_function_result("configure_system_command", "1000", setup="PATH=")
    assert result.returncode != 0
    assert "requires root or an available sudo" in result.stderr


def stop_fixture(monkeypatch, config):
    manager, events = runtime.Runtime(config), []
    state = {"prefix": str(config.prefix), "processes": {
        name: {"pid": pid, "start": "owned"} for name, pid in (("kakao", 101), ("openbox", 102), ("xvfb", 103))
    }}
    live = {record["pid"]: record["start"] for record in state["processes"].values()}
    config.state.mkdir()
    manager.state_file.write_text(json.dumps(state))
    monkeypatch.setattr(runtime, "process_identity", lambda pid: live.get(pid))
    monkeypatch.setattr(runtime.os, "getpgid", lambda pid: pid, raising=False)
    def kill(pid, _signal):
        events.append(("group", pid))
        live.pop(pid, None)
    monkeypatch.setattr(runtime.os, "killpg", kill, raising=False)
    monkeypatch.setattr(manager, "_wineserver", lambda: "/opt/selected/wineserver")
    return manager, state, live, events


def test_stop_reaps_only_recorded_prefix_before_ending_desktop_groups(monkeypatch, config):
    manager, state, live, events = stop_fixture(monkeypatch, config)
    def run(command, **options):
        events.append((command, options["timeout"], options["env"]["WINEPREFIX"]))
        assert options["check"] is True
        if command[-1] == "-k":
            live.pop(101)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    manager._stop(state)
    assert events[:2] == [(["/opt/selected/wineserver", "-k"], 10, str(config.prefix)),
                          (["/opt/selected/wineserver", "-w"], 15, str(config.prefix))]
    assert events[2:] == [("group", 102), ("group", 103)]
    assert not manager.state_file.exists()


def test_stop_of_wrong_prefix_never_calls_wineserver_or_signals_groups(monkeypatch, config):
    manager, state, _live, events = stop_fixture(monkeypatch, config)
    state["prefix"] = str(config.prefix / "different")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not stop another prefix"))
    with pytest.raises(runtime.RuntimeError, match="different Wine prefix"):
        manager._stop(state)
    assert events == []
    assert manager.state_file.exists()


def test_all_group_ownership_is_checked_before_prefix_shutdown(monkeypatch, config):
    manager, state, _live, events = stop_fixture(monkeypatch, config)
    monkeypatch.setattr(runtime.os, "getpgid", lambda pid: 999 if pid == 103 else pid, raising=False)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("invalid group must prevent prefix kill"))
    with pytest.raises(runtime.RuntimeError, match="group ownership changed"):
        manager._stop(state)
    assert events == []


def test_wineserver_timeout_retains_state_and_does_not_retry(monkeypatch, config):
    manager, state, _live, events = stop_fixture(monkeypatch, config)
    calls = []
    def timeout(command, **options):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, options["timeout"])
    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    with pytest.raises(runtime.RuntimeError, match="state was retained"):
        manager._stop(state)
    assert calls == [["/opt/selected/wineserver", "-k"]]
    assert events == []
    assert manager.state_file.exists()


def test_missing_runtime_record_never_stops_an_unowned_wine_prefix(monkeypatch, config):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("no state means no prefix-wide shutdown"))
    runtime.Runtime(config)._stop({"processes": {}})


def test_wineserver_is_resolved_from_selected_installation(monkeypatch, config, tmp_path):
    directory = tmp_path / "chosen Wine/bin"
    directory.mkdir(parents=True)
    wine = directory / "wine"
    server = directory / "wineserver"
    wine.touch()
    server.touch()
    server.chmod(0o700)
    monkeypatch.setattr(runtime.shutil, "which", lambda command: str(wine))
    assert runtime.Runtime(config)._wineserver() == str(server)
    server.unlink()
    with pytest.raises(runtime.RuntimeError, match="no matching wineserver"):
        runtime.Runtime(config)._wineserver()


def test_real_wine_binary_server_precedes_symlink_directory_server(monkeypatch, config, tmp_path):
    alias_directory, real_directory = tmp_path / "aliases", tmp_path / "wine11/bin"
    alias_directory.mkdir()
    real_directory.mkdir(parents=True)
    wine_alias, real_wine = alias_directory / "wine", real_directory / "wine"
    for directory in (alias_directory, real_directory):
        server = directory / "wineserver"
        server.touch()
        server.chmod(0o700)
    original_resolve = Path.resolve
    monkeypatch.setattr(runtime.shutil, "which", lambda command: str(wine_alias))
    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs: real_wine if path == wine_alias else original_resolve(path, *args, **kwargs))
    assert runtime.Runtime(config)._wineserver() == str(real_directory / "wineserver")
