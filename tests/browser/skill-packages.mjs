import assert from 'node:assert/strict';
import { randomBytes, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cp, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { resolve, sep } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/skills-packages-20261121');
await mkdir(output, { recursive: true });
const credentials = { email: `skills-${randomUUID()}@example.test`, password: randomUUID(), display_name: '技能包验收' };
let token;
async function api(path, method = 'GET', body) {
  const response = await fetch(origin + '/api' + path, {
    method,
    signal: AbortSignal.timeout(120000),
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const payload = await response.json();
  assert(response.ok, `${method} ${path}: ${response.status} ${JSON.stringify(payload)}`);
  return payload;
}
await api('/healthz');
token = (await api('/auth/register', 'POST', credentials)).session.token;
const skillName = 'expense-summary';
const fixture = resolve('tests/fixtures/skills/expense-summary');
const packageHelper = resolve('tests/fixtures/skills/package.py');
const packageZip = (directory) => execFileSync(process.env.PYTHON || 'python', [packageHelper, directory], { maxBuffer: 4 * 1024 * 1024 });
const zip = packageZip(fixture);
const largeFixture = await mkdtemp(resolve(output, 'large-fixture-'));
let largeZip;
try {
  await cp(fixture, largeFixture, { recursive: true });
  await writeFile(resolve(largeFixture, 'assets/upload-size-probe.bin'), randomBytes(1200000));
  largeZip = packageZip(largeFixture);
  assert(largeZip.length > 1024 * 1024 && largeZip.length < 20 * 1024 * 1024);
} finally {
  assert(resolve(largeFixture).startsWith(output + sep), '临时目录必须属于本次验收');
  await rm(largeFixture, { recursive: true });
}
const browser = await chromium.launch({ headless: true, ...(process.env.ZHOUXU_CHROME_PATH ? { executablePath: process.env.ZHOUXU_CHROME_PATH } : {}) });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(30000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const checks = [];
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/workspace');
  await page.getByRole('link', { name: '技能', exact: true }).click();
  await page.getByRole('heading', { name: '内置技能', exact: true }).waitFor();
  await page.getByRole('heading', { name: '我的技能', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: /创建技能|社区分享|编辑技能/ }).count(), 0);
  const builtin = page.locator('section[aria-labelledby="builtin-skills"]');
  await builtin.getByRole('button', { name: '查看详情', exact: true }).first().click();
  assert.equal(await page.getByRole('dialog').getByRole('button', { name: '移除技能包', exact: true }).count(), 0);
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  checks.push('只有内置技能和我的技能；内置技能不能移除');

  await page.locator('input[type=file]').setInputFiles({ name: 'not-a-package.zip', mimeType: 'application/zip', buffer: Buffer.from('broken zip') });
  await page.getByRole('alert').filter({ hasText: '无法打开技能包' }).waitFor();
  await page.locator('input[type=file]').setInputFiles({ name: 'too-large.zip', mimeType: 'application/zip', buffer: Buffer.alloc(20 * 1024 * 1024 + 1) });
  await page.getByRole('alert').filter({ hasText: '20 MB' }).waitFor();
  const largeImportedResponse = page.waitForResponse(response => response.url().endsWith('/api/maritime/skills/import') && response.request().method() === 'POST');
  await page.locator('input[type=file]').setInputFiles({ name: 'upload-size-probe.zip', mimeType: 'application/zip', buffer: largeZip });
  const largeImportedHttp = await largeImportedResponse;
  assert.equal(largeImportedHttp.status(), 201, '超过1MB的合规ZIP应通过网站代理');
  const largeImported = await largeImportedHttp.json();
  await page.getByRole('dialog').waitFor();
  await page.getByRole('button', { name: '移除技能包', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert(!(await api('/maritime/skills')).skills.some(skill => skill.id === largeImported.id));
  checks.push('损坏包与超过20 MB的包给出清楚提示；1.2MB合规包可上传');

  const importedResponse = page.waitForResponse(response => response.url().endsWith('/api/maritime/skills/import') && response.request().method() === 'POST');
  await page.locator('input[type=file]').setInputFiles({ name: `${skillName}.zip`, mimeType: 'application/zip', buffer: zip });
  const importedHttp = await importedResponse;
  assert.equal(importedHttp.status(), 201);
  const imported = await importedHttp.json();
  await page.getByRole('dialog').getByText('Python 3.11 标准库；工作模式', { exact: true }).waitFor();
  await page.locator('.md-skill-files li').filter({ hasText: 'references/rules.md' }).getByRole('button', { name: '查看', exact: true }).click();
  await page.locator('.md-skill-resource pre').filter({ hasText: '费用汇总规则' }).waitFor();
  await page.locator('.md-skill-files li').filter({ hasText: 'assets/header.csv' }).getByRole('button', { name: '查看', exact: true }).click();
  await page.locator('.md-skill-resource pre').filter({ hasText: '项目,金额（元）' }).waitFor();
  assert.equal(await page.locator('.md-skill-files li').count(), imported.package.files.length);
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'skill-detail.png') });
  await page.keyboard.press('Escape');
  await page.reload();
  const mine = page.locator('section[aria-labelledby="my-skills"]');
  await mine.getByText(skillName, { exact: true }).waitFor();
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'skills-desktop.png') });
  checks.push('标准ZIP导入后刷新仍保留，包内参考资料可读，脚本只作为文件展示');

  await mine.locator('article').filter({ hasText: skillName }).getByRole('button', { name: '在工作模式使用', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/workspace' && url.searchParams.get('skill') === imported.id);
  await page.getByRole('button', { name: '工作', exact: true, pressed: true }).waitFor();
  await page.goto(origin + '/workspace');
  await page.getByRole('button', { name: '聊天', exact: true, pressed: true }).waitFor();
  const normalChat = (await api('/workspace/conversations', 'POST', { title: '保留聊天模式', mode: 'chat' })).conversation;
  const normalId = normalChat.conversation_id || normalChat.id;
  await page.goto(`${origin}/workspace/chat/${normalId}?skill=${encodeURIComponent(imported.id)}&mode=work`);
  await page.getByLabel('聊天模式，已固定').waitFor();
  checks.push('技能入口预选工作模式；普通新对话和已有聊天模式保持正确');

  await page.goto(origin + '/workspace/skills');
  await page.setViewportSize({ width: 390, height: 844 });
  await mine.getByText(skillName, { exact: true }).waitFor();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '手机页面横向溢出');
  await page.screenshot({ animations: 'disabled', path: resolve(output, 'skills-mobile.png') });
  await mine.locator('article').filter({ hasText: skillName }).getByRole('button', { name: '查看详情', exact: true }).click();
  const removedResponse = page.waitForResponse(response => response.url().endsWith(`/api/maritime/skills/${imported.id}`) && response.request().method() === 'DELETE');
  await page.getByRole('button', { name: '移除技能包', exact: true }).click();
  assert.equal((await removedResponse).status(), 200);
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.reload();
  await page.getByText('上传一个 ZIP 技能包，它会出现在这里。', { exact: true }).waitFor();
  assert.equal(await mine.getByText(skillName, { exact: true }).count(), 0);
  checks.push('手机宽度无横向溢出；本人上传包可移除，刷新后不会再次出现');
  assert.deepEqual(errors, [], `浏览器脚本错误: ${errors.join('; ')}`);
  const report = { checks, imported_skill: imported.id, upload_size_bytes: largeZip.length, page_errors: errors, model_requests: 0 };
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
