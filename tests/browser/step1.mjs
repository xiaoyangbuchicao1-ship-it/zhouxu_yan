// Real HTTP + Chromium + configured model acceptance. No mock provider or saved credentials.
import assert from 'node:assert/strict';
import { randomBytes, randomUUID, createHash } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const paper = process.argv.includes('--paper');
const outputIndex = process.argv.indexOf('--output-dir');
const outputDir = outputIndex >= 0 ? resolve(process.argv[outputIndex + 1]) : null;
if (paper) assert(outputDir, '--paper requires --output-dir for PDF/DOCX verification');
const credentials = {
  email: `step1-${randomUUID()}@example.test`,
  password: randomBytes(24).toString('base64url'),
  display_name: '第一步自动验收',
};
let token;
async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(20000),
  });
  const data = await response.json();
  return { status: response.status, data };
}
assert.equal((await api('/healthz')).status, 200);
const registered = await api('/auth/register', credentials);
assert.equal(registered.status, 201);
token = registered.data.session.token;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(10000);
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.name));
async function snapshot(cid) {
  const response = await api(`/workspace/conversations/${cid}/snapshot`);
  assert.equal(response.status, 200);
  return response.data.runs.at(-1);
}
async function until(cid, predicate, label, timeout = 180000) {
  const deadline = Date.now() + timeout;
  let announce = 0;
  while (Date.now() < deadline) {
    const run = await snapshot(cid);
    if (run && predicate(run)) return run;
    if (run && ['failed', 'cancelled', 'succeeded'].includes(run.status)) {
      throw new Error(`${label}: unexpected ${run.status}; run=${run.run_id}`);
    }
    if (Date.now() > announce) {
      console.log(JSON.stringify({ waiting: label, status: run?.status, run_id: run?.run_id }));
      announce = Date.now() + 30000;
    }
    await new Promise(done => setTimeout(done, 1000));
  }
  throw new Error(`${label}: deadline exceeded; conversation=${cid}`);
}
async function openNew(title, prompt) {
  const response = await api('/workspace/conversations', { title });
  assert.equal(response.status, 201);
  const cid = response.data.conversation.conversation_id;
  await page.goto(`${origin}/workspace/chat/${cid}`);
  await page.locator('textarea').fill(prompt);
  await page.locator('textarea').press('Enter');
  return cid;
}
async function staleChoice(cid, gate) {
  const response = await api(`/workspace/runs/${gate.run_id}/resume`, {
    conversation_id: cid, request_id: gate.pending_request_ids[0], answer: 'upgrade_work',
  });
  assert.equal(response.status, 409, 'answered gate must not mutate Run');
}
async function waitForActions(cid, rid, count) {
  const response = await fetch(`${origin}/api/workspace/conversations/${cid}/events?run_id=${rid}&timeout_seconds=120`, {
    headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(130000),
  });
  assert.equal(response.status, 200);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', actions = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error('Run ended before the pause checkpoint');
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === 'progress' && event.stage === 'action' && ++actions >= count) return;
      }
    }
  } finally {
    await reader.cancel();
  }
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/workspace' || url.pathname.startsWith('/workspace/'));

  const refusalCid = await openNew('第一步：显式模式确认', '请创建一个名为 acceptance.txt 的文件，内容是 hello。');
  const gate = await until(refusalCid, r => r.status === 'waiting_user', 'mode choice');
  for (const answer of ['', null, '这不是授权，只是补充一句话']) {
    const bad = await api(`/workspace/runs/${gate.run_id}/resume`, {
      conversation_id: refusalCid, request_id: gate.pending_request_ids[0], answer,
    });
    assert.equal(bad.status, 422);
  }
  const alias = await api(`/workspace/conversations/${refusalCid}/runs`, {
    conversation_id: refusalCid, dispatch_mode: 'resume', target_run_id: gate.run_id,
    request_id: gate.pending_request_ids[0], answer: '这不是授权',
  });
  assert.equal(alias.status, 422);
  await page.locator('textarea').fill('请只回复 hello。');
  await page.locator('textarea').press('Enter');
  assert.equal(await page.locator('textarea').inputValue(), '请只回复 hello。');
  assert.equal((await snapshot(refusalCid)).status, 'waiting_user');
  await page.getByRole('button', { name: '保持聊天模式', exact: true }).click();
  const declined = await until(refusalCid, r => r.status === 'succeeded', 'read-only completion');
  assert.equal(declined.payload.mode, 'chat');
  assert.equal(declined.artifacts.length, 0);
  assert.match(declined.output, /hello/i);
  await staleChoice(refusalCid, gate);
  assert.equal((await api(`/workspace/conversations/${refusalCid}`)).data.conversation.mode, 'chat');
  console.log(JSON.stringify({ passed: 'explicit refusal / invalid / stale / draft', run_id: gate.run_id }));

  const prompt = paper
    ? '请撰写一篇海事领域论文，并生成PDF和Word。聚焦海上货物运输迟延交付责任，使用论文生成Skill，实际核验法规、案例和论文资料。'
    : '这是运行恢复验收，不是法律研究。请先读取 maritime-legal-research Skill 确认可读，无须检索法律。然后用 write 创建 recovery.txt，内容为“第一章\\n”，接着单独用 bash 执行 sleep 20，最后用 write append=true 追加“第二章\\n”并交付文件。每个动作分开调用，已有章节不得重复写入。';
  const cid = await openNew('第一步：真实暂停恢复', prompt);
  const upgradeGate = await until(cid, r => r.status === 'waiting_user', 'work authorization');
  await page.locator('textarea').fill(paper ? '请区分约定交付时间和合理交付时间，并保留真实脚注。' : '请按动作顺序执行。');
  await page.getByRole('button', { name: '升级为工作模式', exact: true }).click();
  await until(cid, r => r.status === 'running', 'start work');
  await waitForActions(cid, upgradeGate.run_id, 3);
  await page.getByRole('button', { name: '暂停回答', exact: true }).click();
  const paused = await until(cid, r => r.status === 'paused', 'persist pause');
  assert.equal(paused.run_id, upgradeGate.run_id);
  await page.reload();
  await page.getByRole('button', { name: '继续处理', exact: true }).last().waitFor();
  const guidance = paper ? '请遵循附件补充要求，继续完成原论文和双文件。' : '继续原任务；先检查已有文件，已有第一章不要重复追加。完成第二章后依附件追加验收标记。';
  const attachment = paper ? '请在结语讨论交易可预见性，不得编造来源。' : '请在 recovery.txt 末尾独占一行追加：附件已读。';
  // Hold the real upload request to reproduce clicking Continue during upload.
  let releaseUpload;
  const uploadGate = new Promise(done => { releaseUpload = done; });
  const uploadPattern = `**/api/workspace/conversations/${cid}/materials`;
  await page.route(uploadPattern, async route => {
    if (route.request().method() === 'POST') await uploadGate;
    await route.continue();
  });
  await page.locator('input[type=file]').setInputFiles({ name: '验收补充要求.txt', mimeType: 'text/plain', buffer: Buffer.from(attachment) });
  await page.locator('textarea').fill(guidance);
  assert(await page.getByRole('button', { name: '继续处理', exact: true }).last().isDisabled(), 'Continue must wait for upload');
  const uploaded = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/materials'));
  releaseUpload();
  assert.equal((await uploaded).status(), 201);
  await page.unroute(uploadPattern);
  const continuedRequest = page.waitForRequest(r => r.method() === 'POST' && r.url().endsWith(`/runs/${paused.run_id}/continue`));
  await page.getByRole('button', { name: '继续处理', exact: true }).last().click();
  const sent = (await continuedRequest).postDataJSON();
  assert.equal(sent.guidance, guidance);
  assert.equal(sent.attached_material_ids.length, 1);
  const completed = await until(cid, r => r.status === 'succeeded', 'same Run delivery', paper ? 1800000 : 300000);
  assert.equal(completed.run_id, paused.run_id);
  assert.equal(completed.payload.text, prompt);
  assert(completed.continuation_guidance.includes(guidance));
  assert.equal(completed.payload.attached_material_ids.length, 1);
  assert(completed.active_elapsed_ms >= paused.active_elapsed_ms);
  await staleChoice(cid, upgradeGate);
  await page.reload();
  const expected = paper ? ['paper.docx', 'paper.pdf'] : ['recovery.txt'];
  assert.deepEqual(completed.artifacts.map(a => a.name).sort(), expected);
  await page.getByRole('group', { name: `交付文件 ${expected[0]}`, exact: true })
    .getByRole('button', { name: `下载 ${expected[0]}`, exact: true }).waitFor();
  for (let index = 0; index < expected.length; index++) {
    const downloaded = page.waitForEvent('download');
    await page.getByRole('group', { name: `交付文件 ${expected[index]}`, exact: true })
      .getByRole('button', { name: `下载 ${expected[index]}`, exact: true }).click();
    const download = await downloaded;
    const chunks = [];
    for await (const chunk of await download.createReadStream()) chunks.push(chunk);
    const bytes = Buffer.concat(chunks);
    const artifact = completed.artifacts.find(a => a.name === download.suggestedFilename());
    assert(artifact);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), artifact.sha256);
    if (!paper) {
      const content = bytes.toString('utf8');
      for (const phrase of ['第一章', '第二章', '附件已读']) assert.equal(content.split(phrase).length - 1, 1);
    }
    if (outputDir) {
      await mkdir(outputDir, { recursive: true });
      await download.saveAs(resolve(outputDir, artifact.name));
    }
  }
  assert.deepEqual(pageErrors, []);
  console.log(JSON.stringify({ passed: 'pause / refresh / guidance / attachment / same Run / authorized download',
    conversation_id: cid, run_id: completed.run_id, artifacts: completed.artifacts.map(a => ({ name: a.name, sha256: a.sha256 })) }));
} finally {
  await browser.close();
}
