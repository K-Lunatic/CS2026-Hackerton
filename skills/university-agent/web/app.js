// Quiz page for one study conversation. The server only returns answers for finished questions.
const $ = (id) => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';
const DRAFT_KEY = 'turtleneck-drafts';
const TYPE_LABEL = { short: '단답형', essay: '서술형' };
const OUTCOME = {
  correct: '맞았어요', incorrect: '틀렸어요', partial: '일부만 맞았어요',
  skipped: '건너뛴 문제예요', revealed: '정답을 먼저 확인한 문제예요',
};
// Red-pencil marks drawn over the question number: circle, slash, triangle.
const MARK = {
  correct: 'M33 7 C18 4 6 15 6 30 C6 46 19 55 33 54 C47 53 56 42 55 28 C54 14 43 5 27 9',
  incorrect: 'M49 6 C38 22 24 40 9 55',
  partial: 'M30 7 L53 50 L7 49 L31 6',
};
const GLYPH = { correct: '○', incorrect: '／', partial: '△', skipped: '–', revealed: '–' };

let view = null;
let drafts = {};
let signature = null;
let busy = false;
let timer = null;
const drawn = new Set();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Typed answers survive a reload; a different question set starts empty.
function loadDrafts(next) {
  if (next === signature) return;
  signature = next;
  try {
    const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || '{}');
    drafts = saved.signature === signature ? saved.drafts : {};
  } catch { drafts = {}; }
}

function saveDrafts() {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ signature, drafts }));
  } catch { /* Answers stay on screen even when storage is unavailable. */ }
}

const draft = (q) => (drafts[q.id] || '').trim();
const open = () => view.questions.filter((q) => !q.result);
const number = (q) => view.questions.indexOf(q) + 1;

async function request(event) {
  const response = await fetch('/api/quiz', event ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(event),
  } : undefined);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '처리하지 못했어요. 다시 시도해 주세요.');
  return data;
}

async function act(...events) {
  if (busy) return;
  busy = true;
  document.body.classList.add('busy');
  $('error').hidden = true;
  try {
    let next = view;
    for (const event of events) next = await request(event);
    render(next);
  } catch (error) {
    $('error').textContent = error.message;
    $('error').hidden = false;
    try { render(await request()); } catch { /* Keep the current screen. */ }
  } finally {
    busy = false;
    document.body.classList.remove('busy');
  }
}

// A second click confirms actions that cannot be undone.
function confirming(button, question, run) {
  const label = button.textContent;
  button.addEventListener('click', () => {
    if (button.dataset.armed) { run(); return; }
    button.dataset.armed = '1';
    button.textContent = question;
  });
  button.addEventListener('blur', () => {
    delete button.dataset.armed;
    button.textContent = label;
  });
}

function mark(outcome, index) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 60 60');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('mark');
  svg.style.setProperty('--i', index);
  const path = document.createElementNS(SVG, 'path');
  path.setAttribute('d', MARK[outcome]);
  path.setAttribute('pathLength', '1');
  svg.append(path);
  return svg;
}

function answerInput(q) {
  if (q.type === 'mcq') {
    const list = el('ol', 'options');
    q.options.forEach((option, i) => {
      const label = el('label', 'option');
      const input = el('input');
      input.type = 'radio';
      input.name = q.id;
      input.value = String(i + 1);
      input.checked = drafts[q.id] === input.value;
      input.addEventListener('change', () => setDraft(q, input.value));
      label.append(input, el('span', 'bubble', String(i + 1)), el('span', 'option-text', option));
      const item = el('li');
      item.append(label);
      list.append(item);
    });
    return list;
  }
  const input = el(q.type === 'essay' ? 'textarea' : 'input', q.type === 'essay' ? 'essay' : 'short');
  if (q.type === 'essay') input.rows = 4;
  input.maxLength = 4000;
  input.value = drafts[q.id] || '';
  input.setAttribute('aria-label', `${number(q)}번 답`);
  input.placeholder = q.type === 'essay' ? '답을 문장으로 써 주세요' : '답';
  input.addEventListener('input', () => setDraft(q, input.value));
  return input;
}

