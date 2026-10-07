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
| 자료는 있는데 공부가 막막할 때 | 핵심 개념을 먼저 정리하고 자료 근거 기반 문제 생성 |
| 문제를 풀고 실력이 남지 않을 때 | 웹 시험지, 자동 저장, AI 채점, 지난 풀이와 약한 문항 기록 |

> 현재 중심 사용 환경은 **데스크톱 Codex의 macOS·Windows**입니다. 모바일 기기와 자동 동기화하지 않고, 사용하는 컴퓨터 안에 자료를 보관합니다.

## 빠른 시작

### 1. 설치

데스크톱 Codex와 Python 3.10 이상이 필요합니다. 저장소의 `dev` 브랜치를 플러그인 마켓플레이스로 등록합니다.

```bash
codex plugin marketplace add K-Lunatic/CS2026-Hackerton --ref dev
codex plugin add university-agent@kku-university-agent-local
```

설치 후 새 Codex 대화에서 호출합니다.

```text
$turtleneck
```

### 2. 처음 한 번 연결

처음 호출하면 열린 연결 창에서 TLS 아이디와 숨김 비밀번호를 입력합니다. 에브리타임을 함께 쓰려면 이어지는 공식 로그인 화면에서 로그인과 2차 인증을 직접 마칩니다.

비밀번호를 채팅에 보내지 마세요. 이미 연결한 계정과 캐시는 다음 실행에서 재사용합니다.

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
| 시험지 | “자료구조 전체 자료로 10문제 만들어줘” | 전용 웹 시험지와 자동 저장 답안 |
| 채점 | “시험 채점해줘” | Codex의 기준별 AI 채점과 자료 근거 피드백 |
| 지난 기록 | “전에 풀었던 시험 다시 보여줘” | 당시 문제·답안·채점 결과를 읽기 전용으로 확인 |
| 에브리타임 | “내 시간표와 자료구조 강의평 보여줘” | 로컬에 저장한 시간표·선택한 강의평 조회 |

## 터틀넥이 편한 이유

- **한 번 읽은 자료는 다시 읽지 않음:** 파일 분석과 문항 후보를 저장해 다음 학습에서 재사용합니다.
- **원본을 잃지 않음:** 같은 이름의 파일도 서로 덮어쓰지 않고 원래 형식을 유지합니다.
- **시험처럼 풀 수 있음:** 채팅 퀴즈가 아니라 전용 웹 시험지에서 답하고 제출합니다.
- **풀이가 남음:** 답안, 채점, 헷갈린 문항, 전체 풀이 시간과 문항별 풀이 시간을 저장합니다.
- **근거 없는 출제를 줄임:** 실제로 읽지 못한 자료는 문제 근거로 사용하지 않습니다.
- **기기 안에서 끝남:** 별도 데이터베이스 서버나 모델 API 키 없이 로컬 저장소를 사용합니다.

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

자료를 한 번에 AI에 넣지 않습니다. 긴 파일은 작은 본문 조각으로 나누고, 파일별 분석이 끝난 뒤 후보 문항을 조합합니다. 변경된 파일이나 출제 조건만 다시 처리합니다.

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

문항 수는 1~200개이며 객관식 선택지는 2~20개입니다. 한 유형만 반복하거나 여러 유형을 섞을 수 있고, 문제은행 셔플·파일/차시별 출제·문제 세트 분할·4×4 개념 매칭판도 지원합니다.

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

이 저장소에는 선택적 ChatGPT Actions 게이트웨이도 포함되어 있습니다. 기본 사용 경로는 데스크톱 Codex이며, Actions를 사용하려면 컴퓨터에서 게이트웨이·HTTPS 터널·Custom GPT OAuth/Actions를 별도로 설정해야 합니다.

Actions는 학교 정보·과제 진행 기록을 다루지만 로컬 강의 파일 본문과 로컬 시험 세션을 자동으로 제공하지 않습니다. 실제 GPT 연결과 모바일 왕복은 별도 설정과 검증이 필요합니다.

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
