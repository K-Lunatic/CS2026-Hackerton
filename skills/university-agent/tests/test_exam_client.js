// Run with node: token bootstrap must work even when browser storage is blocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../assets/exam.js'), 'utf8').split('const SVG')[0];
const token = 'x'.repeat(43);
for (const blocked of [false, true]) {
  const context = {document: {}, location: {hash: '#' + token},
    sessionStorage: {getItem() { if (blocked) throw Error('Storage disabled'); return ''; },
      setItem(key, value) { if (blocked) throw Error('Storage disabled'); assert.equal(value, token); }},
    history: {replaceState() { context.location.hash = ''; }}};
  vm.createContext(context);
  vm.runInContext(source, context);
  assert.equal(vm.runInContext('token', context), token);
  assert.equal(context.location.hash, blocked ? '#' + token : '');
}
console.log('Exam token bootstrap: OK (normal + blocked storage)');

// Read-only review renders no answer controls, does not submit and does not poll.
async function checkReview() {
  const created = [], elements = new Map();
  function element(tag) {
    created.push(tag);
    return {children: [], dataset: {}, style: {setProperty() {}}, classList: {add() {}, toggle() {}},
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
      setAttribute() {}, addEventListener() {}};
  }
  let requests = 0, polls = 0;
  const review = {readOnly: true, status: 'question', examId: 'old', title: '지난 시험', revision: 1,
    totalPoints: 20, drafts: {q1: '1', q2: '당시 답'}, questions: [
      {id: 'q1', type: 'mcq', responseFormat: 'choice', question: '선택', points: 10, options: ['가', '나']},
      {id: 'q2', type: 'essay', typeLabel: '서술형', responseFormat: 'text', question: '설명', points: 10, options: []}]};
  const context = {document: {getElementById(id) { if (!elements.has(id)) elements.set(id, element('existing')); return elements.get(id); },
      createElement: element, createElementNS(_, tag) { return element(tag); }}, location: {hash: '#' + token},
    sessionStorage: {getItem() { return ''; }, setItem() {}}, history: {replaceState() {}},
    window: {addEventListener() {}}, setInterval() { polls++; },
    fetch: async () => { requests++; return {ok: true, json: async () => review}; }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../assets/exam.js'), 'utf8'), context);
  await new Promise(setImmediate);
  assert.equal(elements.get('submit-area').hidden, true);
  assert.equal(created.includes('input') || created.includes('textarea'), false);
  await vm.runInContext('submit()', context);
  assert.equal(requests, 1);
  assert.equal(polls, 0);
  console.log('Past exam review: OK (no edits, submissions or polling)');
}
checkReview().catch(error => { console.error(error); process.exitCode = 1; });

async function checkSaveRace() {
  const script = fs.readFileSync(require('node:path').join(__dirname, '../assets/exam.js'), 'utf8');
  const elements = {};
  let finish;
  const context = {$: id => elements[id] || (elements[id] = {}), error() {},
    api: () => new Promise(resolve => { finish = () => resolve({revision: 1}); })};
  vm.createContext(context);
  vm.runInContext("let exam={readOnly:false,examId:'test'},drafts={q1:'old'},revision=0,saving=Promise.resolve(),dirty=true,editVersion=1;" +
    script.slice(script.indexOf('function saveDraft()'), script.indexOf('function setDraft(')), context);
  const saving = vm.runInContext('saveDraft()', context);
  await new Promise(setImmediate);
  vm.runInContext("drafts.q1='new';editVersion++;dirty=true;", context);
  finish(); await saving;
  assert.equal(vm.runInContext('dirty', context), true);
  assert.notEqual(elements.saved.textContent, '답안 저장 완료');
  console.log('In-flight save: OK (new edits remain unsaved until acknowledged)');
}
checkSaveRace().catch(error => { console.error(error); process.exitCode = 1; });

async function checkPolling() {
  const elements = new Map(), callbacks = [];
  function element() {
    return {dataset: {}, style: {setProperty() {}}, classList: {add() {}, toggle() {}},
      append() {}, replaceChildren() {}, setAttribute() {}, addEventListener() {}};
  }
  const exam = {readOnly: false, status: 'question', examId: 'same', title: '시험', revision: 0,
    totalPoints: 10, drafts: {}, questions: [{id: 'q1', type: 'mcq', responseFormat: 'choice', question: '문제', points: 10, options: ['가','나']}]};
  let requests = 0, finish;
  const context = {document: {getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
      createElement: element, createElementNS: element}, location: {hash: '#' + token},
    sessionStorage: {getItem() { return ''; }, setItem() {}}, history: {replaceState() {}},
    window: {addEventListener() {}}, setTimeout(fn) { callbacks.push(fn); },
    fetch: async () => {
      requests++;
      if (requests === 1) return {ok:true, json: async () => exam};
      return new Promise(resolve => { finish = () => resolve({ok:true, json:async () => ({...exam,status:'finished',summary:{score:0,counts:{correct:0,partial:0,incorrect:1},review:[]}})}); });
    }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../assets/exam.js'), 'utf8'), context);
  await new Promise(setImmediate);
  const pending = callbacks.shift()();
  await new Promise(setImmediate);
  assert.equal(callbacks.length, 0); // Never schedule overlapping requests.
  vm.runInContext('submitting = true', context);
  finish(); await pending;
  assert.equal(callbacks.length, 1); // Submission deferred the redraw; keep checking.
  vm.runInContext('submitting = false', context);
  const finalPoll = callbacks.shift()();
  await new Promise(setImmediate);
  finish(); await finalPoll;
  assert.equal(callbacks.length, 0); // Finished grading stops polling.
  assert.equal(requests, 3);
  console.log('Result polling: OK (no overlap, submission race, stops after grading)');
}
checkPolling().catch(error => { console.error(error); process.exitCode = 1; });
