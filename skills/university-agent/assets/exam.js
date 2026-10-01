'use strict';
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
const token = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : sessionStorage.getItem('exam-token') || '';
// Tab-scoped token survives refresh, but never enters a request URL or localStorage.
sessionStorage.setItem('exam-token', token);
history.replaceState(null, '', '/');
let exam, revision = 0, timer, saving = Promise.resolve(), submitting = false;
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
function answers() {
  return Object.fromEntries(exam.questions.map((q, i) => {
    const box = $('q-' + i);
    return [q.id, q.responseFormat === 'choice' ? (box.querySelector('input:checked')?.value || '') : box.querySelector('textarea,input[type=text]').value];
  }));
}
function progress() {
  const values = answers();
  let count = 0;
  exam.questions.forEach((q, i) => { const done = Boolean(values[q.id].trim()); $('jump-' + i).classList.toggle('done', done); count += Number(done); });
  $('progress').textContent = count + ' / ' + exam.questions.length + '문항 작성';
}
function saveDraft() {
  const draft = answers();
  saving = saving.catch(() => {}).then(async () => {
    $('saved').textContent = '답안을 저장하고 있어요…';
    const result = await api('/api/draft', {examId: exam.examId, answers: draft, revision});
    revision = result.revision;
    $('saved').textContent = '답안 저장 완료';
    error('');
  });
  // A failed save blocks this submission; the next edit may retry the connection.
  saving.catch(e => error(e.message));
  return saving;
}
function render(data) {
  exam = data; revision = data.revision;
  $('title').textContent = data.title;
  $('total').textContent = data.questions.length + '문항 · ' + data.totalPoints + '점';
  $('questions').replaceChildren(); $('nav').replaceChildren();
  data.questions.forEach((q, i) => {
    const field = node('fieldset'); field.id = 'q-' + i;
    field.append(node('legend', (i + 1) + '번 · ' + q.typeLabel + ' · ' + q.points + '점'), node('p', q.question, 'question'));
    if (q.code) field.append(node('p', q.language || '예제 코드'), node('pre', q.code));
    if (q.responseFormat === 'choice') {
      q.options.forEach((option, n) => {
        const label = node('label', undefined, 'option'), input = node('input');
        input.type = 'radio'; input.name = 'answer-' + i; input.value = String(n + 1);
        input.checked = data.drafts[q.id] === input.value || data.drafts[q.id] === option;
        label.append(input, node('span', (n + 1) + '. ' + option)); field.append(label);
      });
    } else {
      const input = node(q.type === 'short' ? 'input' : 'textarea');
      if (q.type === 'short') input.type = 'text';
      input.id = 'answer-' + i; input.maxLength = 50000; input.value = data.drafts[q.id] || '';
      if (q.responseFormat === 'code') { input.className = 'code'; input.spellcheck = false; }
      const label = node('label', '답안', 'answer'); label.htmlFor = input.id;
      field.append(label, input);
    }
    $('questions').append(field);
    const link = node('a', String(i + 1)); link.href = '#q-' + i; link.id = 'jump-' + i;
    link.setAttribute('aria-label', (i + 1) + '번 문항으로 이동'); $('nav').append(link);
  });
  progress(); update(data);
}
function update(data) {
  exam.status = data.status;
  const locked = data.status !== 'question';
  $('paper').querySelectorAll('input,textarea,button').forEach(el => { el.disabled = locked; });
  $('phase').textContent = {question: '시험 진행 중', grading: '채점 기다리는 중', finished: '채점 완료'}[data.status] || data.status;
  $('notice').hidden = !locked;
  if (data.status === 'grading') $('notice').textContent = '답안을 잘 받았어요. Codex가 평가 기준을 확인해 채점하면 이 화면에 결과가 나타나요. 채점이 시작되지 않으면 대화에서 “시험 채점해줘”라고 말해주세요.';
  if (data.status === 'finished') {
    $('notice').textContent = '풀이를 마쳤어요. 기준별 평가와 수업자료 근거를 함께 확인해보세요.';
    $('results').hidden = false; $('results').replaceChildren(node('h2', '채점 결과'), node('p', data.summary.score + ' / ' + data.totalPoints + '점', 'score'));
    data.feedback.forEach(item => {
      const i = data.questions.findIndex(q => q.id === item.questionId), section = node('article', undefined, 'result');
      section.append(node('h3', (i + 1) + '번 · ' + item.score + ' / ' + item.points + '점'), node('p', '모범 답안: ' + item.answer), node('p', item.explanation));
      item.criteria.forEach(c => section.append(node('p', (c.met ? '✓ ' : '↳ ') + c.criterion + ': ' + c.feedback)));
      item.evidence.forEach(ref => section.append(node('p', '근거 · ' + ref.name + ' / ' + ref.location + ' — ' + ref.quote)));
      $('results').append(section);
    });
  }
}
$('paper').addEventListener('input', () => {
  if (submitting) return;
  progress(); clearTimeout(timer); $('saved').textContent = '답안 변경됨';
  timer = setTimeout(saveDraft, 500);
});
$('paper').addEventListener('submit', async event => {
  event.preventDefault();
  if (submitting) return;
  const blank = Object.values(answers()).filter(v => !v.trim()).length;
  if (!confirm(blank ? blank + '문항이 비어 있어요. 미응답으로 제출할까요?' : '답안을 제출할까요? 제출 후에는 수정할 수 없어요.')) return;
  const submittedAnswers = answers();
  submitting = true; $('paper').querySelectorAll('input,textarea,button').forEach(el => { el.disabled = true; });
  clearTimeout(timer); error('');
  try {
    await saveDraft();
    update(await api('/api/submit', {examId: exam.examId, answers: submittedAnswers, revision}));
  } catch (e) { error(e.message); $('paper').querySelectorAll('input,textarea,button').forEach(el => { el.disabled = false; }); }
  finally { submitting = false; }
});
window.addEventListener('beforeunload', event => {
  if (exam?.status === 'question' && $('saved').textContent !== '답안 저장 완료' && $('saved').textContent) { event.preventDefault(); event.returnValue = ''; }
});
(async () => {
  try { render(await api('/api/exam')); } catch (e) { error(e.message); return; }
  setInterval(async () => {
    try {
      const data = await api('/api/exam');
      if (data.examId !== exam.examId) { error('새 시험지가 만들어졌어요. 대화에서 새 시험 링크를 열어주세요.'); $('submit').disabled = true; return; }
      if (data.status !== exam.status) { exam.status = data.status; update(data); }
    } catch (e) { error(e.message); }
  }, 3000);
})();
