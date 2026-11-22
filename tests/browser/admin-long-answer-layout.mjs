import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// This journey deliberately reads a long answer that is already persisted in
// the preview database. It must never manufacture answer text at the HTTP
// boundary: that would only test a React fixture, not the real data path.
const base = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18180';
const email = process.env.ZHOUXU_TEST_ADMIN_EMAIL;
const password = process.env.ZHOUXU_TEST_ADMIN_PASSWORD;
assert.ok(email && password, '请通过本次进程环境提供管理员邮箱和密码');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));

try {
  await page.goto(`${base}/admin/login`);
  await page.getByLabel('邮箱', { exact: true }).fill(email);
  await page.getByLabel('密码', { exact: true }).fill(password);
  const loginResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/auth/admin-login'));
  await page.getByRole('button', { name: '登录管理台', exact: true }).click();
  const loginResponse = await loginResponsePromise;
  const session = await loginResponse.json();
  assert.equal(session.account.role, 'admin');
  const token = session.session.token;
  const request = (path) => context.request.get(`${base}/api${path}`, { headers: { Authorization: `Bearer ${token}` } });

  const accounts = (await (await request('/admin/accounts')).json()).accounts;
  let target = null;
  for (const account of accounts) {
    const response = await request(`/admin/accounts/${encodeURIComponent(account.id)}/conversations?limit=20`);
    if (!response.ok()) continue;
    const body = await response.json();
    for (const conversation of body.conversations || []) {
      const detailResponse = await request(`/admin/accounts/${encodeURIComponent(account.id)}/conversations/${encodeURIComponent(conversation.id)}?limit=50`);
      if (!detailResponse.ok()) continue;
      const detail = await detailResponse.json();
      const longRun = detail.runs.find(run => (run.public_answer || '').length > 1200);
      if (longRun) { target = { account, conversation, detail, longRun }; break; }
    }
    if (target) break;
  }
  assert.ok(target, '预览库没有已持久化的超长回答；请先通过真实用户入口生成测试回答');

  await page.getByRole('link', { name: '人员', exact: true }).click();
  await page.waitForURL(/\/admin\/accounts$/);
  await page.getByRole('searchbox', { name: '搜索账户' }).fill(target.account.email);
  await page.locator(`a[href="/admin/accounts/${target.account.id}/conversations"]`).click();
  await page.waitForURL(/\/conversations$/);
  await page.locator(`a[href="/admin/accounts/${target.account.id}/conversations/${target.conversation.id}"]`).click();
  await page.waitForURL(/\/conversations\//);
  const answer = page.locator('.admin-run-answer-text').filter({ hasText: target.longRun.public_answer.slice(0, 40) }).first();
  await answer.waitFor();
  // The full answer is visible on first render; the user can collapse it.
  assert.ok((await answer.innerText()).length >= target.longRun.public_answer.length - 8, '管理员详情首屏应显示完整回答');
  const collapse = answer.locator('..').getByRole('button', { name: '收起回答', exact: true });
  await collapse.click();
  assert.equal(await collapse.getAttribute('aria-expanded'), 'false');
  const expand = answer.locator('..').getByRole('button', { name: '展开完整回答', exact: true });
  await expand.click();
  assert.equal(await expand.getAttribute('aria-expanded'), 'true');
  assert.ok((await answer.innerText()).length >= target.longRun.public_answer.length - 8, '展开后不能丢失回答正文');
  await page.reload({ waitUntil: 'networkidle' });
  assert.ok((await page.locator('.admin-run-answer-text').filter({ hasText: target.longRun.public_answer.slice(0, 40) }).first().innerText()).length >= target.longRun.public_answer.length - 8, '刷新后仍显示完整回答');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '详情页出现横向溢出');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '手机详情页出现横向溢出');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, run_id: target.longRun.run_id, answer_length: target.longRun.public_answer.length }));
} finally {
  await browser.close();
}


