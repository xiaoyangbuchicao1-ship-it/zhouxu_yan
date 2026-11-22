import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

// All mutations are restricted to the fresh account registered by this run.
// No paid model calls, stored credentials, browser traces, or server-data deletion.
const base = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const adminEmail = process.env.ZHOUXU_TEST_ADMIN_EMAIL;
const adminPassword = process.env.ZHOUXU_TEST_ADMIN_PASSWORD;
assert.ok(adminEmail && adminPassword, '请通过本次进程环境提供管理员邮箱和密码');
const output = path.resolve(process.env.ZHOUXU_TEST_OUTPUT || '.ai-tmp/admin-console-20261122');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.ZHOUXU_CHROME_PATH ? { executablePath: process.env.ZHOUXU_CHROME_PATH } : {}),
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
let adminToken;
let testAccount;
let testPassword;
let step = '';
let testAccountRestored = false;
const results = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, message) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await sleep(100);
  }
  assert.fail(message);
}
async function check(name, run) {
  step = name;
  await run();
  results.push({ name, passed: true });
  console.log(`通过：${name}`);
}
async function api(method, endpoint, data, token = adminToken) {
  return context.request.fetch(`${base}/api${endpoint}`, {
    method, data, headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
}
async function login(target = page, email = adminEmail, password = adminPassword) {
  await target.goto(`${base}/admin/login`);
  await target.getByLabel('邮箱', { exact: true }).fill(email);
  await target.getByLabel('密码', { exact: true }).fill(password);
  await target.getByRole('button', { name: '登录管理台', exact: true }).click();
}
const search = () => page.getByRole('searchbox', { name: '搜索账户（邮箱或称呼）' });
const row = () => page.locator('.admin-table tbody tr').filter({ hasText: testAccount.account.email });
async function waitReady() {
  await search().waitFor();
  await until(async () => !(await page.getByRole('button', { name: '刷新', exact: true }).isDisabled()), '管理台加载未结束');
}
async function refresh() {
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await waitReady();
}
async function accountState() {
  const response = await api('GET', '/admin/accounts');
  assert.equal(response.status(), 200);
  return (await response.json()).accounts.find(item => item.id === testAccount.account.id);
}
async function requestChange(action, confirm = true) {
  await row().getByRole('button', { name: action, exact: true }).click();
  const dialog = page.getByRole('alertdialog');
  await dialog.waitFor();
  assert.ok((await dialog.innerText()).includes(testAccount.account.email));
  await dialog.getByRole('button', { name: confirm ? '确认' : '取消', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
}
async function capture(name) {
  await page.evaluate(() => document.fonts.ready);
  if (process.env.ZHOUXU_REVIEW_PRIVATE === '1') await page.screenshot({ path: path.join(output, `private-${name}`) });
  await page.screenshot({ path: path.join(output, name),
    // Review layout without publishing real account identities in evidence.
    mask: [page.locator('.admin-account-cell > span:last-child'), page.locator('.admin-identity'), page.locator('.admin-audit-people')],
    maskColor: '#25313f',
  });
}
async function assertFits() {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, '页面出现横向溢出');
}

try {
  await check('管理员真实登录与数据保护', async () => {
    await page.goto(`${base}/admin/login`);
    await page.getByRole('button', { name: '登录管理台', exact: true }).click();
    assert.equal(await page.getByLabel('邮箱', { exact: true }).getAttribute('aria-invalid'), 'true');
    await page.getByLabel('密码', { exact: true }).fill('input-visibility-check');
    await page.getByRole('button', { name: '显示密码', exact: true }).click();
    assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('type'), 'text');
    await page.getByRole('button', { name: '隐藏密码', exact: true }).click();
    assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('type'), 'password');
    const authResponse = page.waitForResponse(response => response.url().endsWith('/api/auth/admin-login') && response.request().method() === 'POST');
    await login();
    const response = await authResponse;
    assert.equal(response.status(), 200);
    const session = await response.json();
    adminToken = session.session.token;
    assert.equal(session.account.role, 'admin');
    await page.waitForURL(`${base}/admin`);
    await page.getByRole('heading', { name: '账户与记录' }).waitFor();
    for (const endpoint of ['/admin/overview', '/admin/accounts', '/admin/audit']) {
      const result = await api('GET', endpoint);
      assert.equal(result.status(), 200);
      const keys = [];
      const visit = value => {
        if (value && typeof value === 'object') for (const [key, child] of Object.entries(value)) { keys.push(key); visit(child); }
      };
      visit(await result.json());
      assert.equal(keys.some(key => /password|token|secret|stack|raw_request/i.test(key)), false, '业务接口泄漏技术或凭证字段');
    }
    assert.equal((await api('GET', '/admin/overview', undefined, null)).status(), 401);
  });
  await check('人员搜索筛选分页与自己的权限保护', async () => {
    await waitReady();
    assert.equal(await page.locator('.admin-table tbody tr').count(), 20, '人员应每页20条');
    const firstEmail = await page.locator('.admin-table tbody tr').first().locator('.admin-account-cell').innerText();
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    assert.notEqual(await page.locator('.admin-table tbody tr').first().locator('.admin-account-cell').innerText(), firstEmail);
    await page.getByRole('button', { name: '上一页', exact: true }).click();
    assert.equal(await page.locator('.admin-table tbody tr').first().locator('.admin-account-cell').innerText(), firstEmail);
    await search().fill(adminEmail);
    await until(async () => await page.locator('.admin-table tbody tr').count() === 1, '邮箱搜索没有收敛');
    assert.equal(await page.locator('.admin-table tbody tr').getByRole('button').count(), 0, '不应允许修改自己');
    await search().fill('不存在的账户-acceptance');
    await page.getByText('没有匹配的账户', { exact: false }).waitFor();
    await search().fill('');
    await page.getByRole('combobox', { name: '角色筛选' }).click();
    await page.getByRole('option', { name: '管理员', exact: true }).click();
    const roles = await page.locator('.admin-table tbody tr td:nth-child(2)').allTextContents();
    assert.ok(roles.length > 0 && roles.every(value => value === '管理员'));
    await page.getByRole('combobox', { name: '角色筛选' }).click();
    await page.getByRole('option', { name: '全部角色', exact: true }).click();
  });
  await check('新测试账户的停用启用角色调整和业务记录', async () => {
    const email = `admin-acceptance-${Date.now()}@example.com`;
    testPassword = `Test-${randomUUID()}`;
    const registration = await api('POST', '/auth/register', { email, password: testPassword, display_name: '管理员页面验收' }, null);
    assert.equal(registration.status(), 201);
    testAccount = await registration.json();
    const forbidden = await api('GET', '/admin/overview', undefined, testAccount.session.token);
    assert.equal(forbidden.status(), 403);
    const ordinaryContext = await browser.newContext();
    try {
      const ordinaryPage = await ordinaryContext.newPage();
      await login(ordinaryPage, email, testPassword);
      await ordinaryPage.getByRole('alert').waitFor();
      assert.ok((await ordinaryPage.getByRole('alert').innerText()).includes('管理员'));
      assert.ok(ordinaryPage.url().endsWith('/admin/login'));
    } finally { await ordinaryContext.close(); }
    await refresh();
    await search().fill(email);
    await requestChange('停用', false);
    assert.equal((await accountState()).status, 'active');
    await row().getByRole('button', { name: '停用', exact: true }).click();
    await page.keyboard.press('Escape');
    await page.getByRole('alertdialog').waitFor({ state: 'hidden' });
    assert.equal(await row().getByRole('button', { name: '停用', exact: true }).evaluate(element => element === document.activeElement), true, '确认窗关闭后焦点应回到原操作');
    await requestChange('停用');
    await until(async () => (await accountState()).status === 'disabled', '停用未保存');
    assert.equal((await api('GET', '/auth/me', undefined, testAccount.session.token)).status(), 401);
    assert.equal((await api('POST', '/auth/login', { email, password: testPassword }, null)).status(), 401);
    await waitReady();
    await page.getByRole('combobox', { name: '状态筛选' }).click();
    await page.getByRole('option', { name: '正常', exact: true }).click();
    await page.getByText('没有匹配的账户', { exact: false }).waitFor();
    await page.getByRole('combobox', { name: '状态筛选' }).click();
    await page.getByRole('option', { name: '已停用', exact: true }).click();
    assert.equal(await row().count(), 1);
    await page.getByRole('combobox', { name: '状态筛选' }).click();
    await page.getByRole('option', { name: '全部状态', exact: true }).click();
    await page.getByRole('tab', { name: '业务记录', exact: true }).click();
    await page.getByText('正常 → 已停用', { exact: true }).first().waitFor();
    const log = page.locator('.admin-audit-row').filter({ hasText: email }).first();
    assert.ok((await log.innerText()).includes(adminEmail), '业务记录未显示操作人');
    await page.getByRole('tab', { name: '人员', exact: true }).click();
    await requestChange('启用');
    await until(async () => (await accountState()).status === 'active', '启用未保存');
    await waitReady();
    await requestChange('设为管理员');
    await until(async () => (await accountState()).role === 'admin', '角色未保存');
    await waitReady();
    await requestChange('改为普通用户');
    await until(async () => (await accountState()).role === 'user', '角色恢复未保存');
    await page.reload();
    await waitReady();
    await search().fill(email);
    assert.ok((await row().innerText()).includes('普通用户'));
  });
  await check('断网重试和修改成功后刷新失败不重复执行', async () => {
    const failReads = async route => {
      if (route.request().method() === 'GET') await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'unavailable' }) });
      else await route.continue();
    };
    await page.route('**/api/admin/**', failReads);
    await page.reload();
    await page.getByRole('alert').waitFor();
    assert.equal(await page.locator('.admin-metrics').count(), 0, '首次失败不应展示假0');
    assert.equal(await search().count(), 0, '首次失败不应展示假空表');
    await capture('admin-load-error.png');
    await page.unroute('**/api/admin/**', failReads);
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await waitReady();
    await search().fill(testAccount.account.email);
    const rejectPatch = async route => {
      if (route.request().method() === 'PATCH') await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'unavailable' }) });
      else await route.continue();
    };
    await page.route('**/api/admin/**', rejectPatch);
    await row().getByRole('button', { name: '停用', exact: true }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: '确认', exact: true }).click();
    await page.getByRole('alertdialog').getByRole('alert').waitFor();
    assert.equal((await accountState()).status, 'active', '失败的修改不应显示成成功');
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await page.unroute('**/api/admin/**', rejectPatch);
    let patchCount = 0;
    const countPatch = request => { if (request.method() === 'PATCH' && request.url().endsWith(`/admin/accounts/${testAccount.account.id}`)) patchCount++; };
    page.on('request', countPatch);
    await page.route('**/api/admin/**', failReads);
    await requestChange('停用');
    await page.getByRole('alert').waitFor();
    assert.ok((await page.getByRole('alert').innerText()).includes('刷新'), '修改回执和刷新失败应分开说明');
    assert.ok((await row().innerText()).includes('已停用'), '应保留服务端确认的新状态');
    assert.equal(patchCount, 1, '一次确认不应重复提交');
    await page.unroute('**/api/admin/**', failReads);
    await refresh();
    await requestChange('启用');
    await waitReady();
    page.off('request', countPatch);
    assert.equal((await accountState()).status, 'active');
    const expired = route => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'authentication_required' }) });
    await page.route('**/api/admin/**', expired);
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.waitForURL(`${base}/admin/login`);
    await page.unroute('**/api/admin/**', expired);
    await login();
    await page.waitForURL(`${base}/admin`);
    await waitReady();
  });
  await check('桌面手机排版弹窗与键盘操作', async () => {
    await assertFits();
    const tabs = await page.getByRole('tablist').boundingBox();
    const firstTab = await page.getByRole('tab', { name: '人员', exact: true }).boundingBox();
    assert.ok(tabs && firstTab && Math.abs(tabs.x - firstTab.x) < 12, '切换标签应与内容左对齐');
    assert.equal(await page.getByRole('tablist').evaluate(element => getComputedStyle(element).backgroundColor), 'rgba(0, 0, 0, 0)', '切换栏不应被组件默认灰底覆盖');
    await capture('admin-desktop.png');
    await search().fill(testAccount.account.email);
    await row().getByRole('button', { name: '停用', exact: true }).click();
    await page.getByRole('alertdialog').waitFor();
    assert.ok((await page.getByRole('alertdialog').boundingBox()).width <= 461, '确认窗尺寸应服从页面设计');
    assert.notEqual(await page.getByRole('alertdialog').evaluate(element => getComputedStyle(element).color), 'rgb(0, 0, 0)');
    await capture('admin-dialog.png');
    await page.getByRole('button', { name: '取消', exact: true }).click();
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await assertFits();
      const mobileRow = page.locator('.admin-mobile-account').filter({ hasText: testAccount.account.email });
      await mobileRow.waitFor();
      const stop = mobileRow.getByRole('button', { name: '停用', exact: true });
      const box = await stop.boundingBox();
      assert.ok(box && box.height >= 44 && box.x >= 0 && box.x + box.width <= width, '手机操作必须可见且便于点击');
      await stop.click();
      const dialogBox = await page.getByRole('alertdialog').boundingBox();
      assert.ok(dialogBox && dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= width, '手机确认窗超出屏幕');
      await page.getByRole('button', { name: '取消', exact: true }).click();
      if (width === 390) await capture('admin-mobile.png');
    }
    await page.getByRole('tab', { name: '业务记录', exact: true }).click();
    await assertFits();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await capture('admin-audit.png');
    assert.deepEqual(pageErrors, [], '页面出现脚本异常');
    const restored = await accountState();
    testAccountRestored = restored.status === 'active' && restored.role === 'user';
    assert.ok(testAccountRestored, '验收账户应恢复为正常普通用户');
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await page.waitForURL(`${base}/admin/login`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(output, 'admin-login.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await assertFits();
    await page.screenshot({ path: path.join(output, 'admin-login-mobile.png') });
  });
  console.log(JSON.stringify({ passed: results.length, testAccountRestored, output }));
} catch (error) {
  const secrets = [adminEmail, adminPassword, adminToken, testPassword, testAccount?.session.token].filter(Boolean);
  let message = String(error.message);
  for (const secret of secrets) message = message.replaceAll(secret, '[已隐藏]');
  console.error(`未通过：${step}\n${message}`);
  if (testAccount) console.error(`本轮验收账户 ID：${testAccount.account.id}`);
  process.exitCode = 1;
} finally {
  if (testAccount && !testAccountRestored) {
    try {
      const restored = await api('PATCH', `/admin/accounts/${testAccount.account.id}`, { status: 'active', role: 'user' });
      assert.equal(restored.status(), 200);
    } catch {
      console.error('验收账户恢复失败；请根据上方账户 ID 排查。');
      process.exitCode = 1;
    }
  }
  await browser.close();
}
