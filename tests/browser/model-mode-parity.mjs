// Real HTTP + Chromium. Only the final DeepSeek greeting calls a model.
// The explicit tool graph checks binding and execution without calling a provider.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/model-mode-parity');
await mkdir(output, { recursive: true });
const credentials = {
  email: `model-parity-${randomUUID()}@example.test`,
  password: randomUUID(),
  display_name: '模型选择验收',
};
let token;
async function api(path, body) {
  const response = await fetch(`${origin}/api${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    signal: AbortSignal.timeout(30000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return { status: response.status, payload: await response.json() };
}
async function snapshot(cid) {
  const result = await api(`/workspace/conversations/${cid}/snapshot`);
  assert.equal(result.status, 200);
  return result.payload.runs;
}
async function finished(cid, runId, status = 'succeeded') {
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    const run = (await snapshot(cid)).find(item => item.run_id === runId);
    if (run?.status === status) return run;
    assert(!['failed', 'cancelled', 'waiting_user'].includes(run?.status), `任务未成功：${run?.status} ${run?.error?.message}`);
    await new Promise(done => setTimeout(done, 500));
  }
  throw new Error(`任务超时：${runId}`);
}

assert.equal((await api('/healthz')).status, 200);
const registered = await api('/auth/register', credentials);
assert.equal(registered.status, 201);
token = registered.payload.session.token;
assert.equal((await api('/auth/login', { ...credentials, password: 'incorrect-password' })).status, 401);
const catalog = (await api('/workspace/models')).payload;
const models = catalog.groups.flatMap(group => group.models);
const available = models.filter(model => model.available);
const unverified = available.find(model => model.provider === 'relay' && model.capabilities.tools !== 'confirmed');
assert(unverified, '需要一个聊天可用但没有工具验证记录的现存模型来捕获旧门槛');
const chat = (await api('/workspace/conversations', { title: '聊天模型清单', mode: 'chat' })).payload.conversation;
const work = (await api('/workspace/conversations', { title: '工作模型清单', mode: 'work' })).payload.conversation;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
page.setDefaultTimeout(30000);
const report = { checks: [], page_errors: [], chat_id: chat.conversation_id, work_id: work.conversation_id, available_models: available.length };
page.on('pageerror', error => report.page_errors.push(error.message));
function check(value, label) {
  assert(value, label);
  report.checks.push(label);
  console.log(JSON.stringify({ passed: label }));
}
async function menu(cid) {
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await page.locator('.ws-model-trigger').click();
  await page.getByRole('menu').waitFor();
  return (await page.locator('.ws-model-option').allTextContents()).map(label => label.trim()).sort();
}
try {
  await page.goto(`${origin}/login`);
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  const expected = available.map(model => model.label).sort();
  assert.deepEqual(await menu(chat.conversation_id), expected);
  await page.keyboard.press('Escape');
  assert.deepEqual(await menu(work.conversation_id), expected);
  check(true, '聊天和工作显示完全相同的可用模型，不显示不可用型号');
  await page.getByRole('menuitem', { name: unverified.label, exact: true }).click();
  await page.reload();
  await page.getByRole('button', { name: `模型：${unverified.label}`, exact: true }).waitFor();
  check(true, '未验证工具的模型可以在工作模式选择，刷新后不会被换掉');

  const proof = `模型与执行分离-${randomUUID()}`;
  const submitted = await api(`/workspace/conversations/${work.conversation_id}/runs`, {
    conversation_id: work.conversation_id,
    client_request_id: randomUUID(),
    text: '模型绑定与文件工具检查',
    model_profile_id: unverified.profile_id,
    thinking_mode: unverified.default_thinking_mode,
    graph: { nodes: [{ id: 'write-proof', kind: 'tool', label: '保存检查文件', config: {
      tool_name: 'write', arguments: { path: 'model-mode-proof.txt', content: proof },
    } }] },
  });
  assert.equal(submitted.status, 202, JSON.stringify(submitted.payload));
  const run = await finished(work.conversation_id, submitted.payload.run.run_id);
  assert.equal(run.payload.mode, 'work');
  assert.equal(run.payload.model_snapshot.profile_id, unverified.profile_id);
  assert.equal(run.payload.model_snapshot.protocol, unverified.protocol);
  assert.deepEqual(run.payload.model_snapshot.capabilities, unverified.capabilities);
  const artifact = run.artifacts.find(item => item.name === 'model-mode-proof.txt');
  assert(artifact, '工具必须留下真实文件');
  const download = await fetch(`${origin}/api/workspace/runs/${run.run_id}/artifacts/${artifact.artifact_id}?conversation_id=${work.conversation_id}`, {
    headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(30000),
  });
  assert.equal(download.status, 200);
  assert.equal(await download.text(), proof);
  report.binding_run_id = run.run_id;
  report.binding_model = unverified.model_id;
  check(true, '后台接受同一模型并执行真实文件工具，原始能力记录保持不变（未调用该模型）');

  const rejected = await api(`/workspace/conversations/${chat.conversation_id}/runs`, {
    conversation_id: chat.conversation_id, client_request_id: randomUUID(), text: '创建文件',
    model_profile_id: unverified.profile_id, thinking_mode: unverified.default_thinking_mode,
    graph: { nodes: [{ id: 'write-blocked', kind: 'tool', label: '创建文件', config: {
      tool_name: 'write', arguments: { path: 'blocked.txt', content: '禁止写入' },
    } }] },
  });
  // Registered tool permissions are resolved by the executor, after acceptance.
  assert.equal(rejected.status, 202);
  const blockedRun = await finished(chat.conversation_id, rejected.payload.run.run_id, 'failed');
  const blockedDetail = await api(`/harness/runs/${blockedRun.run_id}?session_id=${chat.conversation_id}`);
  assert.equal(blockedDetail.status, 200);
  assert.equal(blockedDetail.payload.error.code, 'work_mode_required');
  assert.deepEqual(blockedRun.artifacts, []);
  report.blocked_run_id = blockedRun.run_id;
  assert.equal((await snapshot(chat.conversation_id)).length, 1);
  check(true, '开放模型不会开放聊天写入权限');

  await page.goto(`${origin}/workspace/chat/${work.conversation_id}`);
  await page.locator('.ws-model-trigger').click();
  await page.getByRole('menuitem', { name: 'DeepSeek Flash', exact: true }).click();
  const thinking = page.getByRole('button', { name: /^思考档位：/ });
  if (await thinking.getAttribute('aria-label') !== '思考档位：关闭') {
    await thinking.click();
    await page.getByRole('menuitemradio', { name: '关闭', exact: true }).click();
  }
  await page.locator('textarea').fill('你好');
  const requestPromise = page.waitForResponse(response => response.url().endsWith(`/conversations/${work.conversation_id}/runs`) && response.request().method() === 'POST');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const response = await requestPromise;
  assert.equal(response.status(), 202);
  const hello = await finished(work.conversation_id, (await response.json()).run.run_id);
  assert.equal(hello.payload.model_snapshot.provider, 'deepseek');
  assert.equal(hello.payload.model_snapshot.profile_id, 'deepseek-flash');
  assert(hello.output?.trim());
  report.hello_run_id = hello.run_id;
  await page.getByRole('button', { name: '复制回答', exact: true }).last().waitFor();
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('.ws-answer').length === 2);
  const answer = page.locator('.ws-answer').last();
  await answer.getByRole('button', { name: '复制回答', exact: true }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  assert.equal(copied.replace(/\r\n/g, '\n').trim(), hello.output.trim());
  assert((await answer.locator('[data-testid="stream-answer"]').innerText()).includes('你好'));
  check(true, '官方 DeepSeek 在工作页真实回答“你好”，刷新后回答保留');
  assert.deepEqual(report.page_errors, []);
  await page.screenshot({ path: resolve(output, 'work-models.png') });
} finally {
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
