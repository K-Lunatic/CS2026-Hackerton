<p align="center">
  <img src="assets/turtleneck.png" width="200" alt="안경과 터틀넥을 쓴 터틀넥 캐릭터">
</p>

<h1 align="center">터틀넥 · Turtleneck</h1>

<p align="center">
  <strong>과제는 놓치지 않게. 공부는 차근차근. 하던 일은 이어서.</strong><br>
  <sub>KKU TLS와 수업자료를 연결하는 ChatGPT / Codex 대학생활 스킬</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Codex-Skill-202124?style=flat-square" alt="Codex Skill">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-28654D?style=flat-square" alt="macOS와 Windows용">
  <img src="https://img.shields.io/badge/storage-local%20SQLite-28654D?style=flat-square" alt="로컬 SQLite 저장">
  <img src="https://img.shields.io/badge/status-hackathon%20prototype-BC8B36?style=flat-square" alt="해커톤 프로토타입">
</p>

<p align="center">
  <a href="#빠른-시작">설치하기</a> ·
  <a href="#이렇게-말해보세요">사용 예시</a> ·
  <a href="#자료에서-시험지까지">시험 만들기</a> ·
  <a href="#데이터와-보안">데이터와 보안</a> ·
  <a href="#개발과-검증">개발 안내</a>
</p>

---

강의실마다 과제와 공지를 확인하고, 자료를 내려받고, 지난번 어디까지 풀었는지 찾는 일.

터틀넥은 그 준비를 돕습니다. 저장된 학교 정보를 정리하고, 읽을 수 있는 자료를 AI에 연결하고,
하던 과제의 진행 내용을 남깁니다. 문제를 풀 때는 채팅창 대신 **로컬 시험지와 OMR 답안지**를 엽니다.

> **현재 중심 사용 환경은 컴퓨터에서 실행하는 Codex입니다.**
> 학교 정보 자동 수집·로컬 파일 분석·시험지·AI 채점은 같은 컴퓨터에서 이어집니다.
> 별도 모델 API 키나 외부 DB 서비스는 필요하지 않습니다.

## 이렇게 말해보세요

| 하고 싶은 일 | 터틀넥에게 말하기 | 받을 수 있는 도움 |
| --- | --- | --- |
| 오늘 할 일 확인 | “오늘 뭐 해야 해?” | 마감이 지난 과제, 다음 마감, 남은 강의 |
| 과제·영상 관리 | “이번 주 안 낸 과제 알려줘” | 과목별 마감·제출 상태·시청률·표시된 시청 기간 |
| 일반 과제 등록 | “할 일 추가해줘” | TLS에 없는 과제도 제목부터 등록 |
| 공지·자료 확인 | “자료구조 공지랑 자료 보여줘” | 저장된 공지 본문과 자료 목록 |
| 공부 시작 | “자료구조 공부 좀 해야겠다” | 실제 읽은 자료의 핵심 개념 정리 후 문제 풀이 제안 |
| 시험 만들기 | “자료구조 전체 자료로 10문제 만들어줘” | 파일별 분석, 시험지, 답안 저장과 AI 채점 |
| 하던 과제 저장 | “이 과제 지금까지 한 것 저장해줘” | 대화의 풀이 범위 유지 → 과제 선택 → 저장 명령 안내 |
| 이어서 풀기 | `load "과제명"` | 저장된 진행 내용·막힌 부분·다음 행동 |

명령어를 외우지 않아도 됩니다. 원하는 일을 말하면 필요한 선택만 안내합니다.
다만 진행 기록의 **최종 저장·불러오기·목록 조회**는 안내받은 명령을 직접 보내야 실행됩니다.

## 빠른 시작

### 1. 플러그인 설치

**준비물:** Codex 실행 환경, Python 3.10 이상, 최초 학교 연결에 사용할 KKU TLS 계정.
자료 변환 도구는 파일 형식에 따라 추가로 필요할 수 있습니다.

터미널 또는 Windows PowerShell에서 `dev` 브랜치를 등록합니다.

```bash
codex plugin marketplace add K-Lunatic/CS2026-Hackerton --ref dev
codex plugin add university-agent@kku-university-agent-local
```

설치 후 **새 Codex 대화**에서 호출하세요.

```text
$turtleneck
뭘 할 수 있어?
```

