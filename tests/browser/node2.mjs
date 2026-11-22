// Node 2: real HTTP + Chromium, one official DeepSeek "你好" run.
// Relay models are probed separately with one "你好" each; this script keeps
// the browser regression cheap while checking the public selector and snapshot.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/step3-node2-20261121');
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
assert.deepEqual(catalog.groups.find(group => group.family === 'deepseek').models.map(model => model.profile_id), ['deepseek-flash', 'deepseek-v4-pro']);
assert.deepEqual(deepseek.thinking_modes, ['none', 'low', 'high', 'max']);
assert.equal(deepseek.capabilities.tools, 'confirmed');
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
  check(!(await page.locator('.ws-model-option').allTextContents()).some(text => text.includes('暂不可用')), '选择器隐藏不可用模型和说明文字');
  check((await page.getByRole('menuitem', { name: 'DeepSeek Pro' }).count()) === 1, '选择器显示官方 DeepSeek Pro');
  await page.getByRole('menuitem', { name: 'DeepSeek Pro' }).click();
  const modelSwitchEvent = page.locator('.ws-model-switch-event').filter({ hasText: '模型已从 DeepSeek Flash 切换至 DeepSeek Pro' });
  await modelSwitchEvent.waitFor();
  check(await modelSwitchEvent.count() === 1, '模型切换记录显示在对话中间');
  await page.waitForTimeout(3500);
  check(await modelSwitchEvent.count() === 1, '模型切换记录不会自动消失');
  check(await page.locator('.ws-model-snapshot').count() === 0, '回答区域不显示模型说明行');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /思考档位：关闭/ }).click();
  await page.getByRole('menuitemradio', { name: '高', exact: true }).click();
  await page.getByRole('button', { name: '思考档位：高', exact: true }).waitFor();
  check(await page.getByRole('button', { name: '思考档位：高' }).count() === 1, '思考档位选择会立即更新');
  await page.locator('textarea').fill('你好');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const run = await untilDone();
  report.run_id = run.run_id;
  check(run.payload.model_snapshot?.provider === 'deepseek', '运行快照记录真实 DeepSeek 供应商');
  check(run.payload.model_snapshot?.protocol === 'deepseek_chat_completions', '运行快照记录真实请求协议');
  check(run.payload.model_snapshot?.thinking_mode === 'high', '运行快照记录思考档位');
  check(Boolean(run.output && run.output.trim()), 'DeepSeek 你好回答真实返回');
  await page.getByRole('button', { name: '复制回答', exact: true }).waitFor();
  check(await page.getByRole('button', { name: '复制回答', exact: true }).count() === 1, '有回答时显示复制和保存操作');
  const visibleText = await page.locator('body').innerText();
  check(!visibleText.includes('中转站') && !visibleText.includes('不代表') && !visibleText.includes('需要文件操作时才准备执行环境'), '页面不显示防御性说明和内部连接称呼');
  const rejected = await api(`/workspace/conversations/${cid}/runs`, {
    conversation_id: cid,
    client_request_id: `node2-unconfigured-relay-${randomUUID()}`,
    text: '你好',
    model_profile_id: 'relay:gpt-4o',
  });
  check(rejected.response.status === 409 && rejected.payload.detail === 'model_profile_unavailable', '未通过探测的中转模型被后端拒绝');
  await page.reload();
  check((await page.locator('.ws-message-column').innerText()).includes(run.output.trim()), '刷新后仍显示同一次模型回答');
  check(await page.locator('.ws-model-snapshot').count() === 0, '刷新后仍不显示模型说明行');
  report.page_errors = errors;
  check(errors.length === 0, '浏览器没有页面脚本错误');
  await page.screenshot({ path: resolve(output, 'node2-model-selector.png') });
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
