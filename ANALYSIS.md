# REA 카카오톡 분석 기록

대상: 이 PC에 설치·실행 중인 `C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe`.
도구: [morluto/rea](https://github.com/morluto/rea) npm `rea-agents` 6.8.0,
Node 24.22.1, REA를 통해 호출한 Rizin 0.9.1 및 Frida.
날짜: 2026-10-29, Asia/Seoul.

## 관찰된 근거

| 질문 | 관찰 | REA Evidence |
| --- | --- | --- |
| 분석 대상 식별 | 파일 버전 26.9.0.5337, 31,691,368 bytes, PE32+ AMD64 | `ev_aa8a05b12da0192efb6b338956d51dc405ad5134f213d00e3a73dcff9465be3c` |
| 파일 보호 구조 | `.vm_sec`, `.themida`, `.boot` 섹션이 존재. 지정한 UI 문자열의 정적 검색 결과는 비어 있었음 | `ev_8e00290dacae34c54caba36ba4109f135afbb0e5c947b57ad515b2a46e132b05` |
| 실행 중인 앱의 구조 | Frida가 `KakaoTalk.exe`, x64, 이미지 베이스 `0x140000000`을 반환 | `ev_13392033d60daa89fe53b38c2c7b0d6823bad7cb75f924b8991c879d33887f1c` |
| 입력창 선택 | 실제 자식창 `RICHEDIT50W`, control ID 1006, visible=true | 같은 런타임 Evidence |
| 대화 목록 선택 | 실제 자식창 `EVA_VH_ListControl_Dblclk`, control ID 100, visible=true | 같은 런타임 Evidence |
| 로드된 문자열 | `EVA_Window_Dblclk` RVA `0x2e6be48`, `RICHEDIT50W` RVA `0x271fa40`, `ChatRoomListView` RVA `0x295bfb8` 및 `0x295c1e0` | 같은 런타임 Evidence |
| 연결 해제 | `cleanup_error=null`, `messages_truncated=false` | 같은 런타임 Evidence |

정적 파일 SHA-256:
`5207ec0edffefdc4303fe0fbdf0bc3eaa584af61ffbc0bb1c15e1734e9ca9f5e`.
런타임 Evidence 자체의 subject는 null이며 실행 파일 digest가 묶여 있지 않습니다.
선택 PID 및 모듈 이름으로 연결했으며, 정적·런타임 바이트가 동일하다고 증명한 것은 아닙니다.

정적 탐색만으로 UI 문자열이 없다고 결론 내리지 않았습니다.
Rizin은 큰 영역의 기본 문자열 검사 한도 경고도 반환했습니다.
보호 섹션 및 런타임 문자열 관찰을 근거로 파일만으로 내부 UI 동작을 복원하는 데
제약이 있다고 판단했습니다. 보호 해제나 전체 내부 프로토콜 복원은 수행하지 않았습니다.

## 구현으로 연결한 사항

입력창은 실제 관찰한 ID 1006으로 선택합니다. 대화 목록은 visible=true인 ID 100으로
선택해 검색·참여자 목록을 잘못 복사하지 않게 했습니다. 이름·클래스만 신뢰하지 않고
창 소유 프로세스가 `KakaoTalk.exe`인지 검사합니다.

메시지 입력에는 다른 프로세스의 Edit 내용을 조회하는 `WM_GETTEXT`를 사용합니다.
기존 코드의 `GetWindowText` 빈 결과를 초안 부재로 취급하던 부분을 교체했습니다.
이 선택은 Win32 API 동작에 따른 구현 판단이며 REA로 메시지 전송을 관찰했다는 뜻은 아닙니다.

새 기능은 UI 대화 파싱·필터 검색, UTF-8 문안 파일/표준 입력, JSON 출력,
읽기 전용 전송 사전 점검과 대상·초안·포커스 보호입니다.

## 검증과 남은 범위

실제 앱에는 REA 읽기 관찰과 CLI 상태 조회·전송 사전 점검만 수행합니다.
회귀 테스트는 격리된 데이터와 모의 Win32 호출을 사용합니다.
실제 상대에게 메시지를 보내거나 사용자의 대화 본문을 테스트 자료로 저장하지 않습니다.
실발송과 클립보드 대화 복사는 사용자가 특정 대상 작업을 요청할 때 확인할 범위입니다.

`messages`, `search`, `read`는 열린 방에 로드된 UI 내용에 한정됩니다.
닫힌 방 자동 검색·전체 과거 대화 조회는 구현하지 않았습니다.
기반 코드의 DB·키 복구 명령은 유지했지만 이번 REA 조사에서 DB 복호화 성공을 검증하지 않았습니다.
Ghidra 준비 상태는 확인했지만 대상 전체의 Ghidra 디컴파일은 수행하지 않았습니다.

## 보관 및 재현

원본 Evidence는 로컬 `work/rea/*.json`에 있습니다. 이 디렉터리는 Git에서 제외합니다.
`scripts/rea-kakao.ps1`은 정적 분석을 재현하고, `-Runtime`은 읽기 관찰 스크립트
`scripts/rea-observe.js`를 REA로 로드한 뒤 해제합니다. 재실행 결과의 ID는 달라질 수 있습니다.

기반 CLI: [Lee-SiHyeon/kakaocli-win](https://github.com/Lee-SiHyeon/kakaocli-win), MIT.
이 로컬 확장은 해당 소스와 라이선스를 유지합니다.
