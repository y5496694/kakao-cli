# Ubuntu 서버 자체 실행형 카카오톡 CLI

서버 안에서 **Wine으로 카카오톡 자체를 실행**합니다. 전용 Xvfb 가상 화면을 사용하므로 물리 모니터, 외부 Windows PC, SSH 연결이 필요하지 않습니다. 조회·전송·사진/파일 저장·분석이 서버 내부에서 처리됩니다. Windows용 카카오톡을 실행하는 호환 방식이며 공식 Linux 카카오톡 앱이나 카카오 프로토콜을 새로 구현한 프로그램은 아닙니다.

Windows에서 수행한 REA 분석의 컨트롤 선택자를 재사용합니다. Wine 어댑터는 Frida에 의존하지 않습니다. 전역 입력이 필요한 경우에도 전용 가상 화면 안에서만 사용합니다. 실제 Wine DLL과 격리 실행 플래그를 확인하지 못하면 에이전트가 앱을 조작하기 전에 중단합니다.

## 설치

대상은 x86_64 Ubuntu 24.04 계열입니다. 저장소 전체와 Python 3.10 이상이 필요합니다. `linux/` 폴더만 복사하지 마세요. Windows 패키지를 Ubuntu의 Python에 설치하지 않습니다.

```bash
git clone https://github.com/y5496694/kakao-cli.git
cd kakao-cli
bash linux/install.sh --install-system
```

`--install-system`을 명시한 경우에만 sudo로 Wine/i386, Xvfb, Openbox, 한국어 Tesseract OCR, 글꼴과 선택적인 로그인 화면 도구를 설치합니다. 프로그램 데이터는 사용자 전용 Wine prefix와 상태 폴더에 보관합니다. Python의 Windows 실행 파일은 python.org에서 받아 Wine 안에 설치하며 배포 파일을 이 저장소에 포함하지 않습니다. 기본 Python 3.11.9는 마지막 3.11 Windows 설치 프로그램을 선택한 것이며 최신 보안 릴리스라는 뜻은 아닙니다. `--python-installer`로 로컬 설치 파일을 지정할 수도 있습니다.

