import assert from 'node:assert/strict';
import { chromium } from 'playwright';

// Long public answers in the admin log stay readable without making the whole
// page a single wall of text. Existing short answers remain unchanged.
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
  await page.getByRole('button', { name: '登录管理台', exact: true }).click();
  await page.waitForURL(/\/admin$/);
  await page.getByRole('link', { name: '人员', exact: true }).click();
  await page.waitForURL(/\/admin\/accounts$/);
  const accountLink = page.locator('a.admin-card-link').first();
  await accountLink.waitFor();
  await accountLink.click();
  await page.waitForURL(/\/conversations$/);
  const conversation = page.locator('a.admin-conversation-card').first();
  await conversation.waitFor();
  // The preview seed contains short answers. Add a deterministic long-answer
  // fixture at the HTTP boundary so this test exercises the real React page
  // without writing to SQLite or the formal service.
  await page.route('**/api/admin/accounts/**/conversations/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.split('/').length < 7) return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    if (body.runs?.length) {
      const seed = body.runs[0].public_answer || '回答正文';
      body.runs[0].public_answer = `${seed}\n\n${Array.from({ length: 90 }, (_, index) => `第${index + 1}段：这是用于阅读布局验收的长回答。`).join('\n')}`;
    }
    await route.fulfill({ response, body: JSON.stringify(body) });
  });
  await conversation.click();
  await page.waitForURL(/\/conversations\//);
  await page.locator('.admin-run-card').first().waitFor();

  const longAnswers = page.locator('.admin-run-answer-text');
  const count = await longAnswers.count();
  assert.ok(count > 0, '详情页应显示回答正文');
  let longCount = 0;
  for (let index = 0; index < count; index += 1) {
    const answer = longAnswers.nth(index);
    const text = await answer.innerText();
    if (text.length <= 1200) continue;
    longCount += 1;
    const wrapper = answer.locator('..');
    const button = wrapper.locator('button.admin-run-expand');
    await button.click();
    assert.equal(await button.getAttribute('aria-expanded'), 'true', '长回答展开后应有明确状态');
    assert.equal((await answer.innerText()).length, text.length, '展开不能丢失回答正文');
    await wrapper.getByRole('button', { name: '收起回答', exact: true }).click();
  }
  assert.ok(longCount > 0, '测试夹具应产生至少一条超长回答');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '桌面详情页出现横向溢出');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, '手机详情页出现横向溢出');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, answers: count, long_answers: longCount }));
} finally {
  await browser.close();
}
