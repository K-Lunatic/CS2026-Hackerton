"""Incremental file analysis and question bank, persisted by StudySession."""
import secrets
import re
from features.study_materials import study_materials

CHUNK_CHARS = 8000


def start(session, state, selected, material=None):
    if material and material.get('truncated'):
        return {'status': 'selecting', 'answer': '첨부 본문 전체를 읽지 못했어요. 파일을 나눠서 첨부해주세요.'}
    # ponytail: analysis lives in the existing session JSON; split into rows if large banks make writes measurably slow.
    state.update(phase='extracting', pipeline={'files': selected, 'fileIndex': 0,
                 'units': [], 'candidates': [], 'failures': [], 'chunks': [], 'chunkIndex': 0})
    if material:
        load_chunks(state, material)
    return advance(session, state)


def load_chunks(state, material):
    p = state['pipeline']
    chunks, chunk, size = [], [], 0
    for section in material['sections']:
        text = section['text']
        while text:
            excerpt, text = text[:CHUNK_CHARS - size], text[CHUNK_CHARS - size:]
            chunk.append({'resourceId': material['id'], 'courseId': state['selection']['courseId'],
                          'name': material['title'], 'location': section['location'], 'text': excerpt})
            size += len(excerpt)
            if size == CHUNK_CHARS:
                chunks.append(chunk); chunk, size = [], 0
    if chunk:
        chunks.append(chunk)
    p.update(chunks=chunks, chunkIndex=0, fileName=material['title'])


def advance(session, state):
    from features.study import PROMPT
    p = state['pipeline']
    while p['fileIndex'] < len(p['files']):
        if not p['chunks']:
            rid = p['files'][p['fileIndex']]
            result = study_materials(session.provider.get_courses(session.user), session.provider.get_resources(session.user),
                files_root=session.files_root, course_id=state['selection']['courseId'], resource_ids=[rid],
                locations=state['selection'].get('locations', {}), include_ids=True, max_chars=10_000_000)
            material = result['data']['materials'][0]
            if material.get('error') or material.get('truncated') or not material['sections']:
                p['failures'].append({'resourceId': rid, 'name': material['title'],
                                      'reason': material.get('error', '본문 전체를 읽지 못해 제외했습니다.')})
                p['fileIndex'] += 1
                continue
            load_chunks(state, material)
        state['requestId'] = state.get('requestId') or secrets.token_hex(12)
        progress = {'processedFiles': p['fileIndex'], 'totalFiles': len(p['files']),
                    'fileName': p['fileName'], 'part': p['chunkIndex'] + 1, 'totalParts': len(p['chunks'])}
        schema = session.generation_request({'settings': state['settings'], 'selection': state['selection'],
            'sources': p['chunks'][p['chunkIndex']], 'requestId': state['requestId']}, [], False)['hostOnly']['schema']
        schema.update(context='수업 맥락 (2000자 이내)',
            learning=[{'concept': '주요 학습 내용', 'explanation': '설명',
                       'evidence': [{'resourceId': '자료 ID', 'location': '정확한 위치', 'quote': '연속된 원문'}]}],
            types=[{'type': '유형 식별자', 'reason': '이 내용에 적합한 이유'}])
        prior = p['units'][-1] if p['units'] and p['units'][-1]['resourceId'] == p['files'][p['fileIndex']] else None
        return {'status': 'prepared', 'needsExtraction': True, 'requestId': state['requestId'],
                'progress': progress, 'failures': p['failures'],
                'answer': f"자료 {p['fileIndex'] + 1}/{len(p['files'])} · {p['fileName']}의 {p['chunkIndex'] + 1}/{len(p['chunks'])} 부분을 차근차근 정리하고 있어요.",
                'hostOnly': {'instruction': PROMPT + '\n이 파일 부분만 분석하라. previousPart는 같은 파일 앞부분의 맥락 안내일 뿐 새 문항의 원문 근거가 아니다. 주요 학습 내용과 수업 맥락을 분리하고 가능한 유형별 완성 문항을 보통 1~5개, 최대 20개 작성하라. 자료에 맞지 않는 유형은 만들지 마라. 인용은 1000자 이내. 20개보다 적으면 shortageReason을 적어라.',
                    'previousPart': {'context': prior['context'][:1000], 'concepts': [x['concept'][:100] for x in prior['learning'][:10]]} if prior else None,
                    'courseName': next((c['name'] for c in session.provider.get_courses(session.user) if c['id'] == state['selection']['courseId']), state['selection'].get('attachmentTitle', '첨부 자료')),
                    'settings': state['settings'], 'SOURCE': p['chunks'][p['chunkIndex']],
                    'schema': schema}}
    state['phase'] = 'assembling'
    state.pop('requestId', None)
    return catalog(state, {})


def catalog(state, event):
    p = state['pipeline']
    offset, limit = event.get('offset', 0), event.get('limit', 10)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('후보 목록은 offset >= 0, limit 1~20으로 조회하세요.')
    candidates = p['candidates']
    return {'status': 'assembling', 'needsAssembly': True, 'totalCandidates': len(candidates),
            'processedFiles': p['fileIndex'], 'totalFiles': len(p['files']), 'failures': p['failures'],
            'answer': f"자료 {len(p['files'])}개를 확인했어요. 저장한 후보 {len(candidates)}개에서 범위와 유형을 맞춰 시험지를 엮을게요.",
            'nextOffset': offset + limit if offset + limit < len(candidates) else None,
            'hostOnly': {'instruction': '모든 페이지의 후보를 비교해 개념·파일·유형을 균형 있게 선택하라. 중복·정답 누설을 검토하라. 읽지 못한 파일은 포함하지 마라. 새 문항을 즉석에서 만들지 말고 assemble에 candidateIds를 전달하라.',
                'settings': state['settings'], 'candidates': [{k: q[k] for k in ('id', 'type', 'concept', 'question', 'unitId')}
                    for q in candidates[offset:offset + limit]]}}


