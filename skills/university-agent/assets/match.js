'use strict';
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
let savedToken = '';
try { savedToken = sessionStorage.getItem('exam-token') || ''; } catch (_) {}
const token = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : savedToken;
try { sessionStorage.setItem('exam-token', token); history.replaceState(null, '', '/'); } catch (_) {}
let board, selectedLeft = '', selectedRight = '', locked = false;

function error(message) { $('error').textContent = message; $('error').hidden = !message; }
async function api(data) {
  const response = await fetch('/api/match', {method: data ? 'POST' : 'GET', headers: {'X-Exam-Token': token, 'Content-Type': 'application/json'}, body: data ? JSON.stringify(data) : undefined});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '매칭판 연결을 확인해주세요.');
  return result;
}
function tile(side, item) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'match-tile'; button.textContent = item.text;
  button.dataset.id = item.id; button.dataset.side = side;
  button.disabled = item.matched || locked;
  if (item.matched) button.classList.add('matched');
  if ((side === 'left' ? selectedLeft : selectedRight) === item.id) button.classList.add('selected');
  button.addEventListener('click', () => choose(side, item.id));
  return button;
}
function choose(side, id) {
  if (locked) return;
  if (side === 'left') selectedLeft = selectedLeft === id ? '' : id;
  else selectedRight = selectedRight === id ? '' : id;
  render(board);
  if (selectedLeft && selectedRight) pick();
}
async function pick() {
  locked = true; render(board); error('');
  const leftId = selectedLeft, rightId = selectedRight;
  try {
    board = await api({matchId: board.matchId, leftId, rightId});
    selectedLeft = ''; selectedRight = '';
    render(board);
  } catch (e) { error(e.message); locked = false; render(board); }
  finally { locked = false; render(board); }
}
function render(data) {
  board = data;
  $('title').textContent = data.title || '개념 매칭';
  $('total').textContent = '수업자료에서 뽑은 개념과 설명을 연결해 보세요. 답을 외우기보다 의미를 떠올리는 연습이에요.';
  $('phase').textContent = data.status === 'finished' ? '매칭 완료' : '개념 연결 중';
  $('progress').textContent = data.matched + ' / ' + data.total + '쌍 연결 · 시도 ' + data.attempts + '회';
  $('left-tiles').replaceChildren(...data.leftTiles.map(item => tile('left', item)));
  $('right-tiles').replaceChildren(...data.rightTiles.map(item => tile('right', item)));
  if (data.correct === true) $('notice').textContent = '잘 연결했어요. 다음 개념을 골라 보세요.';
  if (data.correct === false) $('notice').textContent = data.reshuffled
    ? '아직 짝이 아니에요. 위치를 한 번 섞었어요. 다시 찾아보세요.'
    : '아직 짝이 아니에요. 개념의 뜻을 다시 떠올려 보세요.';
  if (data.status !== 'finished') return;
  $('notice').textContent = data.answer;
  $('result').hidden = false; $('score').textContent = '정확도 ' + data.score + '% · 틀린 연결 ' + data.wrong + '회';
  $('review-list').replaceChildren(...(data.review || []).map(item => {
    const li = document.createElement('li'); li.append(document.createElement('strong'), document.createElement('span'));
    li.firstChild.textContent = item.concept; li.lastChild.textContent = item.explanation || '다시 확인해 보세요.'; return li;
  }));
}
(async () => { try { render(await api()); } catch (e) { $('title').textContent = '매칭판을 열지 못했어요'; error(e.message); } })();
