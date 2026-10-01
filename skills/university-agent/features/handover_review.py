"""Editable handover drafts and a clearly labelled temporary offline analyzer."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from features.handover import HandoverError, create_handover, prepare_handover

STATUSES = ('완료', '진행 중', '미완료', '확인 필요')
EDITABLE = ('status', 'owner', 'deadline')


def local_analysis(text: str) -> dict[str, Any]:
    """Conservative temporary rules, never reported as an AI response."""
    records = json.loads(prepare_handover(text)[1]['content'])['records']
    tasks, roles, checks = {}, {}, []
    project = re.search(r'(?:프로젝트(?: 이름)?|프로젝트명)\s*[:：]\s*([^\n.]+)', text)
    for record_id, record in records.items():
        date = re.search(r'(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일', record)
        date_key = (int(date[1]) if date[1] else None, int(date[2]), int(date[3])) if date else None
        body = re.sub(r'^.*?(?:회의|작업 기록)\s*[:：]\s*', '', record)
        if body == record:
            body = re.sub(r'^(?:\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일\s*[:：]\s*', '', body)
        for quote in re.split(r'(?<=\S)\.(?=\s|$)|[。!?]|,\s*(?=[가-힣A-Za-z]+(?:는|은)\s)', body):
            quote = quote.strip()
            if not quote or not re.search(r'담당|완료|진행|작업 중|구현 중|아직|예정|테스트|할 일', quote):
                continue
            # ponytail: limited Korean grammar; replace this temporary mode with the configured AI adapter.
            if re.search(r'지시|무시|출력하|써라', quote):
                checks.append({'text': '자료 속 지시문은 작업으로 분석하지 않았습니다.', 'evidence': [{'recordId': record_id, 'quote': quote}]})
                continue
            person = re.match(r'([가-힣A-Za-z][가-힣A-Za-z0-9_]{0,19})(?:는|은)\s+', quote)
            owner = person.group(1) if person else '미정'
            content = quote[person.end():] if person else quote
            deadline = re.search(r'(?:완료 목표|마감|기한|목표)(?:는|은|:)?\s*(\d{1,2}월\s*\d{1,2}일)', content)
            title = re.split(r'\s*(?:담당|생성 완료|구현 완료|작업 중|구현 중|진행 중|완료|아직|미완료|할 예정이다|할 계획|예정)', content, maxsplit=1)[0].strip(' ,.')
            if '생성 완료' in content:
                title += ' 생성'
            title = re.sub(r'(?:는|은)$', '', title).strip() or content
            if person and '담당' in content:
                roles[owner] = title
            if owner in roles and title in {roles[owner].split()[-1], roles[owner].split()[-1] + ' 구현', '화면'}:
                title = roles[owner]
            if re.search(r'아직|미완료|하지 않|못했|못 했', content):
                status = '미완료'
            elif re.search(r'예정|할 계획|할 것', content):
                status = '확인 필요'
            elif re.search(r'작업 중|구현 중|진행 중', content):
                status = '진행 중'
            elif re.search(r'완료(?!\s*목표)|완성|했음|끝남', content):
                status = '완료'
            else:
                status = '확인 필요'
            item = {'title': title, 'owner': owner, 'status': status,
                    'deadline': deadline.group(1) if deadline else '미정',
                    'deadlineKind': '목표' if deadline and '목표' in deadline.group(0) else '기한' if deadline else '미정',
                    'evidence': [{'recordId': record_id, 'quote': quote}]}
            key = (owner, title)
            old = tasks.get(key)
            if old:
                previous, old_date = old
                item['evidence'] = previous['evidence'] + item['evidence']
                if date_key and old_date and date_key != old_date and (date_key[0] is None) == (old_date[0] is None):
                    older = date_key[1:] < old_date[1:] if date_key[0] is None else date_key < old_date
                    if older:
                        previous['evidence'] = item['evidence']
                        continue
                    if item['deadline'] == '미정' and previous['deadline'] != '미정':
                        item['deadline'], item['deadlineKind'] = previous['deadline'], previous['deadlineKind']
                elif previous['status'] != item['status'] and not all('담당' in e['quote'] for e in previous['evidence']):
                    item['status'] = '확인 필요'
                    checks.append({'text': f'{title}: 기록의 상태가 충돌하여 확인이 필요합니다.', 'evidence': item['evidence']})
            tasks[key] = (item, date_key)
    values = [item for item, _ in tasks.values()]
    resources = [{'text': match.group(0), 'evidence': [{'recordId': rid, 'quote': match.group(0)}]}
                 for rid, record in records.items()
                 for match in re.finditer(r'https?://[^\s,]+|/api/[^\s,]+|[\w./-]+\.(?:py|md|js|tsx|pdf)\b', record)]
    raw = {'summary': f'임시 규칙 분석으로 작업 {len(values)}개를 추출했습니다. 상태와 담당자를 원문과 비교해 확인해주세요.',
           'tasks': values, 'decisions': [], 'resources': resources, 'checks': checks, 'suggestions': []}
    # Reuse the same structural and exact-source validation as real AI analysis.
    result = create_handover(text, analysis_json=json.dumps(raw, ensure_ascii=False),
                             project_name=project.group(1).strip() if project else '')
    result['analysisSource'] = 'local-rules'
    return result


def review_tasks(original: dict[str, Any], edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allow only user-owned fields; original evidence stays untouched."""
    if not isinstance(edits, list) or len(edits) != len(original['tasks']):
        raise HandoverError('모든 작업의 수정 값을 전달해주세요.')
    tasks = deepcopy(original['tasks'])
    for task, edit in zip(tasks, edits):
        if not isinstance(edit, dict) or set(edit) != set(EDITABLE):
            raise HandoverError('상태, 담당자, 기한만 수정할 수 있습니다.')
        if edit['status'] not in STATUSES:
            raise HandoverError('작업 상태가 올바르지 않습니다.')
        task['userEdits'] = {}
        for field in EDITABLE:
            if not isinstance(edit[field], str) or len(edit[field]) > 200:
                raise HandoverError('수정 값은 200자 이내의 텍스트여야 합니다.')
            value = edit[field].strip() or '미정'
            if value != task[field]:
                task['userEdits'][field] = {'before': task[field], 'after': value}
                task[field] = value
                if field == 'deadline':
                    task['deadlineKind'] = '기한' if value != '미정' else '미정'
    return tasks


