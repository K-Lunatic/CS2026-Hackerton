<p align="center">
  <img src="assets/turtleneck.png" width="180" alt="안경과 터틀넥을 쓴 터틀넥 캐릭터">
</p>

<h1 align="center">터틀넥 · Turtleneck</h1>

<p align="center">
  <strong>학교 자료를 모으고, 공부할 만큼 나누고, 시험처럼 풀어요.</strong><br>
  <sub>KKU TLS와 수업자료를 연결하는 ChatGPT / Codex 대학생활 스킬</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Codex-Skill-202124?style=flat-square" alt="Codex Skill">
  <img src="https://img.shields.io/badge/macOS%20%7C%20Windows-28654D?style=flat-square" alt="macOS와 Windows">
  <img src="https://img.shields.io/badge/local%20first-28654D?style=flat-square" alt="기기 우선 저장">
  <img src="https://img.shields.io/badge/hackathon%20prototype-BC8B36?style=flat-square" alt="해커톤 프로토타입">
</p>

<p align="center">
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="#무엇을-해주나요">핵심 기능</a> ·
  <a href="#자세히-보기">자세히 보기</a> ·
  <a href="#개발자-정보">개발자 정보</a>
</p>

---

## 터틀넥은 무엇인가요?

과제, 공지, 강의자료, 시험 준비가 여러 화면에 흩어져 있을 때 생기는 번거로움을 줄이는 **컴퓨터용 학습 스킬**입니다.

터틀넥은 학교 자료를 기기에 정리하고, 읽을 수 있는 자료만 작은 단위로 AI에 전달합니다. 문제를 만들고 풀 때는 채팅창이 아니라 전용 웹 시험지를 열며, 답안과 풀이 기록도 이어서 볼 수 있습니다.

핵심은 세 가지입니다.

| 학생 입장에서 | 터틀넥이 하는 일 |
| --- | --- |
| 무엇부터 해야 할지 모르겠을 때 | 과제·강의·공지·할 일을 과목별로 정리 |
| 할 일을 자꾸 놓칠 때 | 기기에서 주기적으로 새 과제·미시청 강의·마감을 확인하고 먼저 알려줌 |
| 자료는 있는데 공부가 막막할 때 | 핵심 개념을 먼저 정리하고 자료 근거 기반 문제 생성 |
| 문제를 풀고 실력이 남지 않을 때 | 웹 시험지, 자동 저장, AI 채점, 지난 풀이와 약한 문항 기록 |

> 현재 중심 사용 환경은 **데스크톱 Codex의 macOS·Windows**입니다. 모바일 기기와 자동 동기화하지 않고, 사용하는 컴퓨터 안에 자료를 보관합니다.

## 빠른 시작

### 1. 설치

데스크톱 Codex가 필요합니다. 직접 스크립트를 실행할 때는 Python 3.10 이상을 사용하지만, 아래의 macOS·Windows 실행기를 더블클릭하면 Python이 있으면 재사용하고 없으면 사용자 컴퓨터에 필요한 실행 환경을 자동으로 준비합니다.

```bash
codex plugin marketplace add K-Lunatic/CS2026-Hackerton --ref dev
codex plugin add university-agent@kku-university-agent-local
```

설치 후 새 Codex 대화에서 호출합니다.

```text
$turtleneck
```

로컬 시험 서버나 Actions 연결을 실행할 때는 `University Agent.command`(macOS) 또는 `University Agent.bat`(Windows)를 더블클릭하세요. 실행할 때 터틀넥 버전도 확인하고, 설치된 패키지에서 새 버전이 발견되면 자동으로 업데이트합니다. 개발 중인 Git 저장소는 자동으로 덮어쓰지 않습니다.

### 2. 처음 한 번 연결

처음 호출하면 이 컴퓨터의 브라우저에 한 번만 쓰는 터틀넥 연결 화면이 열립니다. TLS 아이디와 비밀번호를 그 화면에 입력하면 학교에서 확인한 뒤 macOS Keychain 또는 Windows 보호 저장소에만 저장합니다. 에브리타임을 함께 쓰려면 이어지는 공식 로그인 화면에서 로그인과 2차 인증을 직접 마칩니다.