기능 안내는 학교 로그인 없이 볼 수 있습니다.
이 저장소는 개발용 플러그인 마켓플레이스이며 공개 Plugins Directory에 게시된 상태는 아닙니다.

### 2. 학교 계정 연결

대화에서 **“TLS 연결해줘”**라고 요청하세요.
최초 연결은 이 컴퓨터의 터미널 숨김 입력으로 진행하고, 이후에는 저장된 계정을 재사용합니다.
비밀번호를 채팅에 보내지 마세요.

<details>
<summary><strong>저장소에서 직접 설치하거나 첫 동기화를 실행하려면</strong></summary>

```bash
git clone --branch dev https://github.com/K-Lunatic/CS2026-Hackerton.git
cd CS2026-Hackerton
codex plugin marketplace add .
codex plugin add university-agent@kku-university-agent-local
```

macOS:

```bash
python3 skills/university-agent/scripts/sync_tls.py
```

Windows:

```powershell
py -3 skills/university-agent/scripts/sync_tls.py
```

입력창을 표시할 수 없는 실행 환경에서는 별도 터미널에서 첫 연결을 진행해야 합니다.
스킬 폴더만 설치하려면 `skills/university-agent`를 현재 사용자의 Codex Skills 디렉터리에 복사하세요.
플러그인 설치와 스킬 단독 설치는 대안이므로 둘 다 설치할 필요는 없습니다.

</details>

### 3. 원하는 일을 말하기

```text
이번 주 미제출 과제 알려줘
자료구조 3주차 자료로 10문제 만들어줘
시험 채점해줘
```

학교 정보는 **마지막 동기화 기준**입니다. 최신 제출·시청 상태가 필요하면
“TLS 새로고침해줘”라고 요청하세요.

> **저장 폴더 접근 승인이 뜨면**
> 기본 데이터 위치는 Codex의 기본 쓰기 허용 범위 밖일 수 있습니다.
> 필요한 저장 명령에만 승인한 뒤 이어갑니다. 과제를 다시 찾거나 저장 위치를 바꿀 필요는 없습니다.
> 권한 승인 기능이 없는 환경에서는 저장을 진행할 수 없습니다.

## 자료에서 시험지까지

**원문 전체를 한 번에 기억시키지 않고, 작은 분석 결과를 저장해 시험지를 엮습니다.**

```text
범위 선택 → 파일별 본문 읽기 → 주요 내용·맥락 정리 → 유형별 문항 후보 저장
                                                     ↓
시험 화면 ← 전체 범위 처리 후 후보 조합 ← 저장된 후보를 작은 목록으로 검토
    ↓
답안 작성·자동 저장 → 제출 → 현재 Codex가 채점 → 화면에 점수·피드백 표시
```

- **부분별 분석:** AI에 전달하는 본문은 한 번에 최대 8,000자. 긴 파일은 나누어 처리합니다.
- **구분된 데이터:** 주요 학습 내용, 수업 맥락, 적합한 시험 유형과 이유, 문항 후보를 따로 기록합니다.
- **전체 범위 확인:** 선택한 파일 처리가 모두 끝난 뒤 저장된 후보로 시험지를 조합합니다.
- **중단 후 재개:** 같은 대화의 저장된 분석 지점부터 이어갈 수 있습니다.
- **출처 연결:** 페이지·슬라이드·줄과 원문 인용을 검증합니다. 읽지 못한 내용은 출제 근거로 쓰지 않습니다.

### 과목에 맞는 문제, 시험처럼 푸는 화면

| 유형 | 답하는 방식 |
| --- | --- |
| 객관식 · n지선다 | 선택지 표시와 OMR 선택 |
| 용어 단답형 | 용어 입력, 동의어를 고려한 평가 |
| 서술형 | 핵심 의미·키워드와 평가 기준별 부분 점수 |
| 코드 오류 수정 | 예제 코드의 오류 수정 답안 작성 |
| 실행 결과 예측 | 주어진 코드의 결과·흐름 설명 |
| 과목별 신규 유형 | 자료에 맞는 유형 이름과 텍스트·코드·선택 입력 정의 |

시험은 1~20문항, 객관식은 2~20개 선택지를 지원합니다.
문제·답안은 로컬 SQLite에 저장하고, 정답과 평가 기준은 채점 전 브라우저에 전달하지 않습니다.
답안은 입력 후 자동 저장되며 새로고침으로 복구됩니다.

