# macOS 전용 카카오톡 CLI

**이 버전은 Windows에서 단위 테스트와 소스 검사를 마쳤으며, 실제 macOS 카카오톡에서는 아직 검증하지 않았습니다.** Windows용 실행 파일·Win32·메모리 복호화 기능을 사용하지 않는 별도 어댑터입니다. 카카오톡이 Accessibility에 공개하는 컨트롤에 따라 지원 범위가 달라집니다.

실행 중인 카카오톡의 Accessibility 속성과 AXPress만 사용합니다. 앱 활성화, 최상단 전환, 전역 키 입력, 마우스 이동, 클립보드 변경을 요청하는 코드가 없습니다. 채팅방·사진 뷰어·저장창을 자동으로 열지 않습니다. 백그라운드에서 컨트롤을 제공하지 않는 카카오톡 버전은 오류를 반환합니다. OS 권한 안내창이나 카카오톡 자체의 저장·알림 동작까지 숨길 수 있다는 보장은 없습니다.

## 설치

macOS에서 Python 3.10 이상과 로그인된 PC 카카오톡이 필요합니다. 저장소 전체를 내려받고 다음을 실행합니다.

```sh
python3 -m venv .venv-macos
.venv-macos/bin/python -m pip install 'Pillow>=10' 'pypdf>=5' 'PyMuPDF>=1.24,<2' 'openpyxl>=3.1'
chmod +x macos/kakao
export KAKAO_PYTHON="$PWD/.venv-macos/bin/python"
./macos/kakao doctor
```

분석용 라이브러리는 `analyze` 명령에서만 필요합니다. 카카오톡 조회·전송에는 Python 표준 라이브러리와 macOS의 `/usr/bin/osascript`만 사용합니다. 루트 Windows 패키지를 macOS에 설치할 필요가 없습니다.

macOS가 요청하면 사용자가 직접 시스템 설정의 **개인정보 보호 및 보안 → 손쉬운 사용**에서 실행 호스트(터미널 또는 Codex)의 접근을 허용해야 합니다. **자동화 → System Events** 허용도 필요할 수 있습니다. CLI가 설정을 변경하지 않습니다. [Apple의 UI 스크립팅 안내](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/AutomatetheUserInterface.html)를 참고하세요.

## 조회와 전송

```sh
./macos/kakao windows
./macos/kakao rooms
./macos/kakao messages '홍길동' --limit 30
./macos/kakao attachments '홍길동'
./macos/kakao send '홍길동' '사진 확인했습니다'          # 미리보기, 입력하지 않음
./macos/kakao send '홍길동' '사진 확인했습니다' --yes    # 실제 AXPress 요청
./macos/kakao send '홍길동' --message-file ./message.txt --yes
```

방 제목은 항상 완전 일치해야 하며, 대상 방이 이미 독립 창으로 열려 있어야 합니다. `messages`와 `read`는 AX 트리에 공개된 텍스트만 반환합니다. 작성 중인 입력란은 제외하고, 발신자·시간·전체 이력은 추정하지 않습니다. `attachments`는 사진·파일 표시를 찾으며 다운로드 URL을 만들어 내지 않습니다.

전송에는 고유한 편집 가능한 입력란과 이름이 확인되는 **보내기/Send** 버튼이 필요합니다. 기존 초안이 있거나 버튼을 구분할 수 없으면 그대로 보존하고 중단합니다. 성공해도 `submitted_unconfirmed`이며 서버 전달·상대 수신은 확인하지 않습니다. 타임아웃·불명확한 실패 뒤에 전송을 자동 재시도하지 마세요.

## 사진과 파일 저장

사진·파일 다운로드의 **저장창은 사용자가 먼저 열어 놓아야 합니다**. 앱에 저장창·모달창이 남아 있으면 다른 방 읽기·전송을 차단하고 저장 처리부터 요구합니다. 이미 열린 저장창 한 개의 고유한 이름/경로 입력란과 **저장/Save** 버튼만 사용합니다.

```sh
# 현재 저장창에서 선택된 폴더에 저장: 먼저 미리보기
./macos/kakao download '홍길동' --filename 'photo.jpg'
./macos/kakao download '홍길동' --filename 'photo.jpg' --yes

# 전체 경로 또는 폴더 경로 AX 입력란이 공개된 저장창에 한해 지원
./macos/kakao download '홍길동' --output "$HOME/Downloads/photo.jpg" --yes

# 특정 방에 연결되지 않은 카카오톡 소유 저장 대화상자가 유일할 때
./macos/kakao download --filename 'received.pdf' --yes

./macos/kakao analyze "$HOME/Downloads/photo.jpg"
./macos/kakao analyze "$HOME/Downloads/received.pdf" --output-dir "$HOME/Downloads/kakao-review"
```

macOS 기본 저장창이 전체 경로 입력란을 제공하지 않으면 `--output`은 `path_control_unavailable`을 반환합니다. 폴더를 수동으로 선택한 뒤 `--filename`을 사용할 수 있습니다. 이 경우 선택된 폴더의 절대경로를 확인했다고 보고하지 않습니다. 덮어쓰기 확인창을 자동으로 승인하지 않습니다.

저장 버튼을 눌렀다는 사실은 다운로드 완료 증거가 아니므로 `save_requested_unconfirmed`를 반환합니다. 로컬 파일이 실제로 생긴 후 `analyze`를 실행하세요. 분석기는 이미지 경로·크기와 PDF 미리보기, 문서 추출 텍스트를 반환하고, 실제 사진 의미 해석은 그 이미지를 여는 어시스턴트가 수행합니다. OCR·모델 실행·클라우드 업로드는 하지 않습니다.

## 테스트

```sh
python3 -m pytest macos/tests -q
```

Python 테스트는 osascript를 모의 실행합니다. Node.js가 있으면 같은 JXA 로직을 모의 AX 트리로 실행해 기존 초안 보존·모달 차단·저장창 선택·실제 전송 플래그를 검사합니다. 이는 실제 macOS 권한과 카카오톡 컨트롤 호환성을 검증한 결과가 아닙니다.
