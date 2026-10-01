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