비밀번호를 채팅이나 터미널에 입력하지 마세요. 이미 연결한 계정과 캐시는 다음 실행에서 재사용합니다.

### 3. 원하는 일을 말하기

```text
이번 주 미제출 과제 알려줘
내 시간표와 오늘 일정 알려줘
자료구조 3주차 자료로 문제 만들어줘
전에 풀었던 시험 보여줘
이 과제 지금까지 한 것 저장해줘
```

명령어를 외울 필요는 없습니다. 원하는 일을 말하면 필요한 선택만 안내합니다. 진행 기록을 최종 저장하거나 불러올 때는 화면에 안내된 확인 명령을 직접 보내야 합니다.

## 무엇을 해주나요?

| 기능 | 사용 예시 | 얻는 결과 |
| --- | --- | --- |
| 학사 정보 | “오늘 뭐 해야 해?” | 과목별 과제, 강의, 공지, 마감, 시청 상태 |
| 일반 할 일 | “TLS에 없는 과제도 추가해줘” | 학교에 없는 개인 과제까지 함께 관리 |
| 수업자료 | “자료구조 공지와 자료 보여줘” | 저장된 과목 자료와 원본 파일 확인 |
| 자료 학습 | “자료구조 공부 좀 해야겠다” | 핵심 개념을 먼저 익히고 다음 학습 선택 |
| 취약 개념 복습 | “약한 개념부터 복습 자료 보여줘” | 자료 분석·풀이 기록으로 중요도와 취약도를 정해 짧은 복습 자료 제공 |
| 시험지 | “자료구조 전체 자료로 10문제 만들어줘” | 전용 웹 시험지와 자동 저장 답안 |
| 채점 | “시험 채점해줘” | Codex의 기준별 AI 채점과 자료 근거 피드백 |
| 지난 기록 | “전에 풀었던 시험 다시 보여줘” | 당시 문제·답안·채점 결과를 읽기 전용으로 확인 |
| 학습 팩 | “학습 자료를 다른 기기로 옮겨줘” | 분석 결과·문제 세트·풀이 기록만 묶어 이동 |
| 에브리타임 | “내 시간표와 자료구조 강의평 보여줘” | 로컬에 저장한 시간표·선택한 강의평 조회 |

## 터틀넥이 편한 이유

- **한 번 읽은 자료는 다시 읽지 않음:** 파일 분석과 문항 후보를 저장해 다음 학습에서 재사용합니다.
- **원본을 잃지 않음:** 같은 이름의 파일도 서로 덮어쓰지 않고 원래 형식을 유지합니다.
- **시험처럼 풀 수 있음:** 채팅 퀴즈가 아니라 전용 웹 시험지에서 답하고 제출합니다.
- **풀이가 남음:** 답안, 채점, 헷갈린 문항, 전체 풀이 시간과 문항별 풀이 시간을 저장합니다.
- **근거 없는 출제를 줄임:** 실제로 읽지 못한 자료는 문제 근거로 사용하지 않습니다.
- **놓치는 일을 줄임:** 새 과제·임박한 마감·미시청 영상을 6시간마다 기기에서 확인하고, 바뀐 내용이 있을 때만 알려줍니다.
- **기기 안에서 끝남:** 별도 데이터베이스 서버나 모델 API 키 없이 로컬 저장소를 사용합니다.
- **필요한 것만 이동:** 학습 팩에는 원본 파일·TLS 비밀번호·쿠키를 넣지 않고 분석 결과와 학습 기록만 담습니다.

## 자세히 보기

아래 항목은 처음 사용하는 사람에게는 숨겨 두었지만, 동작 원리와 제한을 확인하려는 사용자를 위해 남겨 두었습니다.

<details>
<summary><strong>시험지와 학습 기능의 세부 동작</strong></summary>

### 자료에서 시험지까지

```text
범위 선택 → 파일별 읽기 → 핵심 개념·맥락 정리 → 유형별 문항 후보 저장
                                                   ↓
시험지 ← 저장된 후보 조합 ← 전체 범위 처리 확인
  ↓
답안 자동 저장 → 제출 → Codex 채점 → 점수·피드백·근거 표시
```

