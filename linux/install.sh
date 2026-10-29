#!/usr/bin/env bash
# Install a dedicated local Wine runtime. This does not configure SSH.
set -euo pipefail
umask 077

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
prefix=${KAKAOCLI_WINEPREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/kakao-cli/wine}
state=${KAKAOCLI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/kakao-cli}
display=${KAKAOCLI_DISPLAY:-:93}
wine_cmd=${KAKAOCLI_WINE:-wine}
installer='' python_installer='' vc_runtime='' password_file='' install_system=0 enable_service=0
installer_arguments=()

usage() {
  cat <<'HELP'
Usage: bash linux/install.sh [options]
  --installer PATH             Official local KakaoTalk Windows installer
  --installer-arg VALUE        Explicit installer argument; may be repeated
  --python-installer PATH      Local Python 3.11.9 x64 installer (otherwise python.org)
  --vc-runtime PATH            Official VC++ x64 original for extraction (otherwise Microsoft)
  --vnc-password-file PATH     Existing x11vnc password file (chmod 600), localhost only
  --prefix PATH               Dedicated Wine prefix
  --install-system            Explicitly add WineHQ stable 11+ and Ubuntu dependencies
  --enable-service            Enable and start the systemd user service after installation
  KAKAOCLI_WINE               Selected Wine executable, including a custom Wine installation

Without explicit unattended installer arguments, Kakao setup requires the
authenticated optional VNC view to complete its GUI inside the private desktop.
Python 3.11.9 is the last 3.11 Windows binary installer, not the latest security release.
HELP
}

require_wine11() {
  if [[ $1 =~ ^wine-([0-9]+)([.~-]|$) ]] && ((BASH_REMATCH[1] >= 11)); then
    return 0
  fi
  printf 'Wine 11 or newer is required; Ubuntu Wine 9 is incompatible. Select KAKAOCLI_WINE or use --install-system for WineHQ stable.\n' >&2
  return 1
}

configure_system_command() {
  system_command=()
  if (($1 != 0)); then
    if ! command -v sudo >/dev/null; then
      printf 'System installation requires root or an available sudo command.\n' >&2
      return 1
    fi
    system_command=(sudo)
  fi
}

while (($#)); do
  case "$1" in
    --installer) installer=${2:?Missing installer path}; shift 2 ;;
    --installer-arg) installer_arguments+=("${2:?Missing installer argument}"); shift 2 ;;
    --python-installer) python_installer=${2:?Missing Python installer path}; shift 2 ;;
    --vc-runtime) vc_runtime=${2:?Missing VC++ redistributable path}; shift 2 ;;
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
  configure_system_command "$EUID"
  source /etc/os-release
  if [[ ${ID:-} != ubuntu || ! ${VERSION_CODENAME:-} =~ ^(noble|jammy)$ || $(dpkg --print-architecture) != amd64 ]]; then
    printf 'WineHQ installation supports Ubuntu amd64 noble or jammy only.\n' >&2
    exit 1
  fi
  "${system_command[@]}" dpkg --add-architecture i386
  "${system_command[@]}" apt-get update
  "${system_command[@]}" apt-get install -y ca-certificates cabextract xvfb openbox xauth x11-utils x11vnc scrot curl python3 python3-venv tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng fonts-nanum
  if [[ -z ${KAKAOCLI_WINE:-} ]]; then
    repo_stage=$(mktemp -d)
    if ! curl --fail --location --proto '=https' --tlsv1.2 --output "$repo_stage/winehq.key" https://dl.winehq.org/wine-builds/winehq.key ||
       ! curl --fail --location --proto '=https' --tlsv1.2 --output "$repo_stage/winehq.sources" "https://dl.winehq.org/wine-builds/ubuntu/dists/$VERSION_CODENAME/winehq-$VERSION_CODENAME.sources"; then
      rm -f -- "$repo_stage/winehq.key" "$repo_stage/winehq.sources"
      rmdir -- "$repo_stage"
      printf 'Official WineHQ repository metadata download failed.\n' >&2
      exit 1
    fi
    "${system_command[@]}" mkdir -p /etc/apt/keyrings
    "${system_command[@]}" install -m 644 "$repo_stage/winehq.key" /etc/apt/keyrings/winehq-archive.key
    "${system_command[@]}" install -m 644 "$repo_stage/winehq.sources" "/etc/apt/sources.list.d/winehq-$VERSION_CODENAME.sources"
    rm -f -- "$repo_stage/winehq.key" "$repo_stage/winehq.sources"
    rmdir -- "$repo_stage"
    "${system_command[@]}" apt-get update
    "${system_command[@]}" apt-get install -y --install-recommends winehq-stable
  fi
fi
for program in "$wine_cmd" Xvfb openbox xauth xdpyinfo python3 cabextract curl timeout; do
  command -v "$program" >/dev/null || { printf 'Missing %s; review --install-system.\n' "$program" >&2; exit 1; }
done
wine_executable=$(command -v -- "$wine_cmd")
wine_version=$("$wine_cmd" --version) || { printf 'Could not determine selected Wine version.\n' >&2; exit 1; }
require_wine11 "$wine_version"
wine_server="$(dirname -- "$wine_executable")/wineserver"
if [[ ! -x $wine_server ]]; then
  wine_server="$(dirname -- "$(realpath -- "$wine_executable")")/wineserver"
fi
if [[ ! -x $wine_server ]]; then
  wine_server=$(command -v wineserver || true)
