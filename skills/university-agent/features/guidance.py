"""Plain-language discovery; never guesses targets or performs mutations."""
from __future__ import annotations

import re

from features.deadlines import format_deadline


def academic_list_answer(provider, user_id, kind, items):
    """Readable summaries of metadata; never display IDs or local file paths."""
    courses = {course['id']: course['name'] for course in provider.get_courses(user_id)}
    label = {'lectures': '미완료 강의', 'notices': '공지', 'resources': '수업자료'}[kind]
    synced = (provider.get_user(user_id) or {}).get('lastSyncedAt')
    lines = [f"저장된 학사 데이터 기준 · 마지막 TLS 동기화: {format_deadline(synced) if synced else '확인 불가'}",
             f'{label}: {len(items)}개']
    for item in items:
        line = f"{courses.get(item.get('courseId'), '과목 확인 필요')} / {item['title']}"
        if kind == 'lectures':
            line += f" — 시청률 {item['watchProgress']:g}% · 미완료"
            if item.get('availableUntil'):
                line += f" · 시청 기한 {format_deadline(item['availableUntil'])}"
        elif kind == 'notices':
            if item.get('publishedAt'):
                line += f" — 게시 {format_deadline(item['publishedAt'])}"
            content = (item.get('content') or '').strip()
            if content:
                line += '\n' + content[:200] + ('… (미리보기)' if len(content) > 200 else '')
        else:
            status = item.get('downloadStatus')
            line += ' — ' + {'DOWNLOADED': '내려받음', 'NOT_DOWNLOADED': '아직 내려받지 않음', 'PROHIBITED': '본문 이용 제한'}.get(status, '본문 확인 필요')
            if item.get('downloadReason'):
                line += f" · {item['downloadReason']}"
        lines.append(line)
    if not items:
        lines.append(f'현재 기록에는 {label}가 없어요. 최근 정보가 필요하면 TLS 새로고침을 요청해 주세요.')
    elif kind == 'resources':
        lines.append('복습할 과목과 자료명·주차를 알려주세요. 본문을 읽을 수 있는 자료로 진행합니다.')
    return '\n'.join(lines)


def usage_guide(text: str = "") -> dict:
    if re.search(r"과제|할\s*일|마감", text):
        examples = ["이번 주 안 낸 과제 알려줘", "새 과제 추가해줘", "하던 과제 어디까지 했지?"]
        intro = "남은 과제를 확인하거나, 새 과제를 적어 두거나, 하던 과제의 기록을 찾을 수 있어요."
    elif re.search(r"강의|영상|수업", text):
        examples = ["아직 안 본 강의 알려줘", "지금 내 상태 어때?"]
        intro = "덜 본 강의와 시청률, 학교에 표시된 시청 기한을 확인할 수 있어요."
    elif re.search(r"공부|복습|시험|퀴즈|문제|수업\s*자료", text):
        examples = ["자료구조 공부 좀 해야겠다", "이 자료로 객관식 5문제 만들어줘", "핵심 개념부터 정리해줘"]
        intro = "읽을 수 있는 수업자료를 바탕으로 핵심 개념을 정리하거나 연습문제를 만들 수 있어요."
    else:
        examples = ["오늘 뭐 해야 해?", "과제 진행 상황 저장해줘", "수업자료로 복습하고 싶어"]
        intro = "학교에서 할 일을 확인하고 정리하는 도우미예요. 기능 이름을 몰라도 평소 말하듯 요청하면 돼요."
    return {"toolCalls": [], "data": {"suggestions": examples}, "needsInput": True,
            "answer": intro + "\n예를 들면:\n" + "\n".join(f"• {example}" for example in examples) + "\n원하는 일을 한 문장으로 말해 주세요."}


def guidance_request(text: str) -> dict | None:
    if re.search(r"새로\s*고침|동기화|(?:TLS|학교|계정).{0,12}(?:연결|로그인)|(?:최신|다시).{0,8}(?:TLS|학사|제출|시청).{0,12}(?:확인|조회)", text, re.I):
        return {"toolCalls": [], "needsSync": True, "data": {"intent": "sync", "performed": False},
                "nextCommands": ["python3 scripts/sync_tls.py --connect"],
                "answer": "터틀넥 연결창을 열어드릴게요. 처음이라면 열린 터미널에 아이디와 비밀번호를 입력해 주세요. 비밀번호는 화면과 채팅에 표시되지 않아요."}
    if re.search(r"과제\s*(?:id|아이디)|저장.{0,12}(?:방법|어떻게)|(?:어떻게|방법).{0,12}저장", text, re.I):
        return {"toolCalls": [], "needsInput": True, "data": {"intent": "find-assignment"},
                "answer": "‘과제 진행 상황 저장해줘’라고 말하면 대화 속 단서로 과제를 찾아드려요. 후보를 고르거나 직접 이름을 정한 뒤, 안내된 저장 명령을 한 번 보내면 됩니다. 과제 ID는 필요 없어요."}
    if re.search(r"도움말|사용법|사용\s*방법|어떻게\s*(?:써|쓰|사용)|뭘\s*할\s*수|뭐\s*할\s*수|무슨\s*기능|처음이|처음\s*(?:써|사용)|기능.*알려", text):
        return usage_guide(text)
    if re.search(r"(?:과제|할\s*일).{0,20}(?:등록|추가)|(?:등록|추가).{0,20}(?:과제|할\s*일)", text):
        return {"toolCalls": [], "data": {"intent": "assignment-add", "requiredFields": ["title"], "optionalFields": ["courseId", "dueAt", "description"]}, "needsInput": True,
                "answer": "새로 적어 둘 과제 제목이 무엇인가요? 예: ‘독서 보고서’. 과목과 마감일은 알고 있으면 함께 말해 주세요."}
    if re.search(r"(?:과제|할\s*일).{0,20}(?:삭제|지워|완료\s*처리)|(?:삭제|지워).{0,20}(?:과제|할\s*일)", text):
        return {"toolCalls": [], "data": {"intent": "select-assignment"}, "needsInput": True,
                "answer": "어떤 과제인지 제목을 알려주세요. 직접 추가한 과제는 완료 표시하거나 지울 수 있어요. 학교 과제의 제출 상태는 학교에서 확인해 반영해요."}
    return None


def is_status_request(text: str) -> bool:
    return bool(re.search(r"컨텍스트|전체|상태|(?:지금|현재)(?:은|는)?.{0,12}어때|나[.\s…]*어때|할\s*일|해야\s*할\s*일|마감\s*목록|(?:오늘|지금|다음).{0,12}(?:뭐|무엇|뭘).{0,8}(?:해|하)|(?:뭐|무엇)부터.{0,8}(?:해|하)|급한.*(?:것|거|일)", text))