자료를 한 번에 AI에 넣지 않습니다. 긴 파일은 작은 본문 조각으로 나누고, 파일별 분석이 끝난 뒤 후보 문항을 조합합니다. 변경된 파일이나 출제 조건만 다시 처리합니다. 시험지가 완성되면 실행기가 전용 웹 화면을 자동으로 열며, 채팅에 문제를 평문으로 대신 내지 않습니다.

### 약한 개념을 다음 학습에 반영

저장된 파일별 분석의 개념·설명·근거와 이전 풀이의 오답·부분점수·힌트 사용·매칭 실패를 합쳐 개념별 중요도와 취약도를 계산합니다. 먼저 복습할 개념에는 저장된 설명과 원문 위치를 함께 보여주고, 다음 문제 생성에도 `learningFocus`로 전달합니다. 기록이나 근거가 없으면 추측하지 않고 확인 권장으로 남깁니다.

```text
python3 scripts/run_agent.py study-insights --course "자료구조"
```

지원하는 문제 유형은 다음과 같습니다.

| 유형 | 풀이 방식 |
| --- | --- |
| 객관식·n지선다 | 선택지와 OMR 선택 |
| 용어 단답형 | 용어 입력과 동의어 평가 |
| 서술형 | 핵심 의미·키워드·기준별 부분 점수 |
| 코드 오류 수정 | 예제 코드의 오류 수정 |
| 실행 결과 예측 | 코드 결과와 흐름 설명 |
| 순서 배열형 | 블록을 올바른 순서 칸에 배치 |
| 신규 유형 | 자료 특성에 맞는 텍스트·코드·선택 입력 유형 |

문항 수는 1~200개이며 객관식 선택지는 2~20개입니다. 한 유형만 반복하거나 여러 유형을 섞을 수 있고, 문제은행 셔플·파일/차시별 출제·문제 세트 분할·전체 개념 매칭판을 지원합니다. 매칭판은 양쪽을 따로 섞고, 첫 오매칭 때 한 번 자동으로 다시 섞습니다.

시험 화면에는 다음이 표시됩니다.

- 전체 스톱워치와 선택적 제한 시간 타이머
- 시간 초과 후에도 계속 풀 수 있는 초과 시간 표시
- 문항을 건드린 시각을 기준으로 계산한 문항별 풀이 시간
- 자동 저장과 새로고침 복구
- 채점 표시와 분리된 “헷갈렸어요” 눈 아이콘

시간 측정은 실제 시선 추적이 아니라 문항 클릭·답안 입력·OMR 선택 등 시험 화면 상호작용을 기준으로 합니다. 건너뛴 문항은 기록하지 않고, 돌아온 문항은 구간별 시간을 누적합니다.

정답·평가 기준·원문 인용은 채점 전 브라우저에 보내지 않습니다. 코딩 답안은 실행하지 않고 의미 기준으로 평가합니다.

</details>

<details>
<summary><strong>학습 팩 이동</strong></summary>

학습 팩은 `.tpack` 파일로 저장합니다. 과목별 분석 결과, 핵심 개념, 문제 세트, 풀이 기록과 선택한 학습 메모를 담을 수 있지만 원본 강의 파일·TLS 계정·쿠키는 포함하지 않습니다.

```text
python3 scripts/run_agent.py study-pack export --course "자료구조" --output ~/Desktop/data-structures.tpack
python3 scripts/run_agent.py study-pack import --input ~/Desktop/data-structures.tpack
```

학습 팩은 개인 학습 자료이므로 공유할 때는 파일을 받은 사람과 범위를 확인하세요. 가져온 기기에서는 원본 파일을 자동으로 내려받지 않으며, 필요한 경우 해당 파일을 직접 첨부해 모바일 분석을 요청할 수 있습니다.

모바일 학습 팩 경로는 구현을 남겨 두었지만 현재 발표 범위에서는 잠정 보류합니다. 기본 흐름은 데스크톱 Codex에서 진행하며, 모바일 경로를 사용할 때만 학습 게이트웨이를 실행한 컴퓨터가 켜져 있어야 합니다.

