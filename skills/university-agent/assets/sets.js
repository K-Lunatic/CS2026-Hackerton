'use strict';
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
let savedToken = '';
try { savedToken = sessionStorage.getItem('exam-token') || ''; } catch (_) {}
const token = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : savedToken;
try { sessionStorage.setItem('exam-token', token); history.replaceState(null, '', '/'); } catch (_) {}

function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, {method: data ? 'POST' : 'GET', headers: {'X-Exam-Token': token, 'Content-Type': 'application/json'}, body: data ? JSON.stringify(data) : undefined});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '문제 세트 연결을 확인해주세요.');
  return result;
}
function setButton(item, collectionId) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'set-choice';
  const heading = document.createElement('strong');
  heading.textContent = item.index + '세트';
  const detail = document.createElement('span');
  detail.textContent = item.questionCount + '문항 · ' + (item.completed ? '다시 풀기' : '아직 풀지 않음');
  button.append(heading, detail);
  button.addEventListener('click', async () => {
    document.querySelectorAll('.set-choice').forEach(choice => { choice.disabled = true; });
    error(''); $('phase').textContent = '세트를 열고 있어요';
    try {
      await api('/api/sets/select', {collectionId, setId: item.setId});
      location.href = '/exam#' + token;
    } catch (e) {
      error(e.message); $('phase').textContent = '세트 선택';
      document.querySelectorAll('.set-choice').forEach(choice => { choice.disabled = false; });
    }
  });
  return button;
}
function render(data) {
  document.title = data.title + ' · 터틀넥 문제 세트';
  $('title').textContent = data.title;
  $('subtitle').textContent = '전체 문제를 나눠 두었어요. 한 번에 한 세트씩 풀어 보세요.';
  $('phase').textContent = '세트 선택';
  $('progress').textContent = data.completedSets + ' / ' + data.totalSets + '세트 완료';
  $('set-list').replaceChildren(...data.sets.map(item => setButton(item, data.collectionId)));
}
(async () => {
  try { render(await api('/api/sets')); }
  catch (e) { $('title').textContent = '문제 세트를 열지 못했어요'; $('phase').textContent = '확인 필요'; error(e.message); }
})();
