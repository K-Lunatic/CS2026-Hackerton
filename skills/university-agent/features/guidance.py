"""Plain-language discovery; never guesses targets or performs mutations."""
from __future__ import annotations

import re

from features.deadlines import format_deadline


def resource_unavailable_reason(item):
    reason = str(item.get('downloadReason') or '')
    if '뷰어' in reason or 'HTML' in reason:
        return '자료를 보는 화면만 열려 원본 파일은 받지 못했어요.'
    if reason.startswith('TLS 서버가'):
        return '학교에서 이 자료의 내려받기를 막아 두어 제외했어요.'
    if item.get('downloadStatus') == 'PROHIBITED':
        return '다운로드 금지 자료라 이번에는 읽지 않고 제외했어요.'
    if '목록만' in reason:
        return '이번에는 자료 목록만 확인했어요. 원본은 아직 받지 않았어요.'
    return '원본 파일은 아직 받지 못했어요.'


def academic_list_answer(provider, user_id, kind, items):
    """Readable summaries of metadata; never display IDs or local file paths."""
    courses = {course['id']: course['name'] for course in provider.get_courses(user_id)}
    label = {'lectures': '미완료 강의', 'notices': '공지', 'resources': '수업자료'}[kind]
    synced = (provider.get_user(user_id) or {}).get('lastSyncedAt')
    lines = [f"저장된 학교 정보 기준 · 마지막 확인: {format_deadline(synced) if synced else '확인 불가'}",
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
                line += ' · ' + resource_unavailable_reason(item)
        lines.append(line)
    if not items:
        lines.append(f'현재 기록에는 {label}가 없어요. 최신 정보가 필요하면 학교 정보 새로고침을 요청해 주세요.')
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
        examples = ["자료구조 주요 내용 학습시켜줘", "이 자료로 객관식 5문제 만들어줘", "시험 정보 저장해줘"]
        intro = "읽을 수 있는 수업자료를 바탕으로 핵심 개념을 정리하거나 연습문제를 만들 수 있어요."
    else:
        examples = ["오늘 뭐 해야 해?", "과제 진행 상황 저장해줘", "수업자료로 복습하고 싶어"]
        intro = "학교에서 할 일을 확인하고 정리하는 도우미예요. 기능 이름을 몰라도 평소 말하듯 요청하면 돼요."
    return {"toolCalls": [], "data": {"suggestions": examples}, "needsInput": True,
            "answer": intro + "\n예를 들면:\n" + "\n".join(f"• {example}" for example in examples) + "\n원하는 일을 한 문장으로 말해 주세요."}


def guidance_request(text: str) -> dict | None:
    if re.search(r'터틀넥.{0,12}(?:시작|설정|연결)|처음\s*설정|(?:학교|TLS).{0,12}(?:에타|에브리타임).{0,12}(?:연결|설정)', text, re.I):
        return {'toolCalls': [], 'needsSetup': True, 'data': {'intent': 'setup', 'performed': False},
                'nextCommands': ['python3 scripts/setup_turtleneck.py'],
                'answer': '학교 자료와 에타 시간표·강의평을 한 번에 준비할게요. 로그인은 열린 창에서 직접 해주세요. 이미 연결된 항목은 다시 로그인하지 않아요.'}
    if re.search(r'개념\s*(?:매칭|연결)|매칭판|짝\s*맞추', text):
        return {'toolCalls': [], 'needsConceptMatch': True,
                'answer': '저장된 문제은행에서 개념과 설명을 골라 4×4 매칭판을 열어드릴게요. 끝난 뒤 헷갈린 개념은 다음 문제에서 먼저 복습합니다.'}
    if re.search(r'(?:약한|취약|헷갈린|중요한|핵심).{0,20}(?:개념|내용)|(?:개념|내용).{0,20}(?:중요도|취약도|복습 자료|학습 자료)', text):
        return {'toolCalls': [], 'needsConceptInsights': True,
                'nextCommands': ['python3 scripts/run_agent.py study-insights --course "<과목명>"'],
                'answer': '자료 분석과 지금까지의 풀이 기록을 살펴서, 중요한 개념과 먼저 복습할 개념을 나눠 학습 자료로 정리할게요.'}
    if re.search(r'(?:단계별|차근차근|기초부터|개념부터|수준에 맞춰|통달|마스터).{0,30}(?:공부|학습|문제|시험|복습)|(?:개념|기초).{0,20}(?:문제|평가).{0,20}(?:다시|높|심화)', text):
        return {'toolCalls': [], 'needsAdaptiveStudy': True,
                'answer': '먼저 자료의 핵심 개념을 정리하고, 짧은 확인 문제부터 시작할게요. 채점 결과에 따라 헷갈린 개념과 아직 풀지 않은 유형을 다음 학습에 우선 반영하겠습니다.'}
    if re.search(r'(?:문제|시험).{0,16}(?:세트|여러\s*번|나눠|나눠서).{0,20}(?:풀|선택|골라)', text) or re.search(r'(?:세트|문제지).{0,16}(?:하나씩|한\s*세트씩)', text):
        return {'toolCalls': [], 'needsExamSets': True, 'nextCommands': ['python3 scripts/run_agent.py exam-history'],
                'answer': '저장된 문제를 여러 세트로 나눠 웹에서 원하는 세트부터 하나씩 풀 수 있게 열어드릴게요.'}
    if re.search(r'(?:지난|이전|예전|과거|풀었던).{0,20}(?:시험|문제)', text) and re.search(r'확인|기록|내역|보여|열어|다시\s*봐|돌아보기', text):
        return {'toolCalls': [], 'needsExamHistory': True, 'nextCommands': ['python3 scripts/run_agent.py exam-history'],
                'answer': '풀었던 시험지를 찾아볼게요. 저장된 문제와 당시 답안·채점을 다시 보거나 문항 순서를 섞어 새로 풀 수 있어요.'}
    if re.search(r'(?:지난|이전|예전|풀었던).{0,20}(?:시험|문제)', text) and re.search(r'다시\s*풀|셔플|섞|새로', text):
        return {'toolCalls': [], 'needsExamShuffle': True, 'nextCommands': ['python3 scripts/run_agent.py exam-history'],
                'answer': '저장된 시험지 중 하나를 골라 문항 순서를 섞은 새 시험지를 열어드릴게요.'}
    if re.search(r'학습\s*팩|분석.{0,16}(?:다른|모바일|새|이)\s*(?:기기|컴퓨터|곳).{0,12}(?:옮|보내|공유)|(?:다른|모바일|새|이)\s*(?:기기|컴퓨터).{0,12}(?:학습|분석).{0,12}(?:옮|가져|공유)', text):
        if re.search(r'가져|받아|불러|import', text, re.I):
            return {'toolCalls': [], 'needsStudyPackImport': True,
                    'nextCommands': ['python3 scripts/run_agent.py study-pack import --input "<받은 .tpack 파일 경로>"'],
                    'answer': '받은 학습 팩을 이 기기의 학습 공간에 넣을게요. .tpack 파일 경로만 알려 주세요. 원본 강의 파일과 학교 로그인 정보는 따라오지 않아요.'}
        return {'toolCalls': [], 'needsStudyPackExport': True,
                'nextCommands': ['python3 scripts/run_agent.py study-pack export --course "<과목명>" --output "<저장할 경로>.tpack"'],
                'answer': '분석 결과와 학습 세트를 원본 파일 없이 하나의 학습 팩으로 묶을게요. 옮길 과목과 저장할 위치를 정하면 바로 만들 수 있어요.'}
    if re.search(r'원본.{0,20}(?:파일|자료)|(?:파일|자료).{0,20}(?:원본|다운로드|보내줘|제공)', text):
        return {'toolCalls': [], 'needsOriginalFile': True, 'nextCommands': [],
                'answer': '요약본이 아닌 강의 원본 파일로 드릴게요. 대화에서 확인된 과목과 자료를 찾고, 여러 파일이면 골라드릴게요.'}
    if re.search(r"새로\s*고침|동기화|(?:TLS|학교|계정).{0,12}(?:연결|로그인)|(?:최신|다시).{0,8}(?:TLS|학사|제출|시청).{0,12}(?:확인|조회)", text, re.I):
        return {"toolCalls": [], "needsSync": True, "data": {"intent": "sync", "performed": False},
                "nextCommands": ["python3 scripts/sync_tls.py --connect"],
                "answer": "터틀넥 연결창을 열어드릴게요. 열린 화면에서 학교 계정을 직접 입력하면 됩니다. 비밀번호는 화면과 채팅에 표시되지 않아요."}
    if re.search(r"과제\s*(?:id|아이디)|저장.{0,12}(?:방법|어떻게)|(?:어떻게|방법).{0,12}저장", text, re.I):
        return {"toolCalls": [], "needsInput": True, "data": {"intent": "find-assignment"},
                "answer": "‘과제 진행 상황 저장해줘’라고 말하면 과제를 찾아드려요. 후보를 고른 뒤 안내된 저장 명령을 한 번 보내면 됩니다. 과제 ID는 필요 없어요."}
    if re.search(r"도움말|사용법|사용\s*방법|어떻게\s*(?:써|쓰|사용)|뭘\s*할\s*수|뭐\s*할\s*수|무슨\s*기능|처음이|처음\s*(?:써|사용)|기능.*알려", text):
        return usage_guide(text)
    if re.search(r"(?:과제|할\s*일).{0,20}(?:등록|추가)|(?:등록|추가).{0,20}(?:과제|할\s*일)", text):
        return {"toolCalls": [], "data": {"intent": "assignment-add", "requiredFields": ["title"], "optionalFields": ["courseId", "dueAt", "description"]}, "needsInput": True,
                "answer": "새 과제 제목이 무엇인가요? 예: ‘독서 보고서’. 과목이나 마감일을 알면 함께 말해 주세요."}
    if re.search(r"(?:과제|할\s*일).{0,20}(?:삭제|지워|완료\s*처리)|(?:삭제|지워).{0,20}(?:과제|할\s*일)", text):
        return {"toolCalls": [], "data": {"intent": "select-assignment"}, "needsInput": True,
                "answer": "어떤 과제인지 제목을 알려주세요. 직접 추가한 과제는 완료 표시하거나 지울 수 있어요. 학교 과제의 제출 상태는 학교에서 확인해 반영해요."}
    return None


def is_status_request(text: str) -> bool:
    return bool(re.search(r"컨텍스트|전체|상태|(?:지금|현재)(?:은|는)?.{0,12}어때|나[.\s…]*어때|할\s*일|해야\s*할\s*일|마감\s*목록|(?:오늘|지금|다음).{0,12}(?:뭐|무엇|뭘).{0,8}(?:해|하)|(?:뭐|무엇)부터.{0,8}(?:해|하)|급한.*(?:것|거|일)", text))