function tools(q) {
  const row = el('div', 'tools');
  if (!q.hint) {
    const hint = el('button', 'tool', '힌트 보기');
    hint.type = 'button';
    hint.addEventListener('click', () => act({ action: 'hint', questionId: q.id }));
    row.append(hint);
  }
  const skip = el('button', 'tool', '건너뛰기');
  skip.type = 'button';
  confirming(skip, '다시 풀 수 없어요. 건너뛸까요?', () => act({ action: 'skip', questionId: q.id }));
  const reveal = el('button', 'tool', '정답 보기');
  reveal.type = 'button';
  confirming(reveal, '채점에서 빠져요. 정답을 볼까요?', () => act({ action: 'reveal', questionId: q.id }));
  row.append(skip, reveal);
  return row;
}

function definition(list, term, text, className) {
  list.append(el('dt', '', term), el('dd', className, text));
}

function gradedOptions(q) {
  const list = el('ol', 'options graded');
  q.options.forEach((option, i) => {
    const item = el('li', 'option');
    if (option === q.result.answer) item.classList.add('is-answer');
    if (option === submittedOption(q)) item.classList.add('is-mine');
    item.append(el('span', 'bubble', String(i + 1)), el('span', 'option-text', option));
    list.append(item);
  });
  return list;
}

// The session stores the submitted choice as a number or as the option text.
function submittedOption(q) {
  const text = q.result ? q.result.submitted : q.submitted;
  if (text == null) return null;
  return /^\d+$/.test(text) && q.options[Number(text) - 1] !== undefined ? q.options[Number(text) - 1] : text;
}

function result(q) {
  const r = q.result;
  const box = el('div', 'result');
  box.append(el('p', `outcome ${r.outcome}`, OUTCOME[r.outcome] + (r.hintUsed ? ' 힌트를 봤어요.' : '')));
  const list = el('dl');
  if (q.type === 'mcq') {
    if (r.submitted == null) definition(list, '정답', r.answer);
  } else {
    if (r.submitted != null) definition(list, '내 답', r.submitted);
    definition(list, '모범 답안', r.answer);
  }
  definition(list, '해설', r.explanation);
  box.append(list);
  if (r.criteria.length) {
    const criteria = el('ul', 'criteria');
    r.criteria.forEach((c) => {
      const item = el('li', c.met ? 'met' : 'unmet');
      item.append(el('strong', '', c.criterion), el('span', '', c.feedback));
      criteria.append(item);
    });
    box.append(criteria);
  }
  r.evidence.forEach((source) => {
    const figure = el('figure', 'evidence');
    const quote = el('blockquote');
    quote.append(el('span', '', source.quote));
    figure.append(quote, el('figcaption', '', `${source.name}, ${source.location}`));
    box.append(figure);
  });
  return box;
}

function questionItem(q, index) {
  const item = el('li', 'question');
  item.id = `q-${q.id}`;
  const num = el('div', 'num', String(index + 1));
  if (q.result && MARK[q.result.outcome]) {
    const svg = mark(q.result.outcome, drawn.size);
    if (drawn.has(q.id)) svg.classList.add('still');
    num.append(svg);
  }
  if (q.result) drawn.add(q.id);
  const body = el('div', 'body');
  const text = el('p', 'q-text');
  if (TYPE_LABEL[q.type]) text.append(el('span', 'kind', `[${TYPE_LABEL[q.type]}]`), ' ');
  text.append(q.question);
  body.append(text);
  if (q.result) {
    if (q.type === 'mcq') body.append(gradedOptions(q));
    body.append(result(q));
  } else if (view.status === 'question') {
    body.append(answerInput(q));
    if (q.hint) {
      const hint = el('p', 'hint');
      hint.append(el('strong', '', '힌트'), ` ${q.hint}`);
      body.append(hint);
    }
    body.append(tools(q));
  } else if (q.submitted !== undefined) {
    const list = el('dl');
    definition(list, '내 답', q.submitted);
    body.append(list, el('p', 'pending', '채점을 기다리고 있어요.'));
  } else {
    if (q.type === 'mcq') {
      const list = el('ol', 'options graded');
      q.options.forEach((option, i) => {
        const row = el('li', 'option');
        row.append(el('span', 'bubble', String(i + 1)), el('span', 'option-text', option));
        list.append(row);
      });
      body.append(list);
    }
    body.append(el('p', 'pending', '풀지 않은 문제예요.'));
  }
  item.append(num, body);
  return item;
}

