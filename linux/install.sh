#!/usr/bin/env bash
# Install a dedicated local Wine runtime. This does not configure SSH.
set -euo pipefail
umask 077

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
prefix=${KAKAOCLI_WINEPREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/kakao-cli/wine}
state=${KAKAOCLI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/kakao-cli}
display=${KAKAOCLI_DISPLAY:-:93}
installer='' python_installer='' password_file='' install_system=0 enable_service=0
installer_arguments=()

usage() {
  cat <<'HELP'
Usage: bash linux/install.sh [options]
  --installer PATH             Official local KakaoTalk Windows installer
  --installer-arg VALUE        Explicit installer argument; may be repeated
  --python-installer PATH      Local Python 3.11.9 x64 installer (otherwise python.org)
  --vnc-password-file PATH     Existing x11vnc password file (chmod 600), localhost only
  --prefix PATH               Dedicated Wine prefix
  --install-system            Explicitly install Ubuntu dependencies, including i386 Wine
  --enable-service            Enable and start the systemd user service after installation

Without explicit unattended installer arguments, Kakao setup requires the
authenticated optional VNC view to complete its GUI inside the private desktop.
Python 3.11.9 is the last 3.11 Windows binary installer, not the latest security release.
HELP
}

while (($#)); do
  case "$1" in
    --installer) installer=${2:?Missing installer path}; shift 2 ;;
    --installer-arg) installer_arguments+=("${2:?Missing installer argument}"); shift 2 ;;
    --python-installer) python_installer=${2:?Missing Python installer path}; shift 2 ;;
    --vnc-password-file) password_file=${2:?Missing password file}; shift 2 ;;
    --prefix) prefix=${2:?Missing Wine prefix}; shift 2 ;;
    --install-system) install_system=1; shift ;;
    --enable-service) enable_service=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ $(uname -s) == Linux ]] || { printf 'This installer requires Ubuntu/Linux.\n' >&2; exit 1; }
[[ $display =~ ^:[1-9][0-9]*$ ]] || { printf 'Use a private DISPLAY such as :93.\n' >&2; exit 1; }
if ((install_system)); then
  sudo dpkg --add-architecture i386
  sudo apt-get update
  sudo apt-get install -y wine64 wine32:i386 xvfb openbox xauth x11vnc scrot curl python3 python3-venv tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng fonts-nanum
fi
for program in wine wineboot Xvfb openbox xauth python3 curl timeout; do
  command -v "$program" >/dev/null || { printf 'Missing %s; review --install-system.\n' "$program" >&2; exit 1; }
done
prefix=$(realpath -m -- "$prefix")
state=$(realpath -m -- "$state")
for directory in "$prefix" "$state"; do
  case "$directory" in /|/tmp|/var|/var/tmp|/usr|"$HOME")
    printf 'Use dedicated Wine/state subdirectories, not shared system directories.\n' >&2; exit 1 ;;
  esac