def make_draft(original: dict[str, Any], edits: list[dict[str, Any]], assignee: str = '') -> str:
    tasks = review_tasks(original, edits)
    if not isinstance(assignee, str) or len(assignee) > 200:
        raise HandoverError('담당자 선택이 올바르지 않습니다.')
    selected = [(i, task) for i, task in enumerate(tasks) if not assignee or task['owner'] in {assignee, '미정'}]
    selected_ids = {i for i, _ in selected}
    lines = [f"{original['projectName']} · 인수인계 초안", f"범위: {assignee or '팀 전체'}", '', '현재 상태']
    def task_line(task):
        edited = ' [사용자 수정]' if task['userEdits'] else ''
        deadline_label = '목표' if task['deadlineKind'] == '목표' else '기한'
        return f"- {task['title']} | {task['status']} | 담당 {task['owner']} | {deadline_label} {task['deadline']}{edited}"
    lines.extend(task_line(t) for _, t in selected)
    if not selected:
        lines.append('- 선택한 담당자의 작업 기록 없음')
    lines.extend(['', '이어서 할 일'])
    pending = [t for _, t in selected if t['status'] != '완료']
    lines.extend(task_line(t) for t in pending)
    if not pending:
        lines.append('- 기록된 미완료 작업 없음')
    lines.extend(['', '관련 자료'])
    lines.extend('- ' + r['text'] for r in original['resources'])
    if not original['resources']:
        lines.append('- 자료 위치 미정: 코드, 문서, 테스트 결과의 위치를 확인해주세요.')
    lines.extend(['', '확인 사항'])
    checks = [c['text'] for c in original['checks']]
    for _, task in selected:
        unknown = [label for field, label in [('owner', '담당자'), ('deadline', '기한')] if task[field] == '미정']
        if task['status'] == '확인 필요':
            unknown.append('진행 상태')
        if unknown:
            checks.append(task['title'] + ': ' + ', '.join(unknown) + ' 확인 필요')
    lines.extend('- ' + c for c in checks or ['추가 확인 사항 없음'])
    lines.extend(['', 'AI 제안 (기록에서 확인된 작업과 구분)'])
    suggestions = [s for s in original['suggestions'] if any(i in selected_ids for i in s['basedOn'])]
    lines.extend('- ' + s['text'] for s in suggestions or [{'text': '없음'}])
    lines.extend(['', '원문 근거'])
    for _, task in selected:
        lines.append('- ' + task['title'])
        for evidence in task['evidence']:
            source = original.get('sourceName', '붙여넣은 자료')
            lines.append(f"  {source} · {evidence['recordId']}: {evidence['quote']}")
        if task['userEdits']:
            labels = {'owner': '담당자', 'status': '상태', 'deadline': '기한'}
            lines.append('  사용자 수정: ' + '; '.join(f"{labels[k]} {v['before']} → {v['after']}" for k, v in task['userEdits'].items()))
    return '\n'.join(lines)
