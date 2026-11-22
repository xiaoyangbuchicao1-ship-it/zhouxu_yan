import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/paper-source-verify-20261121');
await mkdir(output, { recursive: true });
const credentials = { email: `paper-card-${randomUUID()}@example.test`, password: randomUUID(), display_name: '论文卡片验收' };
let token;
async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    signal: AbortSignal.timeout(120000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}
assert.equal((await api('/healthz')).response.status, 200);
const registered = await api('/auth/register', credentials);
assert.equal(registered.response.status, 201, JSON.stringify(registered.payload));
token = registered.payload.session.token;
const conversationResponse = await api('/workspace/conversations', { title: '论文资料栏验收', mode: 'work' });
assert.equal(conversationResponse.response.status, 201, JSON.stringify(conversationResponse.payload));
const cid = conversationResponse.payload.conversation.conversation_id;
const graph = {
  version: 'paper-source-card-1',
  nodes: [{ id: 'paper-search', kind: 'tool', label: '检索论文资料', config: { tool_name: 'information_card_search', arguments: { query: '货运代理 海上运输 责任', limit: 5 } } }],
};
const submitted = await api(`/workspace/conversations/${cid}/runs`, {
  conversation_id: cid,
  client_request_id: `paper-card-${randomUUID()}`,
  text: '查找论文资料',
  graph,
});
assert.equal(submitted.response.status, 202, JSON.stringify(submitted.payload));
const runId = submitted.payload.run.run_id;
async function snapshot() {
  const result = await api(`/workspace/conversations/${cid}/snapshot`);
  assert.equal(result.response.status, 200, JSON.stringify(result.payload));
  return result.payload.runs.find(run => run.run_id === runId);
}
let run;
for (let i = 0; i < 180; i += 1) {
  run = await snapshot();
  if (run?.status === 'succeeded') break;
  if (['failed', 'cancelled'].includes(run?.status)) throw new Error(`论文卡片图失败: ${JSON.stringify(run)}`);
  await new Promise(resolvePromise => setTimeout(resolvePromise, 1000));
}
assert.equal(run?.status, 'succeeded', `论文卡片图超时: ${run?.status}`);
assert((run.sources || []).some(source => source.kind === 'paper'), `没有论文来源: ${JSON.stringify(run)}`);
const paper = run.sources.find(source => source.kind === 'paper');
const sourceResponse = await api(`/workspace/sources/${encodeURIComponent(paper.source_id)}`);
assert.equal(sourceResponse.response.status, 200, JSON.stringify(sourceResponse.payload));
assert.equal(sourceResponse.payload.kind, 'paper');
assert(sourceResponse.payload.text?.trim(), '论文详情没有正文');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await page.locator('button[aria-label="资料与依据"]').click();
  await page.locator('.ws-research-panel').waitFor();
  const paperFilter = page.locator('.ws-source-filters button').filter({ hasText: '论文资料' });
  await paperFilter.first().waitFor(); assert.equal(await paperFilter.count(), 1, '资料栏有论文资料筛选');
  await paperFilter.click();
  const search = page.locator('input[aria-label="搜索本次资料"]');
  await search.fill('李志文');
  assert((await page.locator('.ws-research-results li button').count()) > 0, '可以按作者搜索论文资料');
  await search.fill('');
  const paperCard = page.locator('.ws-research-results li button').filter({ hasText: paper.title });
  await paperCard.first().waitFor();
  assert.equal(await paperCard.count(), 1, '资料栏显示论文卡片');
  await paperCard.first().click();
  await page.locator('[role="dialog"]').waitFor();
  await page.locator('.ws-source-text').waitFor({ timeout: 120000 });
  assert.equal(await page.locator('[role="dialog"] .ws-source-text').count(), 1, '点击论文卡片可以打开详情');
  const bodyText = await page.locator('body').innerText();
  assert(!bodyText.includes('information_card') && !bodyText.includes('source_path') && !bodyText.includes('card_id'), '页面不显示内部卡片字段');
  assert.equal(errors.length, 0, `浏览器脚本错误: ${errors.join('; ')}`);
  const report = { conversation_id: cid, run_id: runId, paper: { title: paper.title, authors: paper.authors, collection: paper.collection }, source_text_chars: sourceResponse.payload.text.length, checks: ['论文来源进入运行快照', '论文筛选和卡片显示', '可按作者搜索', '卡片详情可打开', '页面不展示内部字段'], page_errors: errors };
  await page.screenshot({ path: resolve(output, 'paper-sources.png') });
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}





