const $ = (selector) => document.querySelector(selector);
const state = { data: null, analysisId: null, edits: [], busy: false, draftDirty: false, draftRevision: 0, pendingDraft: null };
const fieldLabels = {status: '상태', owner: '담당자', deadline: '기한'};
const sampleText = `프로젝트: 학교생활 AI
10월 2일 회의: 민수는 로그인 화면 담당, 지연은 로그인 API 담당. 현우는 DB 테이블 생성 완료.
10월 3일 작업 기록: 민수는 화면 구현 완료, 지연은 API 작업 중이며 완료 목표는 10월 4일. 화면과 API 연결 테스트는 아직 하지 않음.`;

async function api(path, payload) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 75000);
  try {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '처리하지 못했어요. 다시 시도해주세요.');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('응답 시간이 초과되었어요. 입력을 보존했습니다. 다시 시도해주세요.');
    if (error instanceof TypeError) throw new Error('서버에 연결할 수 없어요. 입력을 보존했습니다. 연결 후 다시 시도해주세요.');
    throw error;
  } finally { clearTimeout(timeout); }
}
function announce(text) { $('#live-status').textContent = text; }
function showError(id, error) { const el = $(id); el.textContent = error.message; el.hidden = false; }
function setBusy(busy) {
  state.busy = busy;
  for (const id of ['#analyze', '#sample', '#generate', '#analysis-mode', '#assignee']) $(id).disabled = busy;
  $('#task-fields').disabled = busy;
  $('#records').readOnly = busy;
  $('#source-name').readOnly = busy;
  $('#analysis-progress').hidden = !busy;
  $('#analysis-form').setAttribute('aria-busy', String(busy));
}
function step(name) {
  for (const id of ['input', 'review', 'draft']) $(`#step-${id}`).classList.toggle('active', id === name);
}
function changedFields(index) {
  return Object.keys(fieldLabels).filter(field => state.edits[index][field] !== state.data.tasks[index][field]);
}
function renderList(id, texts, empty) {
  const list = $(id); list.replaceChildren();
  for (const text of texts.length ? texts : [empty]) { const li = document.createElement('li'); li.textContent = text; list.append(li); }
}
function renderInsights() {
  const pending = state.data.tasks.flatMap((task, index) => state.edits[index].status === '완료' ? [] : [`${task.title} · ${state.edits[index].owner} · ${state.edits[index].status}`]);
  const checks = state.data.checks.map(item => item.text);
  state.data.tasks.forEach((task, index) => {
    const edit = state.edits[index];
    const unknown = [];
    if (edit.owner === '미정') unknown.push('담당자');
    if (edit.deadline === '미정') unknown.push('기한');
    if (edit.status === '확인 필요') unknown.push('진행 상태');
    if (unknown.length) checks.push(`${task.title}: ${unknown.join(' · ')} 확인 필요`);
  });
  if (!state.data.resources.length) checks.push('관련 코드·문서·테스트 자료의 위치 확인 필요');
  renderList('#next-list', pending, '기록된 미완료 작업이 없어요.');
  renderList('#check-list', checks, '추가 확인 사항이 없어요.');
  $('#next-count').textContent = pending.length;
  $('#check-count').textContent = checks.length;
}
function renderOwners() {
  const select = $('#assignee'), current = select.value;
  select.replaceChildren(new Option('팀 전체', ''));
  for (const owner of new Set(state.edits.map(e => e.owner).filter(owner => owner !== '미정'))) select.add(new Option(owner, owner));
  if ([...select.options].some(option => option.value === current)) select.value = current;
}
function markTask(card, index) {
  const changed = changedFields(index);
  card.querySelector('.edited-tag').hidden = !changed.length;
  const description = card.querySelector('.edit-description');
  description.hidden = !changed.length;
  description.textContent = changed.map(field => `${fieldLabels[field]}: ${state.data.tasks[index][field]} → ${state.edits[index][field]}`).join(' · ');
  card.querySelector('.task-status').dataset.status = state.edits[index].status;
}
function taskChanged(card, index, field, value) {
  state.edits[index][field] = value.trim() || '미정';
  markTask(card, index);
  renderInsights(); renderOwners();
  if ($('#draft').value) $('#draft-stale').hidden = false;
  if (field === 'status') renderTasks();
}
function renderTasks() {
  const expanded = new Set([...document.querySelectorAll('.task-card')].filter(card => card.querySelector('.evidence').open).map(card => card.dataset.index));
  $('#active-tasks').replaceChildren(); $('#completed-tasks').replaceChildren();
  let completed = 0;
  state.data.tasks.forEach((task, index) => {
    const card = $('#task-template').content.firstElementChild.cloneNode(true);
    card.dataset.index = index;
    card.querySelector('.evidence').open = expanded.has(String(index));
    card.querySelector('h4').textContent = task.title;
    for (const [field, selector] of [['status', '.task-status'], ['owner', '.task-owner'], ['deadline', '.task-deadline']]) {
      const input = card.querySelector(selector);
      input.value = state.edits[index][field];
      input.setAttribute('aria-label', `${task.title} ${fieldLabels[field]}`);
      input.addEventListener(field === 'status' ? 'change' : 'input', () => taskChanged(card, index, field, input.value));
      if (field !== 'status') input.addEventListener('blur', () => { input.value = state.edits[index][field]; });
    }
    card.querySelector('.deadline-note').textContent = task.deadlineKind === '목표' ? `원문 완료 목표: ${task.deadline}` : '';
    const evidence = card.querySelector('.evidence-content');
    for (const entry of task.evidence) {
      const label = document.createElement('p'); label.className = 'source-label'; label.textContent = `${state.data.sourceName} · ${entry.recordId}`;
      const quote = document.createElement('blockquote'); quote.textContent = entry.quote;
      evidence.append(label, quote);
    }
    markTask(card, index);
    if (state.edits[index].status === '완료') { $('#completed-tasks').append(card); completed++; }
    else $('#active-tasks').append(card);
  });
  if (!$('#active-tasks').children.length) { const p = document.createElement('p'); p.className = 'helper'; p.textContent = '진행 중이거나 미완료인 작업이 없어요.'; $('#active-tasks').append(p); }
  $('#completed-count').textContent = completed;
  $('#completed-panel').hidden = !completed;
}
function renderResults() {
  $('#results').hidden = false;
  $('#results-title').textContent = state.data.projectName === '미정' ? '정리 결과' : `${state.data.projectName} · 정리 결과`;
  $('#result-meta').textContent = `${state.data.sourceName} · 작업 ${state.data.tasks.length}개 · ${new Set(state.data.tasks.map(task => task.owner).filter(owner => owner !== '미정')).size}명의 담당자 확인`;
  $('#result-source').textContent = state.data.analysisSource === 'local-rules' ? '임시 규칙 분석 · AI 아님' : 'AI 분석 · 원문 검증됨';
  $('#summary').textContent = state.data.summary;
  $('#decisions').replaceChildren();
  if (state.data.decisions.length) {
    const title = document.createElement('h3'); title.textContent = '주요 결정 사항'; $('#decisions').append(title);
    for (const item of state.data.decisions) {
      const p = document.createElement('p'); p.textContent = item.text;
      const refs = document.createElement('p'); refs.className = 'helper'; refs.textContent = item.evidence.map(e => `${state.data.sourceName} · ${e.recordId}: ${e.quote}`).join(' / ');
      $('#decisions').append(p, refs);
    }
  }
  $('#suggestion-panel').hidden = !state.data.suggestions.length;
  renderList('#suggestions', state.data.suggestions.map(s => `${s.text} (근거 작업: ${s.basedOn.map(i => state.data.tasks[i].title).join(', ')})`), '없음');
  renderInsights(); renderTasks(); renderOwners();
}
function setDraft(text) {
  $('#draft').value = text; state.draftDirty = false; state.draftRevision++;
  state.pendingDraft = null; $('#replacement').hidden = true; $('#draft-edited').hidden = true;
  $('#draft-stale').hidden = true; $('#draft-panel').hidden = false;
  $('#generate').firstChild.textContent = '최신 작업으로 재생성 ';
  step('draft'); announce('인수인계 초안을 만들었어요. 편집하거나 복사할 수 있어요.');
}
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const data = await api('/api/start', {request: $('#request').value});
    $('#chat-answer').textContent = data.answer;
    $('#records').focus(); step('input');
  } catch (error) { $('#chat-answer').textContent = error.message; }
});
$('#records').addEventListener('input', () => { $('#char-count').textContent = `${$('#records').value.length.toLocaleString('ko-KR')} / 80,000`; });
$('#sample').addEventListener('click', () => {
  if ($('#records').value.trim() && !confirm('입력한 자료를 예시 자료로 바꿀까요?')) return;
  $('#records').value = sampleText; $('#source-name').value = '예시 회의록 · 10월 작업 기록';
  $('#records').dispatchEvent(new Event('input')); $('#records').focus(); announce('예시 자료를 넣었어요. 분석 방식은 선택한 설정을 유지합니다.');
});
function showMode() {
  const local = $('#analysis-mode').value === 'local';
  $('#mode-badge').textContent = local ? '임시 분석 모드' : 'AI 분석 모드';
  $('#mode-badge').classList.toggle('ai', !local);
  $('#mode-help').textContent = local ? '임시 분석은 단순한 한국어 작업 문장을 추출합니다. 원문을 확인하고 수정해주세요.' : '설정된 AI 서버가 자료를 분석합니다. 실패하면 입력을 보존해요.';
}
$('#analysis-mode').addEventListener('change', showMode);
$('#analysis-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  $('#input-error').hidden = true;
  if (!$('#records').value.trim()) { showError('#input-error', new Error('회의록이나 작업 기록을 붙여넣어주세요.')); $('#records').focus(); return; }
  if (state.data && (state.edits.some((_, i) => changedFields(i).length) || $('#draft').value) && !confirm('새 분석으로 작업 수정과 초안을 교체할까요? 필요한 초안은 먼저 복사해주세요.')) return;
  const revision = state.draftRevision;
  $('#progress-text').textContent = $('#analysis-mode').value === 'local' ? '기록에서 작업을 추출하고 있어요…' : 'AI가 기록을 읽고 정리하고 있어요…';
  setBusy(true);
  try {
    const response = await api('/api/analyze', {text: $('#records').value, sourceName: $('#source-name').value, mode: $('#analysis-mode').value});
    if (revision !== state.draftRevision && !confirm('분석 중 초안이 편집되었습니다. 새 분석으로 교체할까요?')) return;
    state.data = response.data; state.analysisId = response.analysisId;
    state.edits = state.data.tasks.map(({status, owner, deadline}) => ({status, owner, deadline}));
    $('#draft').value = ''; state.draftDirty = false; state.draftRevision++;
    state.pendingDraft = null; $('#replacement').hidden = true; $('#draft-panel').hidden = true; $('#draft-error').hidden = true;
    $('#generate').firstChild.textContent = '인수인계 만들기 ';
    $('#analyze').firstChild.textContent = '기록 정리하기 ';
    renderResults(); step('review');
    announce(state.data.tasks.length ? '정리가 끝났어요. 작업을 확인하고 수정해주세요.' : '추출된 작업이 없어요. 작업 제목과 진행 상태를 포함해 자료를 보완해주세요.');
    $('#results').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) { showError('#input-error', error); $('#analyze').firstChild.textContent = '다시 시도 '; }
  finally { setBusy(false); }
});
$('#generate').addEventListener('click', async () => {
  if (state.busy || !state.data) return;
  if (state.draftDirty && !confirm('직접 편집한 초안을 새 초안으로 교체할까요? 편집한 내용은 먼저 복사해주세요.')) return;
  $('#draft-error').hidden = true;
  const revision = state.draftRevision;
  $('#progress-text').textContent = '수정한 작업으로 인수인계 초안을 만들고 있어요…'; setBusy(true);
  try {
    const response = await api('/api/handover', {analysisId: state.analysisId, edits: state.edits, assignee: $('#assignee').value});
    if (revision !== state.draftRevision) { state.pendingDraft = response.draft; $('#replacement').hidden = false; announce('생성 중 편집한 초안을 보존했어요.'); }
    else { setDraft(response.draft); $('#draft').focus(); }
  } catch (error) { showError('#draft-error', error); }
  finally { setBusy(false); }
});
$('#draft').addEventListener('input', () => { state.draftDirty = true; state.draftRevision++; $('#draft-edited').hidden = false; });
$('#replace-draft').addEventListener('click', () => { if (state.pendingDraft !== null) setDraft(state.pendingDraft); });
$('#keep-draft').addEventListener('click', () => { state.pendingDraft = null; $('#replacement').hidden = true; });
$('#assignee').addEventListener('change', () => { if ($('#draft').value) $('#draft-stale').hidden = false; });
$('#copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#draft').value); announce('인수인계 내용을 복사했어요.'); }
  catch { $('#draft').focus(); $('#draft').select(); announce('자동 복사를 사용할 수 없어 내용을 선택했어요. Ctrl+C 또는 ⌘C로 복사해주세요.'); }
});
window.addEventListener('beforeunload', event => {
  if ($('#records').value || $('#draft').value || state.edits.some((_, i) => changedFields(i).length)) { event.preventDefault(); event.returnValue = ''; }
});
async function init() {
  const request = new URLSearchParams(location.search).get('request');
  if (request) { $('#request').value = request.slice(0, 500); $('#chat-form').requestSubmit(); }
  try {
    const response = await fetch('/api/config'); if (!response.ok) throw new Error();
    const config = await response.json(); $('#analysis-mode').value = config.aiConfigured ? 'ai' : 'local';
  } catch { announce('서버 연결을 확인해주세요. 입력은 화면에 유지됩니다.'); }
  showMode();
}
init();