def handle(session, state, event):
    from features.study import evidence, required_text, validate_generated
    p, action = state['pipeline'], event['action']
    if action == 'status':
        return advance(session, state) if state['phase'] == 'extracting' else catalog(state, {})
    if action == 'extract':
        if state['phase'] != 'extracting' or event.get('requestId') != state.get('requestId'):
            raise ValueError('현재 파일 부분에 대한 추출 요청이 아닙니다.')
        data = event.get('data')
        context = required_text(data, 'context')
        if len(context) > 2000:
            raise ValueError('파일 맥락은 2000자 이내로 정리하세요.')
        sources = p['chunks'][p['chunkIndex']]
        learning = data.get('learning')
        types = data.get('types')
        if not isinstance(learning, list) or not 1 <= len(learning) <= 20 or not isinstance(types, list) or len(types) > 20:
            raise ValueError('주요 학습 내용 1~20개와 유형 목록(최대 20개)이 필요합니다.')
        checked_learning = [{'concept': required_text(x, 'concept'), 'explanation': required_text(x, 'explanation'),
                            'evidence': evidence(x.get('evidence'), sources)} for x in learning]
        checked_types = [{'type': required_text(x, 'type'), 'reason': required_text(x, 'reason')} for x in types]
        if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', t['type']) or t['type'] == 'auto' for t in checked_types) or len({t['type'] for t in checked_types}) != len(checked_types):
            raise ValueError('유형을 중복 없이 분리하세요.')
        if any(len(x[k]) > 2000 for x in checked_learning for k in ('concept', 'explanation')) or any(len(t['reason']) > 2000 for t in checked_types):
            raise ValueError('학습 내용과 유형 이유는 항목당 2000자 이내로 정리하세요.')
        candidate_state = {'sources': sources, 'settings': {**state['settings'], 'count': 20, 'types': ['auto'] * 20}}
        questions = validate_generated(data, candidate_state)
        if any(q['type'] not in {t['type'] for t in checked_types} for q in questions):
            raise ValueError('각 문항은 분석한 시험 유형에 속해야 합니다.')
        if any(len(ref['quote']) > 1000 for q in questions for ref in q['evidence']):
            raise ValueError('문항 인용은 1000자 이내로 지정하세요.')
        unit_id = f"u{len(p['units']) + 1}"
        p['units'].append({'id': unit_id, 'resourceId': p['files'][p['fileIndex']],
            'part': p['chunkIndex'] + 1, 'context': context, 'learning': checked_learning,
            'types': checked_types, 'sources': sources, 'shortageReason': data.get('shortageReason', '')})
        for q in questions:
            p['candidates'].append({**q, 'id': f"c{len(p['candidates']) + 1}", 'unitId': unit_id})
        p['chunkIndex'] += 1
        if p['chunkIndex'] == len(p['chunks']):
            p['fileIndex'] += 1
            p['chunks'] = []
        state.pop('requestId', None)
        return advance(session, state)
    if state['phase'] != 'assembling':
        raise ValueError('범위 내 모든 파일의 분석이 끝나야 시험지를 조합할 수 있습니다.')
    if action == 'catalog':
        return catalog(state, event)
    if action == 'unit':
        unit = next((u for u in p['units'] if u['id'] == event.get('unitId')), None)
        if unit is None:
            raise ValueError('저장된 파일 분석 단위가 아닙니다.')
        return {'status': 'assembling', 'hostOnly': {k: v for k, v in unit.items() if k != 'sources'}}
    if action == 'candidate':
        q = next((q for q in p['candidates'] if q['id'] == event.get('candidateId')), None)
        if q is None:
            raise ValueError('저장된 문항 후보가 아닙니다.')
        return {'status': 'assembling', 'hostOnly': {'question': q}}
    if action != 'assemble':
        raise ValueError('지원하지 않는 파일 분석 이벤트입니다.')
    ids = event.get('candidateIds')
    bank = {q['id']: q for q in p['candidates']}
    if not isinstance(ids, list) or any(not isinstance(i, str) or i not in bank for i in ids) or len(set(ids)) != len(ids) or len(ids) > state['settings']['count']:
        raise ValueError('저장된 후보를 중복 없이 설정 문항 수 이내로 선택하세요.')
    if p['failures'] and event.get('acceptExclusions') is not True:
        raise ValueError('제외한 파일과 사유를 사용자에게 알리고 동의받은 뒤 acceptExclusions=true로 조합하세요.')
    sources, questions = {}, []
    for index, cid in enumerate(ids, 1):
        q = bank[cid]
        refs = [{k: ref[k] for k in ('resourceId', 'location', 'quote')} for ref in q['evidence']]
        for ref in q['evidence']:
            key = (ref['resourceId'], ref['location'])
            sources.setdefault(key, {**{k: ref[k] for k in ('resourceId', 'courseId', 'name', 'location')}, 'text': ''})['text'] += '\n' + ref['quote']
        questions.append({**q, 'id': f'q{index}', 'evidence': refs})
    state.update(phase='prepared', sources=list(sources.values()), requestId=secrets.token_hex(12))
    result = session.transition(state, {'action': 'generate', 'requestId': state['requestId'],
        'data': {'questions': questions, 'shortageReason': event.get('shortageReason', '')}})
    result['failures'] = p['failures']
    return result
