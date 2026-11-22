import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18180';
const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    await page.goto(`${base}/admin/login`);
    await page.getByLabel('邮箱', { exact: true }).fill('invalid-login-layout@example.com');
    await page.getByLabel('密码', { exact: true }).fill('wrong-password');
    await page.getByRole('button', { name: '登录管理台', exact: true }).click();
    const error = page.getByRole('alert');
    await error.waitFor();
    const button = page.getByRole('button', { name: '登录管理台', exact: true });
    const errorBox = await error.boundingBox();
    const buttonBox = await button.boundingBox();
    assert.ok(errorBox && buttonBox, '登录错误和按钮都应该可见');
    assert.ok(errorBox.y >= buttonBox.y + buttonBox.height + 8, `登录错误提示应在按钮下方留出空间（error=${JSON.stringify(errorBox)}, button=${JSON.stringify(buttonBox)})`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, '登录页不应横向溢出');
    await page.close();
  }
  console.log('登录错误布局测试通过');
} finally { await browser.close(); }