카카오톡 설치 파일은 [카카오 공식 다운로드 안내](https://pc.kakao.com/talk/notices/ko/2882?agent=win32)에서 받아 서버에 둡니다. CLI는 제3자 사이트에서 설치 파일을 받거나 계정·대화를 복사하지 않습니다.

대화형 설치가 필요한 파일은 서버의 가상 화면 안에서 설치합니다.

```bash
umask 077
mkdir -p "$HOME/.config/kakao-cli"
x11vnc -storepasswd "$HOME/.config/kakao-cli/vnc.pass"
chmod 600 "$HOME/.config/kakao-cli/vnc.pass"
bash linux/install.sh --installer /absolute/path/KakaoTalk_Setup.exe \
  --vnc-password-file "$HOME/.config/kakao-cli/vnc.pass"
```

설치 중 관리 화면은 인증이 필요한 `127.0.0.1:5903`에만 열립니다. 서버의 로컬 관리 환경이나 사용자가 구성한 VNC 프록시에서 접근하세요. 무인 설치 인자를 제공하는 설치 파일이라면 `--installer-arg`로 그 인자를 명시할 수 있습니다. 설치 종료 시 전용 prefix의 Wine 프로세스와 설치용 가상 화면을 정리합니다.

## 시작과 최초 로그인

```bash
# 설치된 실행기; ~/.local/bin을 PATH에 추가하거나 절대경로 사용
kakao-local runtime start
kakao-local runtime status

# 서버 가상 화면을 PNG로 받기: QR 코드 등 최초 인증 화면 확인
kakao-local runtime screenshot --output "$HOME/kakao-login.png"

# 필요할 때만 인증된 로컬 로그인 관리 화면 열기
kakao-local runtime login-view --password-file "$HOME/.config/kakao-cli/vnc.pass"
```

최초 카카오 계정 로그인과 모바일 인증은 사용자가 수행해야 합니다. 인증을 우회하거나 Windows PC의 로그인 토큰을 복사하지 않습니다. 저장된 로그인 상태는 이 서버의 전용 Wine prefix에 남습니다. 로그인 관리 화면을 닫으려면 runtime을 중지하고 다시 시작합니다.

기본 가상 화면은 `:93`이며 사용 중인 화면은 재사용하지 않습니다. Xvfb는 TCP를 열지 않고 Xauthority 쿠키로 보호합니다. `KAKAOCLI_WINEPREFIX`, `KAKAOCLI_STATE_DIR`, `KAKAOCLI_DISPLAY`, `KAKAOCLI_WINDOWS_PYTHON`, `KAKAOCLI_KAKAO_EXE`로 경로·가상 화면을 지정할 수 있습니다. 설치된 실행기는 `~/.config/kakao-cli/runtime.env`를 읽습니다.

## 조회·전송·첨부

```bash
kakao-local doctor --json
kakao-local rooms --json
kakao-local open '요청한 상대 이름' --exact --json
kakao-local messages '확인된 방 제목' --limit 30 --json
kakao-local search '확인된 방 제목' '회의' --json
kakao-local send '확인된 방 제목' '사용자가 승인한 문안' --dry-run --json
kakao-local send '확인된 방 제목' '사용자가 승인한 문안' --yes --json

kakao-local attachments '방 제목' --json
kakao-local capture '방 제목' --output "$HOME/chat.png" --json
kakao-local capture '방 제목' --scroll up --output "$HOME/chat-older.png" --json
kakao-local download '방 제목' --at 180 240 --kind image --output-dir "$HOME/kakao-files" --json
kakao-local download '방 제목' --at 260 340 --kind file --output-dir "$HOME/kakao-files" --json
kakao-local analyze "$HOME/kakao-files/received.pdf" --output-dir "$HOME/kakao-review" --json
```

좌표는 실제 capture 이미지를 열어 확인합니다. 예시 숫자를 그대로 사용하지 마세요. 사진은 썸네일, 파일은 **저장 버튼**을 선택합니다. 파일 본문이나 열기 버튼을 누르지 않습니다. 저장 대화상자는 전용 서버 가상 화면 안에서 처리되며 사용자 데스크톱에 표시되지 않습니다. 모달 저장창이 남으면 `download '방 제목' --dialog`로 먼저 완료합니다. 원본 파일 생성·크기 안정화·SHA256을 확인한 뒤 성공을 반환합니다.

닫힌 방은 서버의 채팅 검색 결과를 로컬 한국어/영어 Tesseract OCR로 읽습니다. 후보 표시 제목이 하나이고 실제 열린 창 제목과 완전히 같을 때만 사용합니다. 기존 검색어를 복원하며 모호한 결과는 중단합니다. 검색부터 최종 동작까지 프로세스 간 lock으로 직렬화합니다.

문안과 요청은 로컬 Wine 프로세스의 JSON 표준 입력으로 전달합니다. 사용자 문안을 셸 명령에 이어 붙이지 않습니다. `--message-file`과 출력 경로는 **Ubuntu 파일 경로**를 받으며 Wine의 Z: 경로로 변환합니다. 다운로드 결과도 서버 파일 경로로 돌려줍니다. 원본이 이미 서버에 있으므로 별도 fetch 전송이 없습니다.

읽기는 `loaded_ui_only`이며 전체 대화 이력을 뜻하지 않습니다. 기존 초안을 보존합니다. 실제 전송에는 `--yes`가 필요하며 `submitted_unconfirmed`는 서버 전달·수신 확인이 아닙니다. 오류·타임아웃 뒤에는 자동으로 재전송하지 마세요. 첨부 분석은 로컬에서 수행하며 사진 의미 해석은 반환된 이미지를 여는 어시스턴트가 수행합니다.

## 백그라운드 서비스

```bash
systemctl --user daemon-reload
systemctl --user enable --now kakao-cli.service
# 또는 설치 시 --enable-service 명시
kakao-local runtime stop
```

재부팅 후 사용자 로그인이 없어도 user service를 유지하려면 서버 관리자가 해당 계정의 systemd linger를 별도로 설정해야 합니다. 서비스 종료는 진행 중인 CLI 작업의 lock을 기다립니다. 일반 프로그램 시작 여부와 실제 카카오 계정 로그인 여부는 구분되며 runtime status가 실행 중이라는 사실만으로 로그인 성공을 보고하지 않습니다.

## 검증 범위

검색·대상 제목·기존 초안·모달 처리·원본 저장 완료·경로 변환·프로세스 수명·입력 격리는 모의 Win32 및 로컬 테스트로 검사합니다. 현재 검증 상태와 실제 앱 구동 범위는 저장소 [ANALYSIS.md](../ANALYSIS.md)에 기록합니다. Wine에서 카카오톡의 특정 버전이 실제 로그인되는지는 해당 서버에서 확인해야 합니다.

참고: [Ubuntu Xvfb 설명](https://manpages.ubuntu.com/manpages/noble/man1/Xvfb.1.html), [카카오 공식 다운로드 경로](https://www.kakaocorp.com/page/service/all).
