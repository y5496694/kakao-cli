"""Own one private Xvfb/Openbox/Wine desktop on an Ubuntu host."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid


class RuntimeError(RuntimeError):
    pass


def windows_path(path) -> str:
    """Wine's default Z: drive exposes an absolute host path."""
    return "Z:" + str(Path(path).expanduser().resolve()).replace("/", "\\")


@dataclass(frozen=True)
class Config:
    prefix: Path
    state: Path
    display: str = ":93"
    wine: str = "wine"
    windows_python: str = r"C:\Python311\python.exe"

    def __post_init__(self):
        if not re.fullmatch(r":[1-9]\d*", self.display):
            raise RuntimeError("DISPLAY must be a private display such as :93")
        forbidden = {Path.home().resolve(), Path("/").resolve(), Path("/tmp").resolve(),
                     Path("/var").resolve(), Path("/var/tmp").resolve(), Path("/usr").resolve()}
        if self.prefix.resolve() in forbidden or self.state.resolve() in forbidden:
            raise RuntimeError("Use dedicated Wine and state subdirectories, not shared system directories")

    @classmethod
    def from_environment(cls):
        data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        config = cls(
            Path(os.environ.get("KAKAOCLI_WINEPREFIX", str(data / "kakao-cli/wine"))).expanduser().resolve(),
            Path(os.environ.get("KAKAOCLI_STATE_DIR", str(state / "kakao-cli"))).expanduser().resolve(),
            os.environ.get("KAKAOCLI_DISPLAY", ":93"),
            os.environ.get("KAKAOCLI_WINE", "wine"),
            os.environ.get("KAKAOCLI_WINDOWS_PYTHON", r"C:\Python311\python.exe"),
        )
        if not re.fullmatch(r":[1-9]\d*", config.display):
            raise RuntimeError("KAKAOCLI_DISPLAY must be a private display such as :93")
        return config

    def environment(self):
        return {**os.environ, "WINEPREFIX": str(self.prefix), "DISPLAY": self.display,
                "XAUTHORITY": str(self.state / "Xauthority"), "WINEDEBUG": "-all",
                "KAKAOCLI_ISOLATED_DESKTOP": "1",
                "KAKAOCLI_DATA_DIR": windows_path(self.state / "data")}


def process_identity(pid):
    try:
        # /proc start time prevents stopping an unrelated process after PID reuse.
        value = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = value.rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, ValueError, IndexError):
        return None


