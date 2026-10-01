// Real browser + local Python server. No real AI calls; offline mode is explicitly tested.
const { chromium, devices } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs/promises');

(async () => {
  const server = spawn(process.env.PYTHON || 'python3', [path.join(__dirname, '../scripts/handover_web.py'), '--port', '0'], {
    env: {...process.env, TEAM_HANDOVER_API_URL: '', TEAM_HANDOVER_MODEL: '', TEAM_HANDOVER_API_KEY: ''}, stdio: ['ignore', 'pipe', 'pipe']
  });
  let browser;
  try {
    const base = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Server startup timed out')), 10000);
      server.stdout.on('data', chunk => { const match = chunk.toString().match(/http:\/\/127\.0\.0\.1:\d+/); if (match) { clearTimeout(timeout); resolve(match[0]); } });
      server.once('exit', code => { clearTimeout(timeout); reject(new Error('Server exited: ' + code)); });
    });
    browser = await chromium.launch({headless: true});
    const mobileDevice = process.env.HANDOVER_MOBILE_DEVICE;
    if (mobileDevice) assert.ok(devices[mobileDevice], 'Unknown mobile device: ' + mobileDevice);
    const context = await browser.newContext({
      ...(mobileDevice ? devices[mobileDevice] : {viewport: {width: 1440, height: 1100}}),
      permissions: ['clipboard-read', 'clipboard-write']
    });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const visible = async selector => { await page.locator(selector).waitFor({state:'visible'}); };
    await page.goto(base + '/?request=' + encodeURIComponent('팀플 정리해줘'));
    await page.waitForFunction(() => document.querySelector('#chat-answer').textContent.includes('붙여넣어주세요'));
    await page.click('#analyze'); await visible('#input-error');
    assert.match(await page.locator('#input-error').innerText(), /붙여넣어주세요/);
    await page.click('#sample');
    // Regression: several dated records in one paragraph must keep their owners/statuses.
    const lines = (await page.inputValue('#records')).split('\n');
    await page.fill('#records', lines[0] + '\n' + lines.slice(1).join(' '));
    const originalInput = await page.inputValue('#records');
    await page.selectOption('#analysis-mode', 'ai');
    await page.click('#analyze');
    await page.waitForFunction(() => document.querySelector('#input-error').textContent.includes('AI 설정 필요'));
    assert.equal(await page.inputValue('#records'), originalInput);
    assert.equal(await page.locator('#analyze').isEnabled(), true);
    await page.selectOption('#analysis-mode', 'local');
    // Delayed response makes the progress state observable.
    await page.route('**/api/analyze', async route => { await new Promise(r => setTimeout(r, 300)); await route.continue(); });
    await page.click('#analyze'); await visible('#analysis-progress');
    assert.equal(await page.locator('#analyze').isDisabled(), true);
    await visible('#results'); await page.waitForFunction(() => !document.querySelector('#analyze').disabled);
    assert.match(await page.locator('#result-source').innerText(), /AI 아님/);
    assert.match(await page.locator('#results-title').innerText(), /학교생활 AI/);
    assert.equal(await page.locator('.task-card').count(), 4);
    assert.equal(await page.locator('#completed-panel').evaluate(el => el.open), false);
    assert.equal(await page.locator('#summary').isVisible(), false);
    const apiTask = page.locator('.task-card').filter({has: page.locator('h4', {hasText:'로그인 API'})});
    await apiTask.locator('.evidence summary').click();
    const evidence = await apiTask.locator('.evidence-content').innerText();
    assert.match(evidence, /예시 회의록/); assert.match(evidence, /지연은 API 작업 중/);
    await apiTask.locator('.task-owner').fill('수빈');
    await apiTask.locator('.task-deadline').fill('10월 5일');
    await apiTask.locator('.task-status').selectOption('완료');
    await page.locator('#completed-panel > summary').click();
    assert.match(await apiTask.locator('.edit-description').innerText(), /지연 → 수빈/);
    assert.equal(await apiTask.locator('.evidence-content').innerText(), evidence);
    await page.selectOption('#assignee', '수빈'); await page.click('#generate'); await visible('#draft-panel');
    await page.waitForFunction(() => !document.querySelector('#generate').disabled);
    let draft = await page.inputValue('#draft');
    assert.match(draft, /담당 수빈/); assert.match(draft, /기한 10월 5일/);
    assert.match(draft, /담당자, 기한 확인 필요/); assert.match(draft, /사용자 수정/);
    assert.equal(draft.includes('DB 테이블 생성 |'), false);
    assert.equal(draft.split('이어서 할 일')[1].split('관련 자료')[0].includes('로그인 API'), false);
    await page.fill('#draft', draft + '\n직접 편집한 내용');
    page.once('dialog', dialog => dialog.dismiss()); await page.click('#generate');
    assert.match(await page.inputValue('#draft'), /직접 편집한 내용/);
    // A second edit during an accepted regeneration must also survive.
    await page.route('**/api/handover', async route => { await new Promise(r => setTimeout(r, 400)); await route.continue(); });
    page.once('dialog', dialog => dialog.accept()); await page.click('#generate');
    await page.fill('#draft', draft + '\n생성 중 새로 편집'); await visible('#replacement');
    assert.match(await page.inputValue('#draft'), /생성 중 새로 편집/);
    await page.click('#keep-draft'); await page.click('#copy');
    await page.waitForFunction(() => document.querySelector('#live-status').textContent.includes('복사했어요'));
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), await page.inputValue('#draft'));
    await apiTask.locator('.task-deadline').fill('10월 6일'); await visible('#draft-stale');
    const screenshots = process.env.HANDOVER_SCREENSHOTS || path.join(require('node:os').tmpdir(), 'handover-ui'); await fs.mkdir(screenshots, {recursive:true});
    await page.locator('#results').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(screenshots,mobileDevice ? 'mobile-flow.png' : 'desktop.png'),fullPage:true});
    if (!mobileDevice) await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(screenshots,'mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile must not overflow');
    assert.equal(await page.locator('.task-controls').first().evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 1);
    // Explicit mock response checks that AI suggestions are separate in the screen.
    const isolated = await context.newPage();
    await isolated.goto(base);
    await isolated.route('**/api/analyze', async route => {
      const response = await route.fetch(); const body = await response.json();
      body.data.suggestions = [{text:'API 완료 후 연결 테스트 진행 (화면 검증용 모의 제안)', basedOn:[0], kind:'AI 제안'}];
      await route.fulfill({response, json:body});
    });
    await isolated.fill('#records', '지연은 로그인 API 작업 중.');
    await isolated.click('#analyze'); await isolated.locator('#suggestion-panel').waitFor({state:'visible'});
    assert.match(await isolated.locator('#suggestions').innerText(), /모의 제안/);
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('PASS: missing input, conversation, progress, failure/retry, edits/evidence, scoped draft, overwrite protection/race, clipboard, mobile, separate mock suggestion.');
    console.log('Screenshots: ' + screenshots);
    await isolated.close(); await context.close();
  } finally { if (browser) await browser.close(); server.kill('SIGTERM'); }
})().catch(error => { console.error(error); process.exitCode=1; });
