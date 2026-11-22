// Node 3: real website + Chromium + one CC Switch-compatible relay model.
// The task is deliberately tiny: one write tool call and one downloadable file.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/node3-relay-work-20261121');
await mkdir(output, { recursive: true });
const credentials = {
  email: `node3-${randomUUID()}@example.test`,
  password: randomUUID(),
  display_name: 'Node 3 中转工作闭环',
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
const relay = catalog.groups.flatMap(group => group.models).find(model => model.model_id === 'gpt-6-astra');
assert(relay && relay.available, 'gpt-6-astra must be available');
assert.equal(relay.protocol, 'openai_responses');
assert.equal(relay.capabilities.tools, 'confirmed');
assert.equal(relay.capabilities.streaming, 'confirmed');

const conversation = (await api('/workspace/conversations', { title: 'Node 3 中转工作闭环', mode: 'work' })).payload.conversation;
const cid = conversation.conversation_id;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const report = { conversation_id: cid, model: relay.model_id, checks: [], run_id: null, artifacts: [] };
function check(value, label) {
  assert(value, label);
  report.checks.push(label);
  console.log(JSON.stringify({ passed: label }));
}
async function snapshot() {
  return (await api(`/workspace/conversations/${cid}/snapshot`)).payload.runs.at(-1);
}
async function untilFinished() {
  const end = Date.now() + 240000;
  let last;
  while (Date.now() < end) {
    last = await snapshot();
    if (last && last.status === 'succeeded') return last;
    if (last && ['failed', 'cancelled'].includes(last.status)) {
      throw new Error(`relay work failed: ${last.status} ${last.error?.code || ''} ${last.error?.message || ''}`);
    }
    await new Promise(done => setTimeout(done, 1000));
  }
  throw new Error(`relay work timed out: ${last?.status || 'missing'}`);
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await page.waitForSelector('.ws-model-trigger');
  await page.getByRole('button', { name: /模型：/ }).click();
  await page.getByRole('menuitem', { name: relay.model_id, exact: true }).click();
  await page.waitForTimeout(300);
  const triggerText = await page.locator('.ws-model-trigger').innerText();
  console.log(JSON.stringify({ model_trigger_after_select: triggerText }));
  check(triggerText.includes(relay.model_id), '工作页已选择通过 CC Switch 协议探测的模型');
  const prompt = '请用工具 write 创建 relay-cc-switch-proof.txt，内容只写：CC Switch 工具闭环成功。完成后交付这个文件。';
  await page.locator('textarea').fill(prompt);
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const run = await untilFinished();
  report.run_id = run.run_id;
  check(run.payload.mode === 'work', '运行确实进入工作模式');
  check(run.payload.model_snapshot?.provider === 'relay', '运行快照记录中转模型');
  check(run.payload.model_snapshot?.protocol === 'openai_responses', '运行快照记录 Responses 协议');
  const artifact = (run.artifacts || []).find(item => item.name === 'relay-cc-switch-proof.txt');
  check(Boolean(artifact), '服务端登记了真实文件产物');
  check(artifact?.name === 'relay-cc-switch-proof.txt' && artifact.size_bytes > 0, 'write 工具留下了可追溯的文件结果');
  report.artifacts = run.artifacts || [];
  const downloadResponse = await fetch(`${origin}/api/workspace/runs/${run.run_id}/artifacts/${artifact.artifact_id}?conversation_id=${cid}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(20000),
  });
  check(downloadResponse.status === 200, '真实网站下载接口返回文件');
  const bytes = Buffer.from(await downloadResponse.arrayBuffer());
  check(bytes.toString('utf8') === 'CC Switch 工具闭环成功。', '下载文件内容与工具写入内容一致');
  check(createHash('sha256').update(bytes).digest('hex') === artifact.sha256, '下载文件哈希与服务端记录一致');
  await page.reload();
  check((await page.locator('.ws-message-column').innerText()).includes('CC Switch 工具闭环成功'), '刷新后仍显示工作结果');
  report.page_errors = errors;
  check(errors.length === 0, '浏览器没有页面脚本错误');
  await page.screenshot({ path: resolve(output, 'node3-relay-work.png') });
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