class Runtime:
    def __init__(self, config: Config):
        self.config = config
        self.state_file = config.state / "runtime.json"

    def _read(self):
        if not self.state_file.exists():
            return {"processes": {}}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(state.get("processes"), dict):
                raise ValueError("invalid processes")
            return state
        except (OSError, ValueError, AttributeError) as exc:
            raise RuntimeError("Runtime state is unreadable; inspect it before starting another desktop") from exc

    def _write(self, state):
        self.config.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.state.chmod(0o700)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.state_file)

    @contextmanager
    def _lock(self):
        if sys.platform != "linux":
            raise RuntimeError("The local Wine runtime requires Linux")
        import fcntl
        self.config.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.state.chmod(0o700)
        with (self.config.state / "runtime.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    @staticmethod
    def _alive(record):
        return bool(record and record.get("start") and process_identity(record.get("pid")) == record["start"])

    def status(self):
        state = self._read()
        matches = (state.get('display', self.config.display) == self.config.display
                   and state.get('prefix', str(self.config.prefix)) == str(self.config.prefix))
        processes = {name: {**record, "running": self._alive(record)}
                     for name, record in state["processes"].items()}
        return {"mode": "local_wine", "display": self.config.display,
                "wineprefix": str(self.config.prefix), "state_directory": str(self.config.state),
                "configuration_matches": matches,
                "running": matches and all(processes.get(name, {}).get("running", False)
                               for name in ("xvfb", "openbox", "kakao")), "processes": processes,
                "login_state": "not_checked", "real_kakao_login_verified": False}

    def require_running(self):
        if not self.status()["running"]:
            raise RuntimeError("Local Wine desktop is not running. Run: kakao runtime start")

    def _spawn(self, name, command, state):
        with (self.config.state / f"{name}.log").open("ab") as log:
            process = subprocess.Popen(command, env=self.config.environment(), stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        identity = process_identity(process.pid)
        if identity is None:
            if process.poll() is None:
                process.terminate()
            raise RuntimeError(f"{name} exited during startup; inspect {name}.log")
        state["processes"][name] = {"pid": process.pid, "start": identity}
        self._write(state)
        return process

    def _kakao_executable(self):
        configured = os.environ.get("KAKAOCLI_KAKAO_EXE")
        if configured:
            return configured
        for base in ("Program Files (x86)", "Program Files"):
            path = self.config.prefix / "drive_c" / base / "Kakao/KakaoTalk/KakaoTalk.exe"
            if path.is_file():
                return str(path)
        raise RuntimeError("KakaoTalk.exe is not installed in this Wine prefix. Run linux/install.sh first")

    def _display_ready(self):
        try:
            result = subprocess.run(['xdpyinfo', '-display', self.config.display],
                env=self.config.environment(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=2)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _initialize_prefix(self):
        # Bootstrap helpers may request Mono/Gecko downloads. Disable those
        # components only during wineboot so headless startup cannot await a GUI.
        environment = {**self.config.environment(), "WINEDLLOVERRIDES": "mscoree,mshtml="}
        log_path = self.config.state / "wineboot.log"
        try:
            with log_path.open("ab") as log:
                subprocess.run([self.config.wine, "wineboot.exe", "-u"], env=environment,
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Headless Wine prefix initialization failed; inspect {log_path}") from exc

    def start(self):
        with self._lock():
            state = self._read()
            if self.status()["running"]:
                return self.status()
            if any(self._alive(record) for record in state["processes"].values()):
                raise RuntimeError("A partial runtime is still running; stop it before restarting")
            for program in ("Xvfb", "openbox", "xauth", "xdpyinfo", self.config.wine):
                if not shutil.which(program):
                    raise RuntimeError(f"Missing runtime program: {program}")
            executable = self._kakao_executable()
            self.config.prefix.chmod(0o700)
            number = self.config.display[1:]
            socket = Path(f"/tmp/.X11-unix/X{number}")
            if socket.exists() or Path(f"/tmp/.X{number}-lock").exists():
                raise RuntimeError("Private DISPLAY is already occupied; choose another KAKAOCLI_DISPLAY")
            state = {"display": self.config.display, "prefix": str(self.config.prefix), "processes": {}}
            auth = self.config.state / "Xauthority"
            auth.touch(mode=0o600, exist_ok=True)
            auth.chmod(0o600)
            cookie = secrets.token_hex(16)
            subprocess.run(["xauth", "-f", str(auth), "source", "-"],
                           input=f"add {self.config.display} MIT-MAGIC-COOKIE-1 {cookie}\n".encode(),
                           capture_output=True, check=True)
            try:
                server = self._spawn("xvfb", ["Xvfb", self.config.display, "-screen", "0", "1280x960x24",
                                              "-nolisten", "tcp", "-auth", str(auth)], state)
                deadline = time.monotonic() + 10
                while not self._display_ready():
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Xvfb did not become ready; inspect xvfb.log")
                    time.sleep(.1)
                self._spawn("openbox", ["openbox", "--sm-disable"], state)
                self._initialize_prefix()
                self._spawn("kakao", [self.config.wine, executable], state)
            except BaseException:
                self._stop(state)
                raise
            return self.status()

    def _stop(self, state):
        for name in ("vnc", "kakao", "openbox", "xvfb"):
            record = state["processes"].get(name)
            if not record:
                continue
            identity = process_identity(record["pid"])
            if identity is not None and identity != record.get("start"):
                raise RuntimeError(f"Refusing to stop {name}: PID now belongs to another process")
            if identity is None:
                continue
            if os.getpgid(record["pid"]) != record["pid"]:
                raise RuntimeError(f"Refusing to stop {name}: process group ownership changed")
            os.killpg(record["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 2
            while self._alive(record) and time.monotonic() < deadline:
                time.sleep(.05)
            if self._alive(record):
                os.killpg(record["pid"], signal.SIGKILL)
        self.state_file.unlink(missing_ok=True)

    def stop(self):
        with self._lock():
            self._stop(self._read())
        return {"mode": "local_wine", "running": False, "stopped": True}

    def login_view(self, password_file, *, port=5903):
        with self._lock():
            self.require_running()
            if not 1024 <= port <= 65535:
                raise RuntimeError("VNC port must be between 1024 and 65535")
            password = Path(password_file).expanduser().resolve()
            if not password.is_file() or password.stat().st_mode & 0o077 or password.stat().st_size < 8:
                raise RuntimeError("Use an existing x11vnc password file with permissions 600")
            if password.stat().st_uid != os.getuid():
                raise RuntimeError("VNC password file must belong to the current user")
            if not shutil.which("x11vnc"):
                raise RuntimeError("Install x11vnc to use the optional login view")
            state = self._read()
            if self._alive(state["processes"].get("vnc")):
                raise RuntimeError("Login view is already running; stop the runtime to close it")
            self._spawn("vnc", ["x11vnc", "-display", self.config.display, "-auth",
                               str(self.config.state / "Xauthority"), "-localhost", "-rfbauth", str(password),
                               "-rfbport", str(port), "-forever", "-shared", "-noxdamage"], state)
            return {"mode": "local_wine", "host": "127.0.0.1", "port": port,
                    "authentication_required": True, "display": self.config.display}

    def screenshot(self, output):
        self.require_running()
        if not shutil.which("scrot"):
            raise RuntimeError("Install scrot to capture the isolated login desktop")
        destination = Path(output).expanduser().resolve()
        if destination.suffix.casefold() != ".png":
            raise RuntimeError("Login screenshot output must have a .png extension")
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("Screenshot output already exists; no overwrite performed")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.parent / (".kakao-" + uuid.uuid4().hex + ".png")
        try:
            subprocess.run(["scrot", "--silent", str(temporary)], env=self.config.environment(),
                           capture_output=True, check=True, timeout=10, umask=0o077)
            if temporary.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError("Private desktop screenshot did not produce a PNG")
            temporary.chmod(0o600)
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {"mode": "local_wine", "path": str(destination), "display": self.config.display,
                "coverage": "isolated_desktop", "format": "PNG"}