function omrRow(q, index) {
  const row = el('li', 'omr-row');
  row.dataset.id = q.id;
  const link = el('a', 'omr-num', String(index + 1));
  link.href = `#q-${q.id}`;
  link.setAttribute('aria-label', `${index + 1}번 문제로 이동`);
  row.append(link);
  const answering = !q.result && view.status === 'question';
  if (q.type === 'mcq') {
    const chosen = q.result || q.submitted !== undefined ? q.options.indexOf(submittedOption(q)) + 1 : Number(drafts[q.id] || 0);
    const bubbles = el('div', 'bubbles');
    bubbles.setAttribute('aria-hidden', 'true');
    q.options.forEach((_, i) => {
      const bubble = el(answering ? 'button' : 'span', 'omr-bubble', String(i + 1));
      if (chosen === i + 1) bubble.classList.add('filled');
      if (answering) {
        bubble.type = 'button';
        bubble.tabIndex = -1;
        bubble.addEventListener('click', () => {
          setDraft(q, String(i + 1));
          const radio = document.querySelector(`input[name="${CSS.escape(q.id)}"][value="${i + 1}"]`);
          if (radio) radio.checked = true;
        });
      }
      bubbles.append(bubble);
    });
    row.append(bubbles);
  } else {
    const bar = el('span', 'omr-bar', TYPE_LABEL[q.type]);
    if (q.result ? q.result.submitted != null : q.submitted !== undefined || draft(q)) bar.classList.add('filled');
    row.append(bar);
  }
  if (q.result) row.append(el('span', `omr-result ${q.result.outcome}`, GLYPH[q.result.outcome]));
  return row;
}

function setDraft(q, value) {
  drafts[q.id] = value;
  saveDrafts();
  const row = document.querySelector(`.omr-row[data-id="${CSS.escape(q.id)}"]`);
  if (row) row.replaceWith(omrRow(q, view.questions.indexOf(q)));
  progress();
  $('blank-warning').hidden = true;
  $('submit-skip').hidden = true;
}

function progress() {
  const answered = view.questions.filter((q) => q.result || q.submitted !== undefined || draft(q)).length;
  $('progress').textContent = view.status === 'question' ? `${view.questions.length}문제 중 ${answered}문제에 답했어요`
    : view.status === 'grading' ? '채점하고 있어요' : '채점이 끝났어요';
}

function review() {
  const seen = new Set();
  const items = [];
  view.questions.forEach((q) => {
    const r = q.result;
    if (!r || (r.outcome === 'correct' && !r.hintUsed) || seen.has(r.concept)) return;
    seen.add(r.concept);
    const item = el('li');
    item.append(el('strong', '', r.concept), el('span', '', [...new Set(r.evidence.map((s) => `${s.name}, ${s.location}`))].join(' / ')));
    items.push(item);
  });
  $('review-list').replaceChildren(...items);
  $('review').hidden = view.status !== 'finished' || !items.length;
}

function notice() {
  const waiting = view.questions.filter((q) => q.submitted !== undefined).length;
  const counts = view.counts;
  let text = '';
  if (view.status === 'grading') {
    text = `객관식은 채점했어요. 단답형·서술형 ${waiting}문제는 터틀넥과의 대화로 돌아가 “채점해줘”라고 말하면 채점돼요. 채점이 끝나면 이 화면이 바뀌어요.`;
  } else if (view.status === 'finished') {
    const parts = [`힌트 없이 맞힌 문제는 ${view.selfCorrect}개예요.`];
    const extra = [['partial', '일부만 맞은 문제'], ['skipped', '건너뛴 문제'], ['revealed', '정답을 먼저 본 문제']]
      .filter(([key]) => counts[key]).map(([key, label]) => `${label} ${counts[key]}개`);
    const left = open().length;
    if (left) extra.push(`풀지 않은 문제 ${left}개`);
    if (extra.length) parts.push(extra.join(', ') + '가 있어요.');
    text = parts.join(' ');
  }
  $('notice').textContent = text;
  $('notice').hidden = !text;
  $('notice').classList.toggle('grading', view.status === 'grading');
}

