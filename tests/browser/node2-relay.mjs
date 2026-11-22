// Node 2: real HTTP + Chromium, one official DeepSeek "你好" run.
// Relay models are probed separately with one "你好" each; this script keeps
// the browser regression cheap while checking the public selector and snapshot.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/step3-node2-relay-20261121');
await mkdir(output, { recursive: true });
const credentials = {
  email: `node2-${randomUUID()}@example.test`,
  password: randomUUID(),
  display_name: 'Node 2 验收',
};
let token;
async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    signal: AbortSignal.timeout(20000),
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}
await api('/healthz').then(({ response }) => assert.equal(response.status, 200));
token = (await api('/auth/register', credentials)).payload.session.token;
const catalog = (await api('/workspace/models')).payload;
assert.equal(catalog.default_profile_id, 'deepseek-flash');
assert.deepEqual(catalog.groups.map(group => group.family), ['deepseek', 'gpt', 'claude', 'gemini', 'glm']);
const deepseek = catalog.groups.find(group => group.family === 'deepseek').models[0];
assert.equal(deepseek.available, true);
assert.equal(deepseek.capabilities.tools, 'confirmed');
const relayGpt = catalog.groups.find(group => group.family === 'gpt')?.models.find(model => model.provider === 'relay' && model.available);
assert(relayGpt, '至少有一个 GPT 中转模型通过了真实你好连通性检查');
const catalogText = JSON.stringify(catalog).toLowerCase();
assert(!catalogText.includes('api_key') && !catalogText.includes('secret') && !catalogText.includes('sk-'));
const conversation = (await api('/workspace/conversations', { title: 'Node 2 模型选择' })).payload.conversation;
const cid = conversation.conversation_id;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const report = { conversation_id: cid, checks: [], run_id: null, relay_models: catalog.groups.flatMap(group => group.models.filter(model => model.provider === 'relay' && model.available).map(model => model.model_id)) };
function check(value, label) {
  assert(value, label);
  report.checks.push(label);
  console.log(JSON.stringify({ passed: label }));
}
async function snapshot() {
  return (await api(`/workspace/conversations/${cid}/snapshot`)).payload.runs.at(-1);
}
async function untilDone() {
  const end = Date.now() + 180000;
  while (Date.now() < end) {
    const run = await snapshot();
    if (run && run.status === 'succeeded') return run;
    if (run && ['failed', 'cancelled'].includes(run.status)) throw new Error(`Node 2 hello failed: ${run.status}`);
    await new Promise(resolvePromise => setTimeout(resolvePromise, 1000));
  }
  throw new Error('Node 2 hello timed out');
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await page.waitForSelector('.ws-model-trigger');
  check(await page.getByRole('button', { name: /模型：DeepSeek Flash/ }).count() === 1, '聊天页显示当前模型选择器');
  await page.getByRole('button', { name: /模型：DeepSeek Flash/ }).click();
  check((await page.locator('.ws-model-group-label').allTextContents()).includes('GPT'), '选择器按模型家族分组');
  check(!(await page.locator('.ws-model-option').allTextContents()).some(text => text.includes('暂不可用')), '选择器只显示可用模型');
  await page.getByRole('menuitem', { name: relayGpt.model_id, exact: true }).click();
  await page.getByRole('button', { name: `模型：${relayGpt.model_id}`, exact: true }).waitFor();
  check(true, '已选择通过探测的 GPT 中转模型');
  const modelSwitchEvent = page.locator('.ws-model-switch-event').filter({ hasText: `模型已从 DeepSeek Flash 切换至 ${relayGpt.model_id}` });
  await modelSwitchEvent.waitFor();
  check(await modelSwitchEvent.count() === 1, '模型切换记录显示在对话中间');
  await page.waitForTimeout(3500);
  check(await modelSwitchEvent.count() === 1, '模型切换记录不会自动消失');
  const thinkingTrigger = page.getByRole('button', { name: /思考档位：/ });
  check(await thinkingTrigger.count() === (relayGpt.thinking_modes.length > 1 ? 1 : 0), '思考档位入口与模型能力一致');
  await page.locator('textarea').fill('你好');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const run = await untilDone();
  report.run_id = run.run_id;
  check(run.payload.model_snapshot?.provider === 'relay', '运行快照记录真实中转站供应商');
  check(run.payload.model_snapshot?.protocol === relayGpt.protocol, '运行快照记录该模型实际协议');
  check(relayGpt.thinking_modes.includes(run.payload.model_snapshot?.thinking_mode), '运行快照记录该模型支持的思考档位');
  check(Boolean(run.output && run.output.trim()), '中转站模型你好回答真实返回');
  const rejected = await api(`/workspace/conversations/${cid}/runs`, {
    conversation_id: cid,
    client_request_id: `node2-unconfigured-relay-${randomUUID()}`,
    text: '你好',
    model_profile_id: 'relay:gpt-4o',
  });
  check(rejected.response.status === 409 && rejected.payload.detail === 'model_profile_unavailable', '未通过探测的中转模型被后端拒绝');
  await page.reload();
  check((await page.locator('.ws-message-column').innerText()).includes(run.output.trim()), '刷新后仍显示同一次模型回答');
  report.page_errors = errors;
  check(errors.length === 0, '浏览器没有页面脚本错误');
  await page.screenshot({ path: resolve(output, 'node2-model-selector.png') });
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