fi
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
  if [[ -n $wine_server && -x $wine_server ]]; then
    WINEPREFIX="$prefix" timeout 10 "$wine_server" -k 2>/dev/null || true
    WINEPREFIX="$prefix" timeout 15 "$wine_server" -w 2>/dev/null || printf 'Dedicated Wine prefix did not finish shutdown within 15 seconds.\n' >&2
  fi
  stop_owned "$opid" "$ostart"
  stop_owned "$xpid" "$xstart"
}
trap cleanup EXIT INT TERM
Xvfb "$display" -screen 0 1280x960x24 -nolisten tcp -auth "$XAUTHORITY" >"$state/install-xvfb.log" 2>&1 & xpid=$!
xstart=$(process_start "$xpid")
for _ in {1..100}; do
  if xdpyinfo -display "$display" >/dev/null 2>&1; then break; fi
  kill -0 "$xpid" 2>/dev/null || { printf 'Xvfb exited; inspect install-xvfb.log.\n' >&2; exit 1; }
  sleep .1
done
xdpyinfo -display "$display" >/dev/null 2>&1 || { printf 'Xvfb readiness timed out.\n' >&2; exit 1; }
openbox --sm-disable >"$state/install-openbox.log" 2>&1 & opid=$!
ostart=$(process_start "$opid")
if [[ -n $password_file ]]; then
  x11vnc -display "$display" -auth "$XAUTHORITY" -localhost -rfbauth "$password_file" -rfbport 5903 -forever -shared >"$state/install-vnc.log" 2>&1 & vpid=$!
  vstart=$(process_start "$vpid")
  printf 'Setup view: 127.0.0.1:5903 (password required).\n'
fi
if [[ ! -f $prefix/system.reg || ! -f $prefix/user.reg || ! -f $prefix/drive_c/windows/system32/kernel32.dll ]]; then
  if ! WINEDLLOVERRIDES='mscoree,mshtml=' timeout 120 "$wine_cmd" wineboot.exe -u >"$state/install-wineboot.log" 2>&1; then
    printf 'Headless Wine initialization failed; inspect install-wineboot.log.\n' >&2
    exit 1
  fi
fi
if [[ -z $vc_runtime ]]; then
  vc_runtime="$state/vc_redist.x64.exe"
  if [[ ! -f $vc_runtime ]]; then
    curl --fail --location --proto '=https' --tlsv1.2 --output "$vc_runtime.part" https://aka.ms/vc14/vc_redist.x64.exe
    mv -- "$vc_runtime.part" "$vc_runtime"
  fi
fi
[[ -f $vc_runtime ]] || { printf 'VC++ extraction requires an existing local official redistributable.\n' >&2; exit 1; }
python3 "$repo/linux/native_crt.py" --installer "$vc_runtime" --prefix "$prefix" --cache "$state/native-crt"
for dll in msvcp140 msvcp140_1 msvcp140_2 vcruntime140 vcruntime140_1 concrt140; do
  timeout 30 "$wine_cmd" reg.exe add 'HKCU\Software\Wine\AppDefaults\KakaoTalk.exe\DllOverrides' /v "$dll" /t REG_SZ /d native,builtin /f >"$state/install-crt-overrides.log" 2>&1
done
if [[ -z $python_installer ]]; then
  python_installer="$state/python-3.11.9-amd64.exe"
  if [[ ! -f $python_installer ]]; then
    curl --fail --location --proto '=https' --tlsv1.2 --output "$python_installer.part" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    mv -- "$python_installer.part" "$python_installer"
  fi
fi
[[ -f $python_installer ]] || { printf 'Python installer file is missing.\n' >&2; exit 1; }
if [[ ! -f $prefix/drive_c/Python311/python.exe ]]; then
  timeout 300 "$wine_cmd" "$python_installer" /quiet InstallAllUsers=0 'TargetDir=C:\Python311' Include_launcher=0 Include_test=0 PrependPath=0
fi
"$wine_cmd" 'C:\Python311\python.exe' -m pip install --disable-pip-version-check 'pywin32>=306' 'Pillow>=10'
repo_windows="Z:${repo//\//\\}"
"$wine_cmd" 'C:\Python311\python.exe' -m pip install --no-deps "$repo_windows"
if [[ -n $installer ]]; then
  printf 'Complete official Kakao setup inside the isolated desktop if prompted.\n'
  "$wine_cmd" "$installer" "${installer_arguments[@]}"
fi
python3 "$repo/linux/native_crt.py" --installer "$vc_runtime" --prefix "$prefix" --cache "$state/native-crt" --deploy
python3 -m venv "$repo/.venv-linux"
"$repo/.venv-linux/bin/python" -m pip install -r "$repo/linux/requirements-analysis.txt"

mkdir -p "$HOME/.local/bin" "$HOME/.config/kakao-cli" "$HOME/.config/systemd/user"
launcher="$HOME/.local/bin/kakao-local"
if [[ -e $launcher ]] && ! grep -q 'kakao-cli local Wine launcher' "$launcher"; then
  printf 'Existing kakao-local launcher is not managed by this installer.\n' >&2; exit 1
fi
printf '#!/usr/bin/env bash\n# kakao-cli local Wine launcher\nset -a\nsource "$HOME/.config/kakao-cli/runtime.env"\nset +a\nexec %q %q "$@"\n' "$repo/.venv-linux/bin/python" "$repo/linux/kakao.py" >"$launcher"
chmod 755 "$launcher"
python3 - "$HOME/.config/kakao-cli/runtime.env" "$prefix" "$state" "$display" "$wine_executable" <<'PY'
import os, pathlib, shlex, sys
p = pathlib.Path(sys.argv[1])
p.write_text(''.join(k + '=' + shlex.quote(v) + '\n' for k, v in zip(('KAKAOCLI_WINEPREFIX','KAKAOCLI_STATE_DIR','KAKAOCLI_DISPLAY','KAKAOCLI_WINE'),sys.argv[2:])),encoding='utf-8')
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