done
mkdir -p -- "$prefix" "$state"
chmod 700 -- "$prefix" "$state"
exec 9>"$state/install.lock"
flock -n 9 || { printf 'Another installer is already using this runtime.\n' >&2; exit 1; }
number=${display#:}
[[ ! -e /tmp/.X11-unix/X$number && ! -e /tmp/.X$number-lock ]] || { printf 'Private DISPLAY is occupied; stop its runtime or choose another number.\n' >&2; exit 1; }
if [[ -n $installer ]]; then
  [[ -f $installer ]] || { printf 'Kakao installer must be an existing local file.\n' >&2; exit 1; }
  installer=$(realpath -- "$installer")
  if [[ -z $password_file && ${#installer_arguments[@]} == 0 ]]; then
    printf 'Kakao GUI setup needs --vnc-password-file, or explicit --installer-arg values.\n' >&2
    exit 1
  fi
fi
if [[ -n $password_file ]]; then
  command -v x11vnc >/dev/null || { printf 'Install x11vnc for optional login view.\n' >&2; exit 1; }
  password_file=$(realpath -- "$password_file")
  python3 - "$password_file" <<'PY'
import os, pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.stat()
if not p.is_file() or s.st_uid != os.getuid() or s.st_mode & 0o077 or s.st_size < 8:
    raise SystemExit('VNC password must be a current-user file with mode 600 and valid stored-password content')
PY
fi

export WINEPREFIX=$prefix DISPLAY=$display XAUTHORITY="$state/Xauthority" WINEDEBUG=-all KAKAOCLI_ISOLATED_DESKTOP=1
touch "$XAUTHORITY"
chmod 600 "$XAUTHORITY"
cookie=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
printf 'add %s MIT-MAGIC-COOKIE-1 %s\n' "$display" "$cookie" | xauth -f "$XAUTHORITY" source -
unset cookie
process_start() {
  python3 - "$1" <<'PY'
import pathlib, sys
try: print(pathlib.Path('/proc/' + sys.argv[1] + '/stat').read_text().rsplit(')',1)[1].split()[19])
except (OSError, IndexError): pass
PY
}
xpid='' opid='' vpid='' xstart='' ostart='' vstart=''
stop_owned() {
  local pid=$1 identity=$2
  [[ -n $pid && -n $identity && $(process_start "$pid") == "$identity" ]] || return 0
  kill "$pid" 2>/dev/null || true
  for _ in {1..30}; do
    [[ $(process_start "$pid") == "$identity" ]] || break
    sleep .1
  done
  [[ $(process_start "$pid") != "$identity" ]] || kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}
cleanup() {
  stop_owned "$vpid" "$vstart"
  if command -v wineserver >/dev/null; then
    WINEPREFIX="$prefix" timeout 10 wineserver -k 2>/dev/null || true
    WINEPREFIX="$prefix" timeout 15 wineserver -w 2>/dev/null || printf 'Dedicated Wine prefix did not finish shutdown within 15 seconds.\n' >&2
  fi
  stop_owned "$opid" "$ostart"
  stop_owned "$xpid" "$xstart"
}
trap cleanup EXIT INT TERM
Xvfb "$display" -screen 0 1280x960x24 -nolisten tcp -auth "$XAUTHORITY" >"$state/install-xvfb.log" 2>&1 & xpid=$!
xstart=$(process_start "$xpid")
for _ in {1..100}; do
  [[ ! -e /tmp/.X11-unix/X$number ]] || break
  kill -0 "$xpid" 2>/dev/null || { printf 'Xvfb exited; inspect install-xvfb.log.\n' >&2; exit 1; }
  sleep .1
done
[[ -e /tmp/.X11-unix/X$number ]] || { printf 'Xvfb readiness timed out.\n' >&2; exit 1; }
openbox --sm-disable >"$state/install-openbox.log" 2>&1 & opid=$!
ostart=$(process_start "$opid")
if [[ -n $password_file ]]; then
  x11vnc -display "$display" -auth "$XAUTHORITY" -localhost -rfbauth "$password_file" -rfbport 5903 -forever -shared >"$state/install-vnc.log" 2>&1 & vpid=$!
  vstart=$(process_start "$vpid")
  printf 'Setup view: 127.0.0.1:5903 (password required).\n'
fi
timeout 120 wineboot -u
if [[ -z $python_installer ]]; then
  python_installer="$state/python-3.11.9-amd64.exe"
  if [[ ! -f $python_installer ]]; then
    curl --fail --location --proto '=https' --tlsv1.2 --output "$python_installer.part" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    mv -- "$python_installer.part" "$python_installer"
  fi
fi
[[ -f $python_installer ]] || { printf 'Python installer file is missing.\n' >&2; exit 1; }
if [[ ! -f $prefix/drive_c/Python311/python.exe ]]; then
  timeout 300 wine "$python_installer" /quiet InstallAllUsers=0 'TargetDir=C:\Python311' Include_launcher=0 Include_test=0 PrependPath=0
fi
wine 'C:\Python311\python.exe' -m pip install --disable-pip-version-check 'pywin32>=306' 'Pillow>=10'
repo_windows="Z:${repo//\//\\}"
wine 'C:\Python311\python.exe' -m pip install --no-deps "$repo_windows"
if [[ -n $installer ]]; then
  printf 'Complete official Kakao setup inside the isolated desktop if prompted.\n'
  wine "$installer" "${installer_arguments[@]}"
fi
python3 -m venv "$repo/.venv-linux"
"$repo/.venv-linux/bin/python" -m pip install -r "$repo/linux/requirements-analysis.txt"

mkdir -p "$HOME/.local/bin" "$HOME/.config/kakao-cli" "$HOME/.config/systemd/user"
launcher="$HOME/.local/bin/kakao-local"
if [[ -e $launcher ]] && ! grep -q 'kakao-cli local Wine launcher' "$launcher"; then
  printf 'Existing kakao-local launcher is not managed by this installer.\n' >&2; exit 1
fi
printf '#!/usr/bin/env bash\n# kakao-cli local Wine launcher\nset -a\nsource "$HOME/.config/kakao-cli/runtime.env"\nset +a\nexec %q %q "$@"\n' "$repo/.venv-linux/bin/python" "$repo/linux/kakao.py" >"$launcher"
chmod 755 "$launcher"
python3 - "$HOME/.config/kakao-cli/runtime.env" "$prefix" "$state" "$display" <<'PY'
import os, pathlib, shlex, sys
p = pathlib.Path(sys.argv[1])
p.write_text(''.join(k + '=' + shlex.quote(v) + '\n' for k, v in zip(('KAKAOCLI_WINEPREFIX','KAKAOCLI_STATE_DIR','KAKAOCLI_DISPLAY'),sys.argv[2:])),encoding='utf-8')
p.chmod(0o600)
PY
cp -- "$repo/linux/kakao-cli.service" "$HOME/.config/systemd/user/kakao-cli.service"
cleanup
trap - EXIT INT TERM
if ((enable_service)); then
  systemctl --user daemon-reload
  systemctl --user enable --now kakao-cli.service
fi
printf 'Installed local Wine CLI: %s\nFirst login: kakao-local runtime start; kakao-local runtime login-view --password-file PATH\n' "$launcher"