**채점하는 AI는 현재 대화의 Codex입니다.** 브라우저가 별도 모델 API를 호출하지는 않습니다.
제출 후 채점이 시작되지 않으면 대화에서 **“시험 채점해줘”**라고 요청하세요.
코딩 답안은 의미적으로 검토하며 코드를 실행하지 않습니다.

<details>
<summary><strong>읽을 수 있는 파일과 제한</strong></summary>

- PDF, PPT/PPTX, HWP/HWPX, DOC/DOCX, TXT/MD와 Java·Python·C/C++ 등 지원되는 텍스트·코드 파일.
- 구형 HWP/DOC/PPT는 해당 변환 도구가 있어야 읽을 수 있습니다.
- macOS PDF 추출은 PDFKit, Windows PDF 추출은 Poppler의 `pdftotext`를 사용합니다.
- 파일당 10MB 이하를 처리합니다. 이미지형 PDF의 OCR, 손상·암호화 문서는 지원을 보장하지 않습니다.
- 직접 첨부 파일의 로컬 분석 경로는 PDF/PPTX/PPT/TXT/MD입니다. 학교 자료 읽기와 지원 범위가 다릅니다.
- TLS의 명시적 다운로드 금지나 서버 거부가 있으면 파일을 가져오거나 분석하지 않습니다.
- 문서 뷰어 HTML은 강의 파일 본문으로 쓰지 않습니다. `viewer.php`나 `forcedownload=0`만으로 금지라고 단정하지 않습니다.
- 금지·읽기 실패 자료는 사유를 알리고, 제외 동의 후 나머지 범위로 시험지를 만들 수 있습니다.

작은 단위로 나누면 컨텍스트 부담과 재작업은 줄일 수 있습니다.
다만 구조·인용 검증만으로 정답의 의미, 선택지의 유일성, 출제 품질을 보장하지는 않습니다.
실제 기출이나 학교 시험 예측이 아니라 **수업자료 기반 연습 시험**입니다.

</details>

## 하던 과제 이어가기

저장 요청 → 대화 속 단서로 과제 검색 → 후보 선택 → 최종 명령 → 진행 요약 저장.

```text
save "연습과제 - 배열, 구조체, 포인터"
save new "직접 받은 과제"
load "연습과제 - 배열, 구조체, 포인터"
list
```

- `save`는 TLS 과제, `save new`는 직접 등록한 과제에 연결합니다.
- 여러 문제를 한 대화에서 풀어도 선택한 문제의 진행 내용만 요약합니다.
- 후보가 겹치면 과목·마감으로 구분하며, 검색만으로 저장하지 않습니다.
- 요약은 Codex가 대화에서 작성합니다. 사용자는 내부 ID나 요약 필드를 입력하지 않습니다.
- `list`는 저장된 미완성 과제 목록입니다. 최종 명령을 정확히 보낼 때만 기록을 읽거나 씁니다.
- TLS 과제는 동기화에서 제출 완료가 확인될 때, 일반 과제는 사용자가 제출 여부에 정확히 `예`라고 답해 완료 처리할 때 진행 기록을 제거합니다.

## 데이터와 보안

**기기에 저장되지만, “로컬이니까 보안 걱정이 없다”는 뜻은 아닙니다.**

| 데이터 | 저장·처리 위치 |
| --- | --- |
| 학교 정보·일반 과제·진행 기록 | `~/.university-agent/university.db` |
| 파일 분석·문항 후보·시험·답안 | `~/.university-agent/study-sessions.db` |
| 내려받은 강의 파일 | `~/.university-agent/files/` |
| 계정 ID | 로컬 계정 설정 파일 |
| 비밀번호 | macOS Keychain 또는 Windows DPAPI |
| 출제·채점용 본문과 답안 | 현재 대화의 AI에 전달되는 읽은 범위와 답안 |

기기 간 공유·자동 동기화는 없습니다. `UNIVERSITY_AGENT_DB`로 다른 로컬 저장 경로를
지정할 수 있지만, 권한 오류를 피하려고 사용자 동의 없이 DB를 옮기지는 않습니다.

