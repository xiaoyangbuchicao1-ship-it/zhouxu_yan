// Node 1: real HTTP + Chromium + the configured official DeepSeek.
// The only model prompt is “你好”; permission checks use a rejected graph
// submission so they do not spend another model call.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/step3-node1-20261121');
await mkdir(output, { recursive: true });
const credentials = {
  email: `node1-${randomUUID()}@example.test`,
  password: randomUUID(),
  display_name: 'Node 1 验收',
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
const conversation = (await api('/workspace/conversations', { title: 'Node 1 动态任务图' })).payload.conversation;
const cid = conversation.conversation_id;

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const report = { conversation_id: cid, checks: [], run_id: null };
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
    if (run && ['failed', 'cancelled'].includes(run.status)) {
      throw new Error(`Node 1 hello failed: ${run.status} ${run.error?.message || ''}`);
    }
    await new Promise(resolvePromise => setTimeout(resolvePromise, 1000));
  }
  throw new Error('Node 1 hello timed out');
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);

  // This is the only real model call in Node 1.
  await page.locator('textarea').fill('你好');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const run = await untilDone();
  report.run_id = run.run_id;
  check(run.graph && run.graph.nodes.length === 1, '你好只生成一个最小模型节点');
  check(run.graph.nodes[0].kind === 'model', '最小节点类型是模型');
  check(run.node_states.length === 1 && run.node_states[0].status === 'succeeded', '模型节点真实完成并有服务端回执');
  check(Boolean(run.output && run.output.trim()), '回答正文真实返回');
  check(run.pending_request_ids.length === 0 && run.sources.length === 0 && run.artifacts.length === 0, '普通聊天没有资料卡、文件或升级确认');

  await page.reload();
  // Wait for the restored answer, then compare visible text without Markdown
  // emphasis. The persisted Markdown is checked separately through HTTP.
  await page.waitForFunction(output => {
    const normalize = text => text.replace(/[*_`#]/g, '').replace(/\s+/g, '');
    const answer = document.querySelector('.ws-answer .ws-prose');
    return answer && normalize(answer.textContent).includes(normalize(output));
  }, run.output);
  const history = (await api(`/workspace/conversations/${cid}`)).payload;
  const saved = history.messages.filter(message => message.role === 'assistant' && message.run_id === run.run_id);
  check(saved.length === 1 && saved[0].content === run.output, '刷新后仍显示同一回答，历史正文完整且不重复');

  // The backend must reject a privileged graph in chat mode before a worker
  // or model starts. This request carries no secret and spends no model call.
  const rejected = await api(`/workspace/conversations/${cid}/runs`, {
    conversation_id: cid,
    client_request_id: `node1-privileged-${randomUUID()}`,
    text: '读取 Skill',
    graph: { nodes: [{ id: 'skill', kind: 'skill', label: '读取 Skill' }] },
  });
  check(rejected.response.status === 409 && rejected.payload.detail === 'work_mode_required', '聊天模式后端拒绝越权 Skill 图');

  report.page_errors = errors;
  check(errors.length === 0, '浏览器没有页面脚本错误');
  await page.screenshot({ path: resolve(output, 'node1-hello.png') });
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