</details>

<details>
<summary><strong>읽을 수 있는 자료와 다운로드 제한</strong></summary>

- PDF, PPT/PPTX, HWP/HWPX, DOC/DOCX, TXT/MD와 Java·Python·C/C++ 등 텍스트·코드 파일을 지원합니다.
- 구형 문서는 운영체제에 맞는 변환 도구가 추가로 필요할 수 있습니다.
- PDF는 `pypdf`를 우선 사용하고, 필요하면 Poppler로 재시도합니다. Windows에서 Swift는 필요하지 않습니다.
- 파일당 10MB 이하를 처리하며, 이미지형 PDF의 OCR이나 손상·암호화 문서는 보장하지 않습니다.
- TLS 화면에 다운로드 금지가 명시됐거나 서버가 거부한 자료는 가져오거나 분석하지 않습니다.
- 읽지 못한 자료는 파일명만으로 추정하지 않으며, 나머지 읽을 수 있는 범위만 사용할 수 있습니다.
- 정상적으로 저장한 원본은 `resource-file` 요청으로 원래 파일 형식 그대로 받을 수 있습니다.

</details>

<details>
<summary><strong>저장 위치와 보안 경계</strong></summary>

터틀넥은 기기별 로컬 저장을 기본으로 합니다. 기기 간 자동 공유나 별도 서버 동기화는 제공하지 않습니다.

| 데이터 | 기본 저장 위치 |
| --- | --- |
| 학교 정보·일반 과제·진행 기록 | `~/.university-agent/university.db` |
| 파일 분석·문항·답안·시험 기록 | `~/.university-agent/study-sessions.db` |
| 내려받은 강의 원본 | `~/.university-agent/files/` |
| TLS 비밀번호 | macOS Keychain 또는 Windows DPAPI |
| TLS 쿠키 | 메모리에서만 사용 |

비밀번호는 SQLite·JSON 응답·로그에 넣지 않습니다. 다만 무제한 로컬 명령 실행 권한을 가진 AI 프로세스와 OS 보호 저장소가 완전히 격리된다고 보장하지는 않습니다. 출제와 채점에 쓰는 자료 본문·답안은 현재 대화의 AI에 전달됩니다.

에브리타임 계정·캐시와 TLS 계정·DB는 서로 분리합니다. 에브리타임 변경 기능은 미리보기와 사용자 승인 후에만 적용합니다.

</details>

<details>
<summary><strong>학사 자동 확인</strong></summary>

학교 연결을 마치면 macOS 또는 Windows의 예약 작업으로 6시간마다 TLS의 가벼운 변경 여부를 확인합니다. 새 과제, 마감 임박 과제, 아직 끝내지 않은 영상, 새 공지가 있을 때만 터틀넥이 과목명·기한·확인된 내용·바로 가기 링크를 알려줍니다.

자동 확인은 현재 컴퓨터의 로컬 DB만 갱신합니다. 컴퓨터가 꺼져 있거나 학교 연결이 만료되면 다음 실행 때 다시 확인하며, 기기 간 동기화는 하지 않습니다. 기존 설치에서 켜거나 끄려면 다음을 사용합니다.

```bash
python3 scripts/setup_turtleneck.py --watch-install
python3 scripts/setup_turtleneck.py --watch-uninstall
```

</details>

<details>
<summary><strong>버전 업데이트와 실행 환경</strong></summary>

터틀넥은 신뢰한 저장소의 패키지 버전을 주기적으로 확인합니다. 설치된 패키지에 새 버전이 있으면 사용자 데이터와 별도로 코드만 교체하며, Git으로 직접 관리하는 개발 저장소는 자동 업데이트하지 않습니다.

Python이 없는 일반 사용자는 실행기를 더블클릭하면 됩니다. 실행기는 기존 Python을 먼저 사용하고, 없을 때는 공식 `uv` 실행기를 통해 관리형 Python을 준비합니다. 사용자 비밀번호와 학교 DB는 실행 환경 설치 위치와 분리되어 있습니다.

</details>

<details>
<summary><strong>과제 진행 기록 명령</strong></summary>

