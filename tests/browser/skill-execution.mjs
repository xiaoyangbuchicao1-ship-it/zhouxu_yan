// Real browser + official DeepSeek. Two tiny tasks; no relay/model sweeps.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/skill-packages-20261121/execution');
await mkdir(output, { recursive: true });
const credentials = { email: 'skill-run-' + randomUUID() + '@example.test', password: randomUUID(), display_name: '技能执行验收' };
let token;
async function response(path, method = 'GET', body, auth = token) {
  return fetch(origin + '/api' + path, {
    method, signal: AbortSignal.timeout(30000),
    headers: { ...(auth ? { Authorization: 'Bearer ' + auth } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}
async function api(path, method = 'GET', body) {
  const res = await response(path, method, body);
  assert(res.ok, method + ' ' + path + ': ' + res.status + ' ' + await res.clone().text());
  return res.json();
}
assert.equal((await response('/healthz')).status, 200);
token = (await api('/auth/register', 'POST', credentials)).session.token;
const verifier = await readFile(resolve('tests/browser/verify_skill_receipts.py'), 'utf8');
function receipts(cid, runId) {
  return JSON.parse(execFileSync('docker', ['exec', '-i', 'zhouxu-api', 'python', '-B', '-', cid, runId], { input: verifier, encoding: 'utf8' }));
}
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const report = { checks: [], runs: [], model: 'deepseek-flash', page_errors: errors };
async function finish(cid, runId) {
  const end = Date.now() + 240000;
  while (Date.now() < end) {
    const run = (await api('/workspace/conversations/' + cid + '/snapshot')).runs.find(item => item.run_id === runId);
    if (['succeeded', 'failed', 'cancelled', 'waiting_user'].includes(run.status)) return run;
    await new Promise(done => setTimeout(done, 1000));
  }
  throw new Error('skill run timed out: ' + runId);
}
async function uploadAndUse(name) {
  await page.goto(origin + '/workspace/skills');
  const zip = execFileSync(process.env.PYTHON || 'python', [resolve('tests/fixtures/skills/package.py'), resolve('tests/fixtures/skills/' + name)]);
  const incoming = page.waitForResponse(res => res.url().endsWith('/api/maritime/skills/import') && res.request().method() === 'POST');
  await page.locator('input[type=file]').setInputFiles({ name: name + '.zip', mimeType: 'application/zip', buffer: zip });
  const res = await incoming;
  assert.equal(res.status(), 201);
  const skill = await res.json();
  await page.getByRole('dialog').waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  const card = page.locator('section[aria-labelledby="my-skills"] article').filter({ hasText: name });
  await card.getByText(name, { exact: true }).waitFor();
  await card.getByRole('button', { name: '在工作模式使用', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('skill') === skill.id);
  assert.equal(await page.getByRole('button', { name: '工作', exact: true }).getAttribute('aria-pressed'), 'true');
  await page.locator('.ws-model-trigger').click();
  await page.getByRole('menuitem', { name: 'DeepSeek Flash', exact: true }).click();
  const thinking = page.getByRole('button', { name: /^思考档位：/ });
  if (await thinking.getAttribute('aria-label') !== '思考档位：关闭') {
    await thinking.click();
    await page.getByRole('menuitemradio', { name: '关闭', exact: true }).click();
  }
  return skill;
}
async function send(prompt) {
  await page.locator('textarea').fill(prompt);
  const incoming = page.waitForResponse(res => /\/workspace\/conversations\/[^/]+\/runs$/.test(res.url()) && res.request().method() === 'POST');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  const res = await incoming;
  assert.equal(res.status(), 202, await res.text());
  const run = (await res.json()).run;
  report.runs.push({ run_id: run.run_id, conversation_id: run.conversation_id });
  assert.equal(run.payload.mode, 'work');
  assert.equal(run.payload.model_snapshot.provider, 'deepseek');
  assert.equal(run.payload.model_snapshot.profile_id, 'deepseek-flash');
  console.log(JSON.stringify({ started: run.run_id }));
  return run;
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/workspace');
  const skill = await uploadAndUse('expense-summary');
  const started = await send('请严格使用选中的费用技能包，读取规则并执行原脚本。两条费用：港口费12.50元，资料费7.50元。先写expenses.json，再按read_skill返回的package_path调用scripts/summarize.py，输出expense-summary.csv。不要自己重写脚本，不检索其他资料，最后简短交付文件。');
  const cid = started.conversation_id;
  await page.goto(origin + '/workspace/skills'); // Leave while backend is executing.
  const run = await finish(cid, started.run_id);
  assert.equal(run.status, 'succeeded', JSON.stringify(run.error));
  assert.equal(run.payload.facts.skill_id, skill.id);
  const evidence = receipts(cid, run.run_id);
  assert(evidence.skills.some(item => item.skill_id === skill.id && item.package_path === '.skills/' + skill.id + '/1'));
  assert(evidence.commands.some(item => item.outcome === 'ok' && item.exit_code === 0 && item.command?.includes('/scripts/summarize.py')));
  const file = run.artifacts.find(item => item.name === 'expense-summary.csv');
  assert(file, '没有实际CSV交付');
  const download = await response('/workspace/runs/' + run.run_id + '/artifacts/' + file.artifact_id + '?conversation_id=' + cid);
  assert.equal(download.status, 200);
  const bytes = Buffer.from(await download.arrayBuffer());
  assert.equal(createHash('sha256').update(bytes).digest('hex'), file.sha256);
  assert.deepEqual(bytes.toString('utf8').trim().split(/\r?\n/), ['项目,金额（元）', '港口费,12.50', '资料费,7.50', '合计,20.00']);
  await writeFile(resolve(output, 'expense-summary.csv'), bytes);
  await page.goto(origin + '/workspace/chat/' + cid);
  const downloadable = page.getByRole('button', { name: '下载 expense-summary.csv', exact: true });
  await downloadable.waitFor();
  const userDownload = page.waitForEvent('download');
  await downloadable.click();
  const saved = await userDownload;
  await saved.saveAs(resolve(output, 'browser-expense-summary.csv'));
  assert.deepEqual(await readFile(resolve(output, 'browser-expense-summary.csv')), bytes);
  const before = (await api('/workspace/conversations/' + cid + '/snapshot')).runs;
  await page.reload();
  await downloadable.waitFor();
  const after = (await api('/workspace/conversations/' + cid + '/snapshot')).runs;
  assert.deepEqual(after.map(item => item.run_id), before.map(item => item.run_id));
  assert.equal(receipts(cid, run.run_id).commands.length, evidence.commands.length);
  await page.screenshot({ path: resolve(output, 'real-skill-result.png'), animations: 'disabled' });
  report.checks.push('网页上传后实际执行原脚本，按规则和模板交付合计20.00元的CSV');
  report.checks.push('执行中离开页面、重开和刷新后仍是同一任务，未重复执行');
  report.receipts = evidence;

  const other = (await api('/auth/register', 'POST', { email: 'skill-other-' + randomUUID() + '@example.test', password: randomUUID(), display_name: '权限验收' })).session.token;
  assert.equal((await response('/maritime/skills/' + skill.id + '/resources?path=references/rules.md', 'GET', undefined, other)).status, 404);
  assert.equal((await response('/workspace/runs/' + run.run_id + '/artifacts/' + file.artifact_id + '?conversation_id=' + cid, 'GET', undefined, other)).status, 404);
  report.checks.push('另一账户无法读取私有技能与生成的文件');

  const bad = await uploadAndUse('broken-summary');
  const failedStart = await send('请读取选中的测试技能，只执行一次它的scripts/create_report.py，不修复脚本，不生成替代文件。执行失败就停止，并把原始错误简短告诉我。');
  const failed = await finish(failedStart.conversation_id, failedStart.run_id);
  const badEvidence = receipts(failedStart.conversation_id, failedStart.run_id);
  assert(badEvidence.skills.some(item => item.skill_id === bad.id));
  const badCommands = badEvidence.commands.filter(item => item.command?.includes('create_report.py'));
  assert.equal(badCommands.length, 1, '失败脚本只执行一次');
  assert.equal(badCommands[0].outcome, 'ok', 'bash 调用本身应留下真实回执');
  assert(badCommands[0].stderr.includes('SKILL_TEST_FAILURE'));
  assert(badCommands[0].exit_code !== 0 || /(?:^|\n)EXIT=([1-9]\d*)/.test(badCommands[0].stdout || ''), '必须保留脚本失败的退出证据');
  assert.equal(failed.artifacts.length, 0);
  assert((failed.output || failed.partial_output || failed.error?.message || '').includes('缺少必需模板'));
  await page.goto(origin + '/workspace/chat/' + failedStart.conversation_id);
  await page.getByText(/缺少必需模板/).first().waitFor();
  assert.equal(await page.locator('.md-delivery-file').count(), 0);
  await page.screenshot({ path: resolve(output, 'real-skill-failure.png'), animations: 'disabled' });
  report.failure_receipts = badEvidence;
  report.checks.push('失败脚本保留错误且只执行一次，网页说明实际原因，不交付文件');
  assert.deepEqual(errors, []);
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
