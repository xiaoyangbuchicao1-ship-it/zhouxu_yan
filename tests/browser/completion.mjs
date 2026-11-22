// Real browser + configured DeepSeek: completion must retain the answer DOM.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { chromium } from 'playwright';
import { formatDuration } from '../../frontend/src/lib/format-duration.ts';

for (const [ms, expected] of [[0, '0.0 秒'], [12449, '12.4 秒'], [59960, '1 分 00.0 秒'], [63700, '1 分 03.7 秒']]) {
  assert.equal(formatDuration(ms), expected);
}

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const credentials = { email: `completion-${randomUUID()}@example.test`, password: randomUUID(), display_name: '回答完成验收' };
let token;
async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST', signal: AbortSignal.timeout(20000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  assert(response.ok, `${path}: HTTP ${response.status}`);
  return response.json();
}
token = (await api('/auth/register', credentials)).session.token;
const cid = (await api('/workspace/conversations', { title: '完成时保持阅读画面' })).conversation.conversation_id;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(120000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);
  for (const round of [1, 2]) {
    await page.locator('textarea').fill(`第${round}问：不调用工具，用约1500字介绍合同审阅的基本步骤，包含标题、列表和一个三行表格。直接给出正文。`);
    await page.getByRole('button', { name: '发送问题', exact: true }).click();
    await page.waitForFunction(count => document.querySelectorAll('.ws-answer').length === count && document.querySelectorAll('.ws-answer')[count - 1].getAttribute('aria-busy') === 'true' && document.querySelectorAll('.ws-answer')[count - 1].querySelector('.ws-prose')?.textContent.length > 100, round);
    await page.locator('.ws-stage-details > summary').last().click();
    if (round === 2) {
      await page.locator('.ws-reader').hover();
      await page.mouse.wheel(0, -10000);
      await page.waitForTimeout(150);
    }
    await page.evaluate(() => {
      const answer = [...document.querySelectorAll('.ws-answer')].at(-1);
      const prose = answer.querySelector('.ws-prose');
      const reader = document.querySelector('.ws-reader');
      if (answer.getAttribute('aria-busy') !== 'true') throw Error('Probe must start before completion');
      window.completionProbe = { answer, prose, removed: 0, maxAnswers: document.querySelectorAll('.ws-answer').length, reader, scrollTop: reader.scrollTop };
      window.completionObserver = new MutationObserver(records => {
        for (const record of records) for (const node of record.removedNodes) {
          if (node === answer || node.contains?.(answer) || node === prose || node.contains?.(prose)) window.completionProbe.removed++;
        }
        window.completionProbe.maxAnswers = Math.max(window.completionProbe.maxAnswers, document.querySelectorAll('.ws-answer').length);
      });
      window.completionObserver.observe(document.querySelector('.ws-message-column'), { childList: true, subtree: true });
    });
    await page.waitForFunction(count => document.querySelectorAll('.ws-answer-tools').length === count, round);
    await page.waitForTimeout(1200);
    const result = await page.evaluate(() => {
      window.completionObserver.disconnect();
      const p = window.completionProbe;
      return { sameAnswer: p.answer === [...document.querySelectorAll('.ws-answer')].at(-1), sameProse: p.prose === [...document.querySelectorAll('.ws-answer')].at(-1).querySelector('.ws-prose'), removed: p.removed, maxAnswers: p.maxAnswers, scrollDelta: p.reader.scrollTop - p.scrollTop, detailsOpen: p.answer.querySelector('.ws-stage-details').open, times: [...document.querySelectorAll('.ws-progress time')].map(el => el.textContent) };
    });
    console.log(JSON.stringify({ round, ...result }));
    assert(result.sameAnswer && result.sameProse && result.removed === 0, '完成时不移除或重建回答正文');
    assert.equal(result.maxAnswers, round, '同一个回答不重复显示');
    assert(result.detailsOpen, '完成后保持已展开的阶段详情');
    if (round === 2) assert(Math.abs(result.scrollDelta) < 2, '上翻阅读时完成不能抢滚动');
    assert(result.times.length && result.times.every(t => /\d+\.\d 秒$/.test(t)), '总耗时和阶段耗时都保留一位小数');
  }
  await page.reload();
  await page.locator('.ws-answer-tools').last().waitFor();
  assert.equal(await page.locator('.ws-answer').count(), 2, '刷新后两个回答均保留');
  assert.equal(errors.length, 0, errors.join('\n'));
  console.log('PASS: 连续两次真实回答完成时正文不重建；小数耗时与刷新恢复通过');
} finally { await browser.close(); }