```text
save "연습과제 - 배열, 구조체, 포인터"
save new "직접 받은 과제"
load "연습과제 - 배열, 구조체, 포인터"
list
```

- `save`는 TLS 과제, `save new`는 직접 등록한 과제에 연결합니다.
- 여러 문제를 한 대화에서 풀어도 선택한 문제의 진행 내용만 저장합니다.
- 후보가 겹치면 과목·마감으로 구분하며, 검색만으로 저장하지 않습니다.
- TLS 과제는 제출 완료가 확인될 때, 일반 과제는 사용자가 제출했다고 확인할 때 진행 기록을 제거합니다.

</details>

<details>
<summary><strong>ChatGPT 웹·모바일 Actions 경로</strong></summary>

이 저장소에는 선택적 ChatGPT Actions 게이트웨이도 포함되어 있습니다. 현재 중심 범위는 데스크톱 Codex이며, 모바일·Actions 경로는 잠정 보류된 보조 구현입니다. 이를 사용하려면 컴퓨터에서 게이트웨이·HTTPS 터널·Custom GPT OAuth/Actions를 별도로 설정해야 합니다.

Actions는 학교 정보·과제 진행 기록과 학습 팩 기반 학습 화면을 다룹니다. 모바일 학습 팩은 원본 강의 파일·비밀번호·쿠키를 포함하지 않으며, 분석 데이터와 저장된 문제만 가져옵니다. 실제 GPT 연결과 모바일 왕복은 별도 설정과 검증이 필요합니다.

자세한 설정은 [게이트웨이 안내](server/README.md)를 참고하세요.

</details>

## 개발자 정보

<details>
<summary><strong>개발 구조와 검증 결과 열기</strong></summary>

### 구조

```text
.
├── plugin.json
├── assets/                       터틀넥 아이콘
├── skills/university-agent/
│   ├── SKILL.md                  사용 지침
│   ├── providers/                TLS 연결·계정 보호
│   ├── storage/                  로컬 학사 DB
│   ├── features/                 학사 조회·자료 분석·시험지
│   ├── assets/                   시험지 HTML·CSS·JavaScript
│   ├── scripts/                  동기화·실행·시험 서버
│   ├── integrations/everytime/   에브리타임 하위 기능
│   ├── references/               데이터·기능 계약
│   └── tests/                    테스트
└── server/                       선택적 Actions 게이트웨이
```

기능 코드는 `features/`에 두고, TLS 접근은 `TLSProvider` 계약 뒤에 둡니다. 모든 기능은 공통 로컬 데이터와 로컬 학습 DB를 사용하며 TLS 엔드포인트를 직접 호출하지 않습니다.

### 테스트

```bash
python3 -m unittest discover -s skills/university-agent/tests -v
python3 -m unittest server.test_app server.test_local
node skills/university-agent/tests/test_exam_client.js
python3 skills/university-agent/integrations/everytime/scripts/self_check.py
```

2026-10-07 기준 로컬 테스트 **168개 중 167개 통과·1개 건너뜀**, 게이트웨이 **11개**, 시험 화면 점검 **4개**가 통과했습니다. 선택적 `pypdf`가 없어 해당 직접 실행 검사만 건너뛰었습니다.

상세 계약은 [스킬 사용 지침](skills/university-agent/SKILL.md), [학습·출제·채점 계약](skills/university-agent/references/study.md), [공통 데이터 모델](skills/university-agent/references/data-model.md), [TLS 제공자 계약](skills/university-agent/references/provider-contract.md)에서 확인할 수 있습니다.

### 로컬 저장소를 업데이트한 뒤

```bash
codex plugin add university-agent@kku-university-agent-local
```

설치본은 저장소의 사본이므로 업데이트 후 새 Codex 대화를 시작하세요.

</details>

---

<p align="center">
  <strong>한 번에 다 외우지 않아도 괜찮아요. 저장하고, 차근차근 이어가면 됩니다.</strong><br>
  <sub>README 구성은 <a href="https://github.com/DietrichGebert/ponytail">Ponytail</a>의 간결한 소개 방식에서 참고했습니다.</sub>
</p>
