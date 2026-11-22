// Real website checks. Relay greetings stay tiny; --work-smoke observes each
// approved paper task for at most 30 seconds before requesting a real pause.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.env.ZHOUXU_TEST_OUTPUT || '.ai-tmp/c3-protocols');
await mkdir(output, { recursive: true });
const workSmoke = process.argv.includes('--work-smoke');
const report = { checks: [], greetings: [], work: [], page_errors: [] };
const credentials = { email: `c3-${randomUUID()}@example.test`, password: randomUUID(), display_name: 'C3 协议验收' };
let token;
async function api(path, body) {
  const response = await fetch(`${origin}/api${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    signal: AbortSignal.timeout(20000),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return { status: response.status, payload: await response.json() };
}
function check(condition, label) {
  assert(condition, label);
  report.checks.push(label);
  console.log(JSON.stringify({ passed: label }));
}
async function snapshot(cid) {
  const result = await api(`/workspace/conversations/${cid}/snapshot`);
  assert.equal(result.status, 200);
  return result.payload;
}
async function until(cid, predicate, timeout = 150000) {
  const end = Date.now() + timeout;
  let run;
  while (Date.now() < end) {
    run = (await snapshot(cid)).runs.at(-1);
    if (run && predicate(run)) return run;
    if (run && ['failed', 'cancelled', 'waiting_user'].includes(run.status)) {
      throw new Error(`Run ${run.run_id}: ${run.status}: ${run.error?.message || ''}`);
    }
    await new Promise(done => setTimeout(done, 500));
  }
  throw new Error(`Run ${run?.run_id}: timed out in ${run?.status}`);
}

assert.equal((await api('/healthz')).status, 200);
const registration = await api('/auth/register', credentials);
token = registration.payload.session.token;
const catalog = (await api('/workspace/models')).payload.groups.flatMap(group => group.models);
const requestedModels = process.env.C3_MODELS
  ? process.env.C3_MODELS.split(',').filter(Boolean)
  : ['deepseek-flash', 'gpt-5.6', 'claude-fable-5', 'gemini-3.8-flash'];
const models = requestedModels.map(id => catalog.find(p => p.model_id === id));
assert(models.every(Boolean));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(20000);
page.on('pageerror', error => report.page_errors.push(error.message));
try {
  await page.goto(`${origin}/login`);
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));

  for (const model of models) {
    const conversation = (await api('/workspace/conversations', { title: `C3 ${model.model_id}`, mode: 'work' })).payload.conversation;
    const cid = conversation.conversation_id;
    await page.goto(`${origin}/workspace/chat/${cid}`);
    await page.waitForSelector('.ws-model-trigger');
    if (model.provider === 'relay') {
      check(model.capabilities.tools === 'confirmed', `${model.family} 的工作能力来自真实探测`);
      await page.locator('.ws-model-trigger').click();
      await page.getByRole('menuitem', { name: model.label, exact: true }).click();
      if (model.thinking_modes.length > 1) {
        await page.getByRole('button', { name: /思考档位：/ }).click();
        await page.getByRole('menuitemradio', { name: '低', exact: true }).click();
      } else {
        check(await page.getByRole('button', { name: /思考档位：/ }).count() === 0,
          `${model.family} 只有一个已确认思考档位时隐藏选择器`);
      }
    }
    await page.locator('textarea').fill('你好');
    await page.getByRole('button', { name: '发送问题', exact: true }).click();
    const run = await until(cid, value => value.status === 'succeeded');
    check(run.payload.model_snapshot.model_id === model.model_id && run.payload.model_snapshot.protocol === model.protocol,
      `${model.family} 网页选择、运行记录和实际协议一致`);
    if (model.provider === 'relay') check(run.payload.model_snapshot.thinking_mode === 'low', `${model.family} 网页低档思考被保存`);
    check(Boolean(run.output?.trim()), `${model.family} 的真实问候回答已返回`);
    await page.reload();
    await page.waitForSelector('.ws-message-column');
    const restoredText = await page.locator('.ws-message-column').innerText();
    check(restoredText.includes(run.output.trim().slice(0, 24)), `${model.family} 刷新后仍保留真实回答`);
    const times = await page.locator('.ws-progress time').allTextContents();
    check(times.length > 0 && times.every(text => /\d+\.\d(?:\s*秒|s|\s*分)/.test(text)), `${model.family} 耗时保留一位小数`);
    await page.screenshot({ path: resolve(output, `${model.family}-hello.png`) });
    report.greetings.push({ model: model.model_id, conversation_id: cid, run_id: run.run_id, status: run.status, thinking_mode: run.payload.model_snapshot.thinking_mode });

    if (!workSmoke || model.provider !== 'relay') continue;
    await page.locator('textarea').fill('请撰写一篇海事领域相关论文，并生成PDF。题目和结构由你确定。');
    const began = Date.now();
    await page.getByRole('button', { name: '发送问题', exact: true }).click();
    let work;
    while (Date.now() - began < 30000) {
      work = (await snapshot(cid)).runs.at(-1);
      if (work && work.run_id !== run.run_id && ['failed', 'succeeded', 'waiting_user'].includes(work.status)) break;
      await new Promise(done => setTimeout(done, 500));
    }
    assert(work && work.run_id !== run.run_id);
    assert(!['failed', 'cancelled', 'waiting_user'].includes(work.status), `${model.model_id}: ${work.status} ${work.error?.message || ''}`);
    if (work.status === 'running') {
      await page.getByRole('button', { name: '暂停回答', exact: true }).click();
      work = await until(cid, value => value.status === 'paused', 45000);
    }
    check(['paused', 'succeeded'].includes(work.status), `${model.family} 论文任务可真实推进并暂停`);
    const labels = (work.stages || []).flatMap(stage => [stage.label, ...stage.details]);
    check(labels.length > 0 && !labels.some(label => /理解问题|装配上下文/.test(label)), `${model.family} 步骤显示真实的处理和工具动作`);
    check((work.stages || []).some(stage => ['research', 'execution', 'files', 'writing'].includes(stage.id)), `${model.family} 已进入查资料、执行、写作或文件步骤`);
    await page.reload();
    await page.waitForSelector('.ws-message-column');
    await page.screenshot({ path: resolve(output, `${model.family}-work.png`) });
    const frozen = await until(cid, value => ['paused', 'succeeded'].includes(value.status));
    await new Promise(done => setTimeout(done, 2000));
    const later = (await snapshot(cid)).runs.at(-1);
    check(later.status === frozen.status && later.partial_output === frozen.partial_output && later.active_elapsed_ms === frozen.active_elapsed_ms,
      `${model.family} 暂停后正文和处理时间不再增长`);
    report.work.push({ model: model.model_id, conversation_id: cid, run_id: work.run_id, status: work.status, labels });
  }

  // Real HTTP graph with a fresh unpredictable file: the model must read its
  // tool result and return it over the same provider call ID.
  const conversation = (await api('/workspace/conversations', { title: 'C3 工具编号回传', mode: 'work' })).payload.conversation;
  const cid = conversation.conversation_id;
  const proof = `目录回传验证-${randomUUID()}`;
  const submitted = await api(`/workspace/conversations/${cid}/runs`, {
    conversation_id: cid, client_request_id: randomUUID(), text: '读取 proof.txt 并原样回复其中的文字。',
    model_profile_id: 'deepseek-flash', thinking_mode: 'low',
    graph: { nodes: [
      { id: 'seed', kind: 'tool', label: '准备验证内容', config: { tool_name: 'write', arguments: { path: 'proof.txt', content: proof } } },
      { id: 'read', kind: 'model', label: '核对内容', depends_on: ['seed'], config: { instruction: '必须调用 read 工具读取 proof.txt，只把读到的完整内容原样交付。' } },
    ] },
  });
  assert.equal(submitted.status, 202);
  const graphRun = await until(cid, run => run.status === 'succeeded');
  check(graphRun.output.includes(proof), '真实 DeepSeek 取得工具回传的随机文字并完成图任务');
  report.graph_run_id = graphRun.run_id;
  const before = (await snapshot(cid)).runs.length;
  const unavailableRelay = catalog.find(model => model.provider === 'relay' && !model.available);
  assert(unavailableRelay, '目录中保留至少一个不可用的模型');
  const rejected = await api(`/workspace/conversations/${cid}/runs`, {
    conversation_id: cid, client_request_id: randomUUID(), text: '你好', model_profile_id: unavailableRelay.profile_id,
  });
  check(rejected.status === 409 && rejected.payload.detail === 'model_profile_unavailable' && (await snapshot(cid)).runs.length === before,
    '不可用模型在创建任务前被拒绝');
  check(report.page_errors.length === 0, '浏览器没有脚本错误');
} finally {
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