function renderWaiting() {
  $('exam').hidden = true;
  $('waiting').hidden = false;
  $('waiting-title').textContent = view.single ? '한 문제씩 풀기는 대화에서 진행해요' : '아직 풀 문제가 없어요';
  $('waiting-text').textContent = view.single
    ? '이 화면은 여러 문제를 한 번에 푸는 연습문제를 보여줘요. 대화에서 계속 풀어 주세요.'
    : '터틀넥과의 대화에서 문제를 요청하면 이 화면에 바로 나타나요. 이렇게 말해 보세요.';
  $('waiting-example').textContent = '자료구조 3주차 자료로 문제 10개 만들어줘';
  $('waiting-example').hidden = Boolean(view.single);
}

function render(next) {
  view = next;
  clearTimeout(timer);
  const active = ['question', 'grading', 'finished'].includes(view.status) && view.questions.length;
  if (!active || view.status === 'grading') timer = setTimeout(poll, 3000);
  if (!active) { renderWaiting(); return; }
  loadDrafts(view.questions.map((q) => q.id).join('|'));
  $('waiting').hidden = true;
  $('exam').hidden = false;
  $('exam').classList.toggle('answering', view.status === 'question');
  $('title').textContent = view.materials.join(', ');
  $('subtitle').textContent = `수업자료에서 만든 연습문제 ${view.questions.length}문항이에요. 실제 시험 문제나 출제 예측이 아니에요.`;
  document.title = `${view.materials.join(', ')} 연습문제`;
  $('score').hidden = view.status !== 'finished';
  $('score-got').textContent = view.counts.correct;
  $('score-total').textContent = `/${view.questions.length}`;
  $('score').setAttribute('aria-label', `${view.questions.length}문제 중 ${view.counts.correct}문제 정답`);
  $('score').style.setProperty('--i', view.questions.length);
  notice();
  review();
  $('questions').replaceChildren(...view.questions.map(questionItem));
  $('omr-rows').replaceChildren(...view.questions.map(omrRow));
  $('submit-area').hidden = view.status !== 'question';
  $('blank-warning').hidden = true;
  $('submit-skip').hidden = true;
  progress();
}

async function poll() {
  if (busy) { timer = setTimeout(poll, 3000); return; }
  try {
    const next = await request();
    // Re-render only on change so typed answers and scroll position stay put.
    if (JSON.stringify(next) !== JSON.stringify(view)) render(next);
    else timer = setTimeout(poll, 3000);
  } catch { timer = setTimeout(poll, 3000); }
}

const submitEvent = (questions) => ({
  action: 'submit', answers: questions.map((q) => ({ questionId: q.id, text: draft(q) })),
});

$('submit').addEventListener('click', () => {
  const blanks = open().filter((q) => !draft(q));
  if (!blanks.length) { act(submitEvent(open())); return; }
  $('blank-warning').textContent = `${blanks.map((q) => `${number(q)}번`).join(', ')} 문제가 비어 있어요. 답을 쓰거나 건너뛰어 주세요.`;
  $('blank-warning').hidden = false;
  $('submit-skip').hidden = false;
  $(`q-${blanks[0].id}`).scrollIntoView({ behavior: 'smooth', block: 'center' });
});

$('submit-skip').addEventListener('click', () => {
  const blanks = open().filter((q) => !draft(q));
  const answered = open().filter((q) => draft(q));
  const events = blanks.map((q) => ({ action: 'skip', questionId: q.id }));
  if (answered.length) events.push(submitEvent(answered));
  act(...events);
});

confirming($('stop'), '남은 문제는 채점하지 않아요. 그만 풀까요?', () => act({ action: 'stop' }));

request().then(render).catch(() => {
  view = { status: 'idle', questions: [] };
  renderWaiting();
  $('waiting-title').textContent = '문제를 불러오지 못했어요';
  $('waiting-text').textContent = '이 화면을 연 프로그램이 꺼졌을 수 있어요. 터틀넥과의 대화에서 화면을 다시 열어 달라고 요청해 주세요.';
  $('waiting-example').hidden = true;
});
