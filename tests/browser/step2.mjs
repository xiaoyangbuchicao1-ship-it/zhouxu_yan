// Actual HTTP, Chromium and configured DeepSeek. No model or source fixtures.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/step2-20261120');
await mkdir(output, { recursive: true });
const credentials = { email: `step2-${randomUUID()}@example.test`, password: randomUUID(), display_name: '第二步体验验收' };
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
await api('/healthz');
token = (await api('/auth/register', credentials)).session.token;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const cid = (await api('/workspace/conversations', { title: '第二步：按问题整理资料' })).conversation.conversation_id;
// Only this script's disposable account; never store user/platform credentials.
await writeFile(resolve(output, 'test-session.json'), JSON.stringify({ credentials, token, cid }));
const report = { conversation_id: cid, checks: [], runs: [] };
async function snapshot() { return (await api(`/workspace/conversations/${cid}/snapshot`)).runs.at(-1); }
async function until(predicate, label, timeout = 240000) {
  const end = Date.now() + timeout;
  let announce = 0;
  while (Date.now() < end) {
    const run = await snapshot();
    if (run && predicate(run)) return run;
    if (run && ['failed', 'cancelled', 'succeeded'].includes(run.status)) throw Error(`${label}: unexpected ${run.status}; ${run.run_id}`);
    if (Date.now() > announce) { console.log(JSON.stringify({ waiting: label, status: run?.status, sources: run?.sources.length })); announce = Date.now() + 30000; }
    await new Promise(done => setTimeout(done, 1000));
  }
  throw Error(`${label}: timed out`);
}
async function send(text) {
  await page.locator('textarea').fill(text);
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
}
async function check(condition, label) {
  assert(condition, label);
  report.checks.push(label);
  console.log(JSON.stringify({ passed: label }));
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await send('请实际检索海上货物运输迟延交付、承运人免责与赔偿限制的相关法规和案例，打开原文核对。请分开检索上述三个问题，完成检索后用约1800字说明，包含标题、要点列表和四行比较表格。不要创建文件。');
  await until(r => r.status === 'running' && r.sources.length > 2, 'sources before pause');
  await page.getByRole('button', { name: '暂停回答', exact: true }).click();
  const paused = await until(r => r.status === 'paused', 'pause');
  report.runs.push(paused.run_id);
  await page.getByRole('button', { name: '继续处理', exact: true }).last().waitFor();
  await check(await page.locator('.ws-source-card').count() === 0, '暂停时正文没有整排参考卡片');
  const clockBefore = await page.locator('.ws-progress').last().innerText();
  await new Promise(done => setTimeout(done, 1500));
  await check(await page.locator('.ws-progress').last().innerText() === clockBefore, '暂停后计时停止');
  await page.reload();
  await page.getByRole('button', { name: '继续处理', exact: true }).last().waitFor();
  const afterReload = await snapshot();
  await check(afterReload.run_id === paused.run_id && afterReload.active_elapsed_ms === paused.active_elapsed_ms, '刷新保留同一任务和耗时');
  await page.locator('.ws-source-summary').last().click();
  await page.getByLabel('按提问查看').waitFor();
  await check(await page.getByLabel('按提问查看').inputValue() === paused.run_id, '资料入口定位到对应问题');
  await page.getByRole('button', { name: '关闭资料面板', exact: true }).click();
  await page.evaluate(() => {
    window.renderSample = { longTasks: [], mutations: 0 };
    new PerformanceObserver(list => window.renderSample.longTasks.push(...list.getEntries().map(entry => entry.duration))).observe({ type: 'longtask', buffered: false });
    new MutationObserver(items => window.renderSample.mutations += items.length).observe(document.querySelector('.ws-message-column'), { childList: true, subtree: true, characterData: true });
  });
  await page.getByRole('button', { name: '继续处理', exact: true }).last().click();
  const complete = await until(r => r.status === 'succeeded', 'finish research');
  await check(complete.run_id === paused.run_id, '继续处理沿原任务完成');
  report.performance = await page.evaluate(() => window.renderSample);
  report.answer_chars = complete.output.length;
  report.stages = complete.stages;
  await check(complete.stages.length > 0 && complete.stages.every(s => s.elapsed_ms !== null), '新任务的阶段耗时有真实记录');
  await page.reload();
  await page.locator('.ws-source-summary').last().click();
  const unique = new Set(complete.sources.map(s => s.source_id));
  await check(await page.locator('.ws-research-list li').count() === Math.min(8, unique.size), '资料去重且首批最多八条');
  if (unique.size > 8) {
    await page.getByRole('button', { name: /查看更多 · 还有/ }).click();
    await check(await page.locator('.ws-research-list li').count() === Math.min(16, unique.size), '更多资料可以分批展开');
  }
  await page.getByRole('button', { name: /^法规 \d/ }).click();
  const law = complete.sources.find(s => s.kind === 'law');
  assert(law, 'real law source required');
  await page.getByRole('searchbox', { name: '搜索本次资料' }).fill(law.title);
  await page.locator('.ws-research-list button').first().click();
  await page.locator('.ws-source-text').waitFor();
  await check((await page.locator('.ws-source-text').innerText()).length > 30, '筛选搜索后可以读取真实原文');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('searchbox', { name: '搜索本次资料' }).fill('');
  await page.getByRole('button', { name: /^全部 \d/ }).click();
  await page.screenshot({ path: resolve(output, 'desktop.png') });
  await page.getByRole('button', { name: '关闭资料面板', exact: true }).click();
  await send('第二个独立问题：请只检索船舶碰撞责任相关法规，至少实际检索一次，然后用200字说明。不要继续上一问。');
  const second = await until(r => r.run_id !== complete.run_id && r.status === 'succeeded', 'second question');
  report.runs.push(second.run_id);
  assert(second.sources.length > 0);
  await page.reload();
  await page.locator('.ws-source-summary').last().click();
  await check(await page.getByLabel('按提问查看').inputValue() === second.run_id, '新问题默认展示自己的资料');
  await page.getByLabel('按提问查看').selectOption(complete.run_id);
  await check(await page.locator('.ws-research-list li').count() === Math.min(8, unique.size), '切换旧问题仍可查看原资料');
  await page.getByRole('button', { name: '关闭资料面板', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.ws-source-summary').first().click();
  await page.getByRole('dialog', { name: '资料与依据', exact: true }).waitFor();
  await check(await page.getByLabel('按提问查看').inputValue() === complete.run_id, '手机抽屉也定位到点击的问题');
  await page.screenshot({ path: resolve(output, 'mobile.png') });
  await page.keyboard.press('Escape');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('.ws-source-summary').last().click();
  await page.getByRole('dialog', { name: '资料与依据', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await check(errors.length === 0, '页面无脚本错误，减少动效时仍可操作');
} finally {
  await writeFile(resolve(output, 'results.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
