import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

// Read-only browser journey. It uses a real administrator session and only
// opens an existing account's log; it does not call a model or mutate data.
const base = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18180';
const adminEmail = process.env.ZHOUXU_TEST_ADMIN_EMAIL;
const adminPassword = process.env.ZHOUXU_TEST_ADMIN_PASSWORD;
assert.ok(adminEmail && adminPassword, '请通过本次进程环境提供管理员邮箱和密码');
const output = path.resolve(process.env.ZHOUXU_TEST_OUTPUT || '.ai-tmp/admin-conversation-log');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.ZHOUXU_CHROME_PATH ? { executablePath: process.env.ZHOUXU_CHROME_PATH } : {}),
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));

function visibleSegments(markdown) {
  return markdown
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/[`*_>#~-]/g, '')
    .split(/\r?\n/)
    .map(value => value.trim())
    .filter(value => value.length >= 6);
}

async function api(endpoint, token) {
  return context.request.get(`${base}/api${endpoint}`, { headers: { Authorization: `Bearer ${token}` } });
}

try {
  await page.goto(`${base}/admin/login`);
  await page.getByLabel('邮箱', { exact: true }).fill(adminEmail);
  await page.getByLabel('密码', { exact: true }).fill(adminPassword);
  const responsePromise = page.waitForResponse(response => response.url().endsWith('/api/auth/admin-login'));
  await page.getByRole('button', { name: '登录管理台', exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const session = await response.json();
  assert.equal(session.account.role, 'admin');
  const token = session.session.token;

  const accountsResponse = await api('/admin/accounts', token);
  assert.equal(accountsResponse.status(), 200);
  const accounts = (await accountsResponse.json()).accounts;
  let selected = null;
  let selectedLogs = null;
  for (const account of accounts) {
    const logs = await api(`/admin/accounts/${encodeURIComponent(account.id)}/conversations?limit=1`, token);
    if (logs.status() !== 200) continue;
    const body = await logs.json();
    if (body.conversations?.length) { selected = account; selectedLogs = body; break; }
  }
  assert.ok(selected, '预览库没有可供只读验收的会话');

  await page.getByRole('link', { name: '人员', exact: true }).click();
  await page.waitForURL(/\/admin\/accounts$/);
  await page.getByRole('searchbox', { name: '搜索账户' }).fill(selected.email);
  const accountConversationsLink = page.locator(`a[href="/admin/accounts/${selected.id}/conversations"]`);
  await accountConversationsLink.waitFor();
  await accountConversationsLink.click();
  await page.waitForURL(new RegExp(`/admin/accounts/${selected.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/conversations$`));
  const conversationCard = page.locator('.admin-conversation-card').filter({ hasText: selectedLogs.conversations[0].title });
  await conversationCard.waitFor();
  await conversationCard.click();
  await page.waitForURL(/\/conversations\//);
  await page.getByRole('heading', { name: selectedLogs.conversations[0].title, exact: true }).waitFor();
  const detailResponse = await api(`/admin/accounts/${encodeURIComponent(selected.id)}/conversations/${encodeURIComponent(selectedLogs.conversations[0].id)}?limit=50`, token);
  assert.equal(detailResponse.status(), 200);
  const detail = await detailResponse.json();
  assert.ok(detail.runs.length > 0);
  const runCard = page.locator('.admin-run-card').filter({ hasText: detail.runs[0].user_question }).first();
  await runCard.waitFor();
  if (detail.runs[0].public_answer) {
    const rendered = await runCard.locator('.admin-run-answer-text').innerText();
    const segments = visibleSegments(detail.runs[0].public_answer);
    assert.ok(segments.length > 0);
    const markers = [segments[0], segments[Math.floor(segments.length / 2)], segments.at(-1)];
    markers.forEach(marker => assert.ok(rendered.includes(marker), `公开回答缺少：${marker}`));
  }
  assert.equal(await page.locator('body').evaluate(body => /payload_json|system_prompt|hidden_reasoning|api_key|disk_path|skill_source/.test(body.innerText)), false);
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: selectedLogs.conversations[0].title, exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '桌面详情页出现横向溢出');
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(output, 'admin-conversation-detail.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: selectedLogs.conversations[0].title, exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '手机详情页出现横向溢出');
  await page.screenshot({ path: path.join(output, 'admin-conversation-detail-mobile.png'), fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, account_id: selected.id, conversation_id: detail.conversation.id, runs: detail.runs.length, output }));
} catch (error) {
  console.error(`未通过：${error.message}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}

