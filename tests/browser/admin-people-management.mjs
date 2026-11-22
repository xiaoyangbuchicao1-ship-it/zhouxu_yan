import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18180';
const email = process.env.ZHOUXU_TEST_ADMIN_EMAIL;
const password = process.env.ZHOUXU_TEST_ADMIN_PASSWORD;
assert.ok(email && password, '请提供管理员测试账号');
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
  await page.waitForURL(/\/admin\/overview$/);
  await page.goto(`${base}/admin/accounts`);
  await page.getByRole('heading', { name: '账户', exact: true }).waitFor();
  assert.ok(await page.getByRole('searchbox', { name: '搜索账户' }).count());
  await page.getByRole('combobox', { name: '角色筛选' }).click();
  await page.getByRole('option', { name: '普通用户', exact: true }).click();
  await page.getByRole('combobox', { name: '状态筛选' }).click();
  await page.getByRole('option', { name: '正常', exact: true }).click();
  const card = page.locator('.admin-account-card').filter({ hasNotText: email }).first();
  await card.waitFor();
  const accountEmail = await card.locator('p').count() ? await card.locator('p').first().innerText() : await card.locator('h2').innerText();
  await card.getByRole('button', { name: '停用', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: '确认', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '已更新' }).waitFor();
  await page.getByRole('combobox', { name: '状态筛选' }).click();
  await page.getByRole('option', { name: '已停用', exact: true }).click();
  await page.getByText(accountEmail, { exact: true }).waitFor();
  await page.getByRole('combobox', { name: '状态筛选' }).click();
  await page.getByRole('option', { name: '正常', exact: true }).click();
  const restored = page.locator('.admin-account-card').filter({ hasText: accountEmail });
  await restored.getByRole('button', { name: '启用', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: '确认', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '已更新' }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, account: accountEmail }));
} finally {
  await browser.close();
}
