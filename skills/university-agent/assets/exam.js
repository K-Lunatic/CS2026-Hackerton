'use strict';
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
let savedToken = '';
try { savedToken = sessionStorage.getItem('exam-token') || ''; } catch (_) { /* Browser privacy settings can disable storage. */ }
const token = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : savedToken;
// Tab-scoped token survives refresh, but never enters a request URL or localStorage.
try {
  sessionStorage.setItem('exam-token', token);
  history.replaceState(null, '', '/');
} catch (_) { /* Keep the fragment for refresh when tab storage is unavailable. */ }
const SVG = 'http://www.w3.org/2000/svg';
const OUTCOME = {correct: '맞았어요', partial: '일부만 맞았어요', incorrect: '틀렸어요'};
// Red-pencil marks drawn over the question number: circle, triangle, slash.
const MARK = {
  correct: 'M33 7 C18 4 6 15 6 30 C6 46 19 55 33 54 C47 53 56 42 55 28 C54 14 43 5 27 9',
  partial: 'M30 7 L53 50 L7 49 L31 6',
  incorrect: 'M49 6 C38 22 24 40 9 55',
};
const GLYPH = {correct: '○', partial: '△', incorrect: '／'};
let exam, drafts = {}, revision = 0, timer, clockTimer, clockBaseElapsed = null, clockSyncedAt = 0, focusRequest = Promise.resolve(), focusedQuestionId = '', saving = Promise.resolve(), submitting = false, dirty = false, editVersion = 0;
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, {method: data ? 'POST' : 'GET', headers: {'X-Exam-Token': token, 'Content-Type': 'application/json'}, body: data ? JSON.stringify(data) : undefined});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '시험지 연결을 확인해주세요.');
  return result;
}
function focusQuestion(q) {
  if (exam.readOnly || exam.status !== 'question' || focusedQuestionId === q.id) return;
  focusedQuestionId = q.id;
  focusRequest = focusRequest.catch(() => {}).then(() => api('/api/focus', {examId: exam.examId, questionId: q.id})).catch(e => error(e.message));
}
function orderingValue(q) {
  try { const value = JSON.parse(drafts[q.id] || '[]'); return Array.isArray(value) ? value : []; } catch (_) { return []; }
}
const answered = q => {
  if (q.responseFormat !== 'ordering') return Boolean((drafts[q.id] || '').trim());
  const value = orderingValue(q);
  return value.length === q.blocks.length && new Set(value).size === q.blocks.length && value.every(item => Number.isInteger(item) && item >= 0 && item < q.blocks.length);
};
function displayAnswer(q, text) {
  if (q.responseFormat !== 'ordering') return text;
  try { return JSON.parse(text || '[]').map(index => q.blocks[index] || '알 수 없는 블록').join(' → '); } catch (_) { return '아직 순서를 정하지 않았어요.'; }
}
// A saved choice is an option number or the option text.
function chosen(q) {
  const text = drafts[q.id] || '';
  return /^\d+$/.test(text) && q.options[Number(text) - 1] !== undefined ? Number(text) : q.options.indexOf(text) + 1;
}
function progress() {
  const count = exam.questions.filter(answered).length;
  if (exam.readOnly) { $('progress').textContent = '지난 시험과 당시 답안을 보고 있어요'; return; }
  $('progress').textContent = exam.status === 'question' ? exam.questions.length + '문항 중 ' + count + '문항에 답했어요'
    : exam.status === 'grading' ? '채점을 기다리고 있어요' : '채점이 끝났어요';
}
function formatDuration(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), secs = seconds % 60;
  return (hours ? String(hours).padStart(2, '0') + ':' : '') + String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
}
function updateClock() {
  if (!exam || !Number.isFinite(Number(exam.elapsedSeconds))) { $('time-panel').hidden = true; return; }
  const running = exam.status === 'question' && !exam.readOnly && clockSyncedAt;
  const elapsed = Number(exam.elapsedSeconds) + (running ? Math.floor((Date.now() - clockSyncedAt) / 1000) : 0);
  $('time-panel').hidden = false; $('elapsed-time').textContent = formatDuration(elapsed);
  const limited = Number.isFinite(Number(exam.timeLimitSeconds)) && Number(exam.timeLimitSeconds) > 0;
  $('limit-clock').hidden = !limited;
  if (!limited) return;
  const remaining = Math.max(0, Number(exam.timeLimitSeconds) - elapsed), overtime = Math.max(0, elapsed - Number(exam.timeLimitSeconds));
  $('limit-label').textContent = overtime ? '제한 시간 초과' : '남은 시간';
  $('remaining-time').textContent = overtime ? '+' + formatDuration(overtime) : formatDuration(remaining);
  $('limit-clock').classList.toggle('overtime', overtime > 0);
}
function syncClock(data) {
  clearInterval(clockTimer); clockTimer = null; clockBaseElapsed = Number.isFinite(Number(data.elapsedSeconds)) ? Number(data.elapsedSeconds) : null;
  clockSyncedAt = Date.now(); updateClock();
  if (data.status === 'question' && !data.readOnly && clockBaseElapsed !== null) clockTimer = setInterval(updateClock, 1000);
}
function saveDraft() {
  if (exam.readOnly) return Promise.resolve();
  const draft = {...drafts};
  const version = editVersion;
  saving = saving.catch(() => {}).then(async () => {
    $('saved').textContent = '답안을 저장하고 있어요…';
    const result = await api('/api/draft', {examId: exam.examId, answers: draft, revision});
    revision = result.revision;
    dirty = editVersion !== version;
    $('saved').textContent = dirty ? '새로 쓴 답안도 저장하고 있어요…' : '답안 저장 완료';
    error('');
  });
  // A failed save blocks this submission; the next edit may retry the connection.
  saving.catch(e => error(e.message));
  return saving;
}
function setDraft(q, value) {
  if (submitting || exam.readOnly || exam.status !== 'question') return;
  focusQuestion(q);
  drafts[q.id] = value;
  editVersion++;
  $('row-' + q.id).replaceWith(omrRow(q, exam.questions.indexOf(q)));
  progress(); resetConfirm(); clearTimeout(timer);
  dirty = true; $('saved').textContent = '답안 변경됨';
  timer = setTimeout(saveDraft, 500);
}
function mark(outcome, order) {
  const svg = document.createElementNS(SVG, 'svg'), path = document.createElementNS(SVG, 'path');
  svg.setAttribute('viewBox', '0 0 60 60'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('mark');
  svg.style.setProperty('--i', order);
  path.setAttribute('d', MARK[outcome]); path.setAttribute('pathLength', '1');
  svg.append(path);
  return svg;
}
function confusionIcon() {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 44 32'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('confusion-mark');
  const eye = document.createElementNS(SVG, 'path'); eye.setAttribute('d', 'M3 16 Q12 5 22 5 Q32 5 41 16 Q32 27 22 27 Q12 27 3 16');
  const line = document.createElementNS(SVG, 'path'); line.setAttribute('d', 'M22 1 V31'); svg.append(eye, line); return svg;
}
function confusionButton(q) {
  const button = node('button', undefined, 'confusion-toggle'); button.type = 'button';
  button.append(confusionIcon(), node('span', '헷갈렸어요'));
  const update = confused => { button.classList.toggle('selected', confused); button.setAttribute('aria-pressed', String(confused)); };
  update((exam.confusedIds || []).includes(q.id)); button.disabled = exam.readOnly || exam.status !== 'question';
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    const confused = !(exam.confusedIds || []).includes(q.id); button.disabled = true;
    try {
      const result = await api('/api/confusion', {examId: exam.examId, questionId: q.id, confused});
      exam.confusedIds = result.confusedIds; update(confused);
    } catch (e) { error(e.message); }
    finally { button.disabled = exam.readOnly || exam.status !== 'question'; }
  });
  return button;
}
function define(list, term, text, className) { list.append(node('dt', term), node('dd', text, className)); }
function options(q, feedback) {
  const answering = exam.status === 'question' && !exam.readOnly, list = node('ol', undefined, 'options' + (answering ? '' : ' graded'));
  q.options.forEach((option, n) => {
    const row = node(answering ? 'label' : 'div', undefined, 'option');
    if (answering) {
      const input = node('input');
      input.type = 'radio'; input.name = 'answer-' + q.id; input.value = String(n + 1); input.checked = chosen(q) === n + 1;
      input.addEventListener('change', () => setDraft(q, input.value));
      row.append(input);
    } else {
      if (chosen(q) === n + 1) row.classList.add('is-mine');
      if (feedback && option === feedback.answer) row.classList.add('is-answer');
    }
    row.append(node('span', String(n + 1), 'bubble'), node('span', option, 'option-text'));
    const item = node('li'); item.append(row); list.append(item);
  });
  return list;
}
function orderingInput(q, index) {
  const wrap = node('div', undefined, 'ordering-input'), help = node('p', '블록을 끌어 순서 칸에 놓거나, 블록과 칸을 차례로 눌러 주세요.', 'ordering-help');
  const bank = node('div', undefined, 'ordering-bank'), slots = node('ol', undefined, 'ordering-slots');
  let order = orderingValue(q).filter(item => Number.isInteger(item) && item >= 0 && item < q.blocks.length);
  order = Array.from({length: q.blocks.length}, (_, slot) => order[slot] ?? null);
  let selected = -1, dragging = -1;
  const save = () => setDraft(q, JSON.stringify(order));
  const place = (block, slot) => {
    if (block < 0) return;
    const oldSlot = order.indexOf(block), previous = order[slot];
    if (oldSlot >= 0) order[oldSlot] = previous === null ? null : previous;
    else if (previous !== null) order[slot] = null;
    order[slot] = block; selected = dragging = -1; save(); draw();
  };
  const draw = () => {
    bank.replaceChildren(...q.blocks.map((text, block) => {
      if (order.includes(block)) return null;
      const button = node('button', text, 'order-block' + (selected === block ? ' selected' : ''));
      button.type = 'button'; button.draggable = true; button.addEventListener('dragstart', () => { dragging = block; });
      button.addEventListener('click', () => { selected = selected === block ? -1 : block; draw(); }); return button;
    }).filter(Boolean));
    slots.replaceChildren(...order.map((block, slot) => {
      const item = node('li', undefined, 'order-slot' + (block === null ? '' : ' filled'));
      item.addEventListener('dragover', event => event.preventDefault()); item.addEventListener('drop', event => { event.preventDefault(); place(dragging, slot); });
      const button = node('button', (slot + 1) + '. ' + (block === null ? '여기에 놓기' : q.blocks[block]), 'order-slot-button');
      button.type = 'button'; button.setAttribute('aria-label', (slot + 1) + '번째 순서 칸');
      button.addEventListener('click', () => block === null ? place(selected, slot) : (order[slot] = null, selected = -1, save(), draw()));
      item.append(button); return item;
    }));
  };
  wrap.append(help, node('p', '아직 배치하지 않은 블록', 'ordering-label'), bank, node('p', '정해진 순서', 'ordering-label'), slots); draw(); return wrap;
}
function answerInput(q, index) {
  if (q.responseFormat === 'ordering') return orderingInput(q, index);
  const input = node(q.type === 'short' ? 'input' : 'textarea', undefined, q.responseFormat === 'code' ? 'code' : q.type === 'short' ? 'short' : 'essay');
  if (q.type === 'short') input.type = 'text'; else input.rows = 4;
  if (q.responseFormat === 'code') input.spellcheck = false;
  input.maxLength = 50000; input.value = drafts[q.id] || '';
  input.setAttribute('aria-label', (index + 1) + '번 답안');
  input.placeholder = q.responseFormat === 'code' ? '코드를 써 주세요' : q.type === 'short' ? '답' : '답을 문장으로 써 주세요';
  input.addEventListener('input', () => setDraft(q, input.value));
  return input;
}
function result(q, feedback) {
  const box = node('div', undefined, 'result'), list = node('dl');
  box.append(node('p', OUTCOME[feedback.outcome] + ' ' + feedback.score + '/' + feedback.points + '점', 'outcome'));
  if (q.responseFormat !== 'choice') define(list, '내 답', answered(q) ? displayAnswer(q, drafts[q.id]) : '답을 쓰지 않았어요.', q.responseFormat === 'code' && answered(q) ? 'code' : '');
  else if (!chosen(q)) define(list, '내 답', '답을 고르지 않았어요.');
  if (q.responseFormat !== 'choice') define(list, '모범 답안', feedback.answer, q.responseFormat === 'code' ? 'code' : '');
  define(list, '해설', feedback.explanation);
  box.append(list);
  if (feedback.criteria.length) {
    const criteria = node('ul', undefined, 'criteria');
    feedback.criteria.forEach(c => {
      const item = node('li', undefined, c.met ? 'met' : 'unmet');
      item.append(node('strong', c.criterion), node('span', c.feedback)); criteria.append(item);
    });
    box.append(criteria);
  }
  feedback.evidence.forEach(ref => {
    const figure = node('figure', undefined, 'evidence'), quote = node('blockquote');
    quote.append(node('span', ref.quote));
    figure.append(quote, node('figcaption', ref.name + ', ' + ref.location)); box.append(figure);
  });
  return box;
}
function questionItem(q, index, feedback) {
  const item = node('li', undefined, 'question'), num = node('div', String(index + 1), 'num'), body = node('div', undefined, 'body'), text = node('p', undefined, 'q-text');
  item.id = 'q-' + index;
  item.addEventListener('pointerdown', () => focusQuestion(q)); item.addEventListener('focusin', () => focusQuestion(q));
  if (feedback) num.append(mark(feedback.outcome, index));
  if (q.responseFormat !== 'choice') text.append(node('span', '[' + q.typeLabel + ']', 'kind'), ' ');
  text.append(q.question, ' ', node('span', '[' + q.points + '점]', 'points'));
  if (Number.isFinite(Number(exam.questionTimes?.[q.id]))) text.append(' ', node('span', '[풀이 ' + formatDuration(exam.questionTimes[q.id]) + ']', 'question-time'));
  const head = node('div', undefined, 'question-head'); head.append(text, confusionButton(q)); body.append(head);
  if (q.code) { const pre = node('pre', q.code); if (q.language) pre.dataset.language = q.language; body.append(pre); }
  if (q.responseFormat === 'choice') body.append(options(q, feedback));
  else if (exam.status === 'question' && !exam.readOnly) body.append(answerInput(q, index));
  if (feedback) body.append(result(q, feedback));
  else if (exam.status !== 'question' || exam.readOnly) {
    if (q.responseFormat !== 'choice') { const list = node('dl'); define(list, '내 답', answered(q) ? displayAnswer(q, drafts[q.id]) : '답을 쓰지 않았어요.', q.responseFormat === 'code' && answered(q) ? 'code' : ''); body.append(list); }
    body.append(node('p', exam.readOnly ? '당시 저장된 답안이에요.' : '채점을 기다리고 있어요.', 'pending'));
  }
  item.append(num, body);
  return item;
}
function omrRow(q, index, feedback) {
  const row = node('li', undefined, 'omr-row'), link = node('a', String(index + 1), 'omr-num'), answering = exam.status === 'question' && !exam.readOnly;
  row.id = 'row-' + q.id;
  link.href = '#q-' + index; link.id = 'jump-' + index; link.setAttribute('aria-label', (index + 1) + '번 문항으로 이동');
  link.addEventListener('click', () => focusQuestion(q));
  row.append(link);
  if (q.responseFormat === 'choice') {
    // The paper's radio buttons are the accessible control; these bubbles mirror them.
    const bubbles = node('div', undefined, 'bubbles'); bubbles.setAttribute('aria-hidden', 'true');
    q.options.forEach((_, n) => {
      const bubble = node(answering ? 'button' : 'span', String(n + 1), 'omr-bubble' + (chosen(q) === n + 1 ? ' filled' : ''));
      if (answering) {
        bubble.type = 'button'; bubble.tabIndex = -1;
        bubble.addEventListener('click', () => {
          setDraft(q, String(n + 1));
          const radio = document.querySelector('input[name="answer-' + CSS.escape(q.id) + '"][value="' + (n + 1) + '"]');
          if (radio) radio.checked = true;
        });
      }
      bubbles.append(bubble);
    });
    row.append(bubbles);
  } else row.append(node('span', q.typeLabel, 'omr-bar' + (answered(q) ? ' filled' : '')));
  if (feedback) row.append(node('span', GLYPH[feedback.outcome], 'omr-result'));
  return row;
}
function render(data) {
  exam = data; exam.confusedIds = data.confusedIds || []; revision = data.revision;
  drafts = Object.fromEntries(data.questions.map(q => [q.id, data.drafts[q.id] || '']));
  const finished = data.status === 'finished', feedback = Object.fromEntries((data.feedback || []).map(item => [item.questionId, item]));
  $('title').textContent = data.title; document.title = data.title + ' 연습 시험';
  $('total').textContent = '수업자료에서 만든 연습 시험 ' + data.questions.length + '문항, ' + data.totalPoints + '점 만점이에요. 실제 학교 시험이나 예상 기출이 아니에요.';
  $('phase').textContent = {question: '시험 진행 중', grading: '채점 기다리는 중', finished: '채점 완료'}[data.status] || data.status;
  $('exam').classList.toggle('answering', data.status === 'question' && !data.readOnly);
  $('questions').replaceChildren(...data.questions.map((q, i) => questionItem(q, i, feedback[q.id])));
  $('nav').replaceChildren(...data.questions.map((q, i) => omrRow(q, i, feedback[q.id])));
  $('submit-area').hidden = data.status !== 'question' || data.readOnly;
  if (data.status !== 'question') $('saved').textContent = '';
  resetConfirm(); progress();
  $('notice').hidden = data.status === 'question';
  $('sets-link').hidden = !data.collectionId;
  syncClock(data);
  $('notice').classList.toggle('grading', data.status === 'grading');
  if (data.status === 'grading') $('notice').textContent = '답안을 잘 받았어요. Codex가 평가 기준을 확인해 채점하면 이 화면에 결과가 나타나요. 채점이 시작되지 않으면 대화에서 “시험 채점해줘”라고 말해주세요.';
  $('score').hidden = !finished; $('review').hidden = true;
  if (finished) {
    const counts = data.summary.counts;
    $('notice').textContent = '맞은 문항 ' + counts.correct + '개, 일부만 맞은 문항 ' + counts.partial + '개, 틀린 문항 ' + counts.incorrect + '개예요. 기준별 평가와 수업자료 근거를 함께 확인해보세요.';
    $('score-got').textContent = data.summary.score; $('score-total').textContent = '/' + data.totalPoints;
    $('score').setAttribute('aria-label', data.totalPoints + '점 만점에 ' + data.summary.score + '점');
    $('score').style.setProperty('--i', data.questions.length);
    const seen = new Set(), items = [];
    data.summary.review.forEach(entry => {
      if (seen.has(entry.concept)) return;
      seen.add(entry.concept);
      const item = node('li');
      item.append(node('strong', entry.concept), node('span', [...new Set(entry.sources.map(s => s.name + ', ' + s.location))].join(' / ')));
      items.push(item);
    });
    $('review-list').replaceChildren(...items); $('review').hidden = !items.length;
  }
  if (data.readOnly) {
    $('phase').textContent = '지난 시험 돌아보기';
    if (!finished) {
      $('notice').hidden = false;
      $('notice').textContent = '보관한 시험지예요. 당시 답안은 수정하거나 다시 제출할 수 없어요. 채점이 진행 중이라면 완료 후 이 화면을 새로고침해 주세요.';
    }
  }
}
function resetConfirm() {
  $('confirm-text').hidden = true; $('cancel').hidden = true;
  $('submit').textContent = '답안 제출하기'; delete $('submit').dataset.armed;
}
async function submit() {
  if (submitting || exam.readOnly) return;
  // First press explains what cannot be undone; the second press submits.
  if (!$('submit').dataset.armed) {
    const blank = exam.questions.map((q, i) => answered(q) ? '' : (i + 1) + '번').filter(Boolean);
    $('confirm-text').textContent = (blank.length ? blank.join(', ') + ' 문항이 비어 있어요. 빈 답안은 미응답으로 제출돼요. ' : '') + '제출하면 답안을 고칠 수 없어요.';
    $('confirm-text').hidden = false; $('cancel').hidden = false;
    $('submit').textContent = '제출하기'; $('submit').dataset.armed = '1';
    return;
  }
  const submittedAnswers = {...drafts};
  submitting = true; document.body.classList.add('busy');
  $('exam').querySelectorAll('input,textarea,button').forEach(el => { el.disabled = true; });
  clearTimeout(timer); error('');
  try {
    await focusRequest;
    await saveDraft();
    render(await api('/api/submit', {examId: exam.examId, answers: submittedAnswers, revision}));
  } catch (e) { error(e.message); $('exam').querySelectorAll('input,textarea,button').forEach(el => { el.disabled = false; }); }
  finally { submitting = false; document.body.classList.remove('busy'); }
}
$('submit').addEventListener('click', submit);
$('cancel').addEventListener('click', resetConfirm);
window.addEventListener('beforeunload', event => {
  if (exam?.status === 'question' && dirty) { event.preventDefault(); event.returnValue = ''; }
});
(async () => {
  try { render(await api('/api/exam')); } catch (e) { $('title').textContent = '시험지를 열지 못했어요'; $('submit-area').hidden = true; error(e.message); return; }
  if (exam.readOnly || exam.status === 'finished') return;
  const poll = async () => {
    if (exam.status === 'finished') return;
    if (document.hidden) { setTimeout(poll, 3000); return; }
    let stopped = false;
    try {
      const data = await api('/api/exam');
      if (data.examId !== exam.examId) { error('새 시험지가 만들어졌어요. 대화에서 새 시험 링크를 열어주세요.'); $('submit').disabled = true; stopped = true; return; }
      // Answers are locked once the status changes, so a full redraw cannot lose typing.
      if (data.status !== exam.status && !submitting) render(data);
      stopped = exam.status === 'finished';
    } catch (e) { error(e.message); }
    finally { if (!stopped) setTimeout(poll, 3000); }
  };
  setTimeout(poll, 3000);
})();
