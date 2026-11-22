import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve('.ai-tmp/preview-verify-20261121');
await mkdir(output, { recursive: true });
const credentials = { email: `preview-${randomUUID()}@example.test`, password: randomUUID(), display_name: '预览验收' };
let token;
async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    signal: AbortSignal.timeout(20000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}
assert.equal((await api('/healthz')).response.status, 200);
const registered = await api('/auth/register', credentials);
assert.equal(registered.response.status, 201);
token = registered.payload.session.token;
const conversation = (await api('/workspace/conversations', { title: '交付与预览验收', mode: 'work' })).payload.conversation;
const cid = conversation.conversation_id;

// The graph uses the production tool and file handlers through the public HTTP
// boundary, but avoids a model call. It creates a valid DOCX, a PDF with the
// same basename, and a plain text file so the UI can be checked against both
// branches of the preview rule.
const command = `python -c "from docx import Document; import pathlib; pathlib.Path('papers').mkdir(); d=Document(); d.add_paragraph('预览验收文件'); d.save('papers/paper.docx'); open('papers/paper.pdf','wb').write(b'%PDF-1.4\\n%EOF\\n'); open('notes.txt','w',encoding='utf-8').write('仅供下载')"`;
const graph = {
  version: 'preview-verification-1',
  nodes: [
    { id: 'fixture', kind: 'tool', label: '准备交付文件', config: { tool_name: 'bash', arguments: { command } }, side_effect: 'idempotent' },
    { id: 'paper-docx', kind: 'file', label: 'paper.docx', depends_on: ['fixture'], config: { path: 'papers/paper.docx' } },
    { id: 'paper-pdf', kind: 'file', label: 'paper.pdf', depends_on: ['fixture'], config: { path: 'papers/paper.pdf' } },
    { id: 'notes', kind: 'file', label: 'notes.txt', depends_on: ['fixture'], config: { path: 'notes.txt' } },
  ],
};
const submitted = await api(`/workspace/conversations/${cid}/runs`, {
  conversation_id: cid,
  client_request_id: `preview-${randomUUID()}`,
  text: '准备预览验收文件',
  graph,
});
assert.equal(submitted.response.status, 202, JSON.stringify(submitted.payload));
const runId = submitted.payload.run.run_id;
async function snapshot() {
  return (await api(`/workspace/conversations/${cid}/snapshot`)).payload.runs.find(run => run.run_id === runId);
}
let run;
for (let i = 0; i < 120; i += 1) {
  run = await snapshot();
  if (run?.status === 'succeeded') break;
  if (['failed', 'cancelled'].includes(run?.status)) throw new Error(`fixture graph failed: ${JSON.stringify(run)}`);
  await new Promise(resolvePromise => setTimeout(resolvePromise, 500));
}
assert.equal(run?.status, 'succeeded');
assert.deepEqual((run.artifacts || []).map(item => item.name).sort(), ['notes.txt', 'paper.docx', 'paper.pdf']);
const deliverables = await api(`/maritime/runs/${runId}/deliverables?conversation_id=${cid}`);
console.log(JSON.stringify({ deliverables_status: deliverables.response.status, deliverables: deliverables.payload }));
assert.equal(deliverables.response.status, 200);

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(20000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => { if (response.url().includes('/deliverables')) console.log(JSON.stringify({ deliverables_request: response.url(), status: response.status() })); });
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await page.locator('.ws-answer-tools').waitFor();
  console.log(JSON.stringify({ pathname: await page.evaluate(() => location.pathname), run_id: runId }));
  await page.waitForTimeout(1500);
  const bodyText = await page.locator('body').innerText();
  console.log(JSON.stringify({ body_preview: bodyText.slice(-2000), artifact_names: (run.artifacts || []).map(item => item.name) }));
  assert.equal((bodyText.match(/本次交付/g) || []).length, 1, '交付区域只出现一次');
  assert.equal(await page.locator('[aria-label="交付文件 paper.docx"]').count(), 1);
  assert.equal(await page.locator('[aria-label="交付文件 paper.pdf"]').count(), 1);
  assert.equal(await page.locator('[aria-label="交付文件 notes.txt"]').count(), 1);
  assert.equal(await page.getByRole('button', { name: '预览 paper.docx' }).count(), 1, 'DOCX 有同名 PDF 时可预览');
  assert.equal(await page.getByRole('button', { name: '预览 paper.pdf' }).count(), 1, 'PDF 可预览');
  assert.equal(await page.getByRole('button', { name: '预览 notes.txt' }).count(), 0, '没有预览文件的文本只显示下载');
  assert(!bodyText.includes('preview_type_not_supported'));
  assert(!bodyText.includes('/workspace/runs/') && !bodyText.includes('papers/'));
  await page.getByRole('button', { name: '预览 paper.docx' }).click();
  await page.locator('.md-office-preview-frame').waitFor();
  assert.equal(await page.locator('.md-office-preview-frame').count(), 1);
  const report = { conversation_id: cid, run_id: runId, artifacts: run.artifacts, checks: ['交付区域只出现一次', 'DOCX 与同名 PDF 显示预览', '无预览文件只显示下载', '预览接口返回内容'], page_errors: errors };
  await page.screenshot({ path: resolve(output, 'preview-delivery.png') });
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.checks, run_id: runId, artifacts: run.artifacts, page_errors: errors }));
} finally {
  await browser.close();
}