비밀번호는 SQLite·JSON 응답·로그로 반환하지 않으며 TLS 쿠키는 메모리에서만 사용합니다.
다만 무제한 로컬 명령 실행 권한이 있는 AI 프로세스와 OS 비밀 저장소가 완전히 격리된다고
보장하지는 않습니다. 원본 파일이 로컬에 남아도 출제·채점에 쓰는 텍스트는 AI에 전달됩니다.

<details>
<summary><strong>ChatGPT Actions 연결은 별도 경로입니다</strong></summary>

ChatGPT 웹·모바일용 게이트웨이 코드도 포함돼 있습니다.
학교 정보 자동 수집과 로컬 시험 흐름의 기본 사용 경로는 위의 **로컬 Codex**입니다.

Actions를 사용하려면 컴퓨터에서 게이트웨이를 실행하고 HTTPS 터널·Custom GPT의
OAuth/Actions를 별도로 설정해야 합니다.
Mac은 [University Agent.command](University%20Agent.command),
Windows는 [University Agent.bat](University%20Agent.bat)을 사용합니다.

현재 Actions는 학교 정보·과제 진행 기록을 다루지만 강의 파일 본문이나 로컬 시험 세션을
제공하지 않습니다. 직접 첨부한 자료의 ChatGPT 학습은 별도 제품 기능이며,
로컬 분석 검증기와 연결되지 않습니다. 실제 GPT 연결·모바일 왕복은 미검증입니다.

설정·허용 범위는 [게이트웨이 안내](server/README.md)를 참고하세요.

</details>

## 개발과 검증

로컬 실행기는 Python 표준 라이브러리 기반입니다. TLS 담당자는 제공자 계약을 유지하고,
기능 담당자는 저장된 공통 데이터를 사용합니다.

```text
.
├── assets/                       터틀넥 아이콘
├── plugin.json                   플러그인 정보
├── skills/university-agent/
│   ├── SKILL.md                  AI의 사용 지침
│   ├── providers/                TLS 연결·계정 보호
│   ├── storage/                  로컬 학사 DB
│   ├── features/                 과제·강의·진행 기록·단계별 출제
│   ├── assets/                   시험지 HTML·CSS·JavaScript
│   ├── scripts/                  동기화·실행·시험 서버
│   ├── references/               기능·데이터 계약
│   └── tests/                    계정과 분리된 테스트
└── server/                       선택적 ChatGPT Actions 게이트웨이
```

### 테스트

```bash
python3 -m unittest discover -s skills/university-agent/tests -v
python3 -m unittest server.test_app server.test_local
```

2026-10-02 기준 로컬 테스트 **97개 통과**. 임시 DB에서 단계별 파일 분석·재개·인용 검사·
시험지 API·답안 저장·채점 상태를 검증했습니다. Safari에서는 테스트용 문항의 제출·채점 결과 표시도 확인했습니다.
Windows 분기는 모의 테스트이며 실제 Windows 브라우저와 실제 학교 자료의 AI 출제 품질은
아직 검증하지 않았습니다. 과거 검증 이력과 제한은 [검증 기록](skills/university-agent/references/project-tests.md)에 있습니다.

### 업데이트

설치본은 저장소의 사본입니다. 저장소만 수정해도 실행 중인 스킬이 즉시 바뀌지는 않습니다.
로컬 저장소를 마켓플레이스로 등록했다면 최신 코드를 받은 뒤 다음 명령으로 다시 설치하고
새 Codex 대화를 시작하세요.

```bash
codex plugin add university-agent@kku-university-agent-local
```

### 더 알아보기

- [스킬 사용 지침](skills/university-agent/SKILL.md)
- [학습·출제·채점 계약](skills/university-agent/references/study.md)
- [공통 데이터 모델](skills/university-agent/references/data-model.md)
- [TLS 제공자 계약](skills/university-agent/references/provider-contract.md)
- [입력 폼·비밀값 처리](skills/university-agent/references/form-pattern.md)
- [문제 신고·개선 제안](https://github.com/K-Lunatic/CS2026-Hackerton/issues)

---

<p align="center">
  <strong>한 번에 다 외우지 않아도 괜찮아요. 저장하고, 차근차근 이어가면 됩니다.</strong><br>
  <sub>README 구성은 <a href="https://github.com/DietrichGebert/ponytail">Ponytail</a>의 로고 중심 소개와 짧은 설치·사용 안내에서 참고했습니다.</sub>
</p>
