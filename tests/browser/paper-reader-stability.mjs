import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.argv[2] || '.ai-tmp/paper-reader-stability-20261121');
await mkdir(output, { recursive: true });
const credentials = { email: `paper-reader-${randomUUID()}@example.test`, password: randomUUID(), display_name: '论文阅读器验收' };
const chrome = process.env.ZHOUXU_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';

async function api(path, body, token) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json();
  assert(response.ok, `${path}: ${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const auth = await api('/auth/register', credentials);
const token = auth.session.token;
const browser = await chromium.launch({ headless: true, executablePath: chrome });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
page.setDefaultTimeout(60000);

async function measure() {
  return page.evaluate(() => {
    const paper = document.querySelector('.ws-paper-page');
    const canvas = paper?.querySelector('canvas');
    const loading = paper?.querySelector('[role="status"]');
    if (!paper || !canvas) return null;
    const paperRect = paper.getBoundingClientRect();
    const canvasRect = canvas.getBoundingClientRect();
    return {
      busy: paper.getAttribute('aria-busy'),
      loading: Boolean(loading),
      paperTop: paperRect.top,
      paperHeight: paperRect.height,
      canvasTop: canvasRect.top,
      canvasHeight: canvasRect.height,
      canvasWidth: canvasRect.width,
      canvasPixels: [canvas.width, canvas.height],
    };
  });
}

async function sampleStable(label, duration = 1200) {
  const samples = [];
  const started = Date.now();
  while (Date.now() - started < duration) {
    const value = await measure();
    if (value) samples.push({ at: Date.now() - started, ...value });
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  return { label, samples, first: samples[0], last: samples.at(-1) };
}

const report = { checks: [], pageErrors };
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(origin + '/workspace/research');
  await page.getByRole('button', { name: '论文资料', exact: true }).click();
  await page.getByRole('textbox', { name: '海事检索关键词' }).fill('关于海事法院受理案件范围的若干规定');
  const searchResponse = page.waitForResponse(response => response.url().endsWith('/api/maritime/search') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '检索', exact: true }).click();
  const search = await (await searchResponse).json();
  assert(search.items.length, '论文资料检索没有结果');
  report.search_title = search.items[0].title;
  await page.locator('.md-result').first().getByRole('button', { name: '查看原文', exact: true }).click();
  await page.locator('.ws-source-text').waitFor();
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await page.locator('.ws-paper-page canvas').waitFor();
  await page.locator('.ws-paper-page [role="status"]').waitFor({ state: 'visible' });
  const duringInitial = await measure();
  await page.waitForFunction(() => document.querySelector('.ws-paper-page')?.getAttribute('aria-busy') === 'false');
  const afterInitial = await measure();
  assert(Math.abs(afterInitial.canvasTop - duringInitial.canvasTop) <= 1, `首次读取导致画布跳动: ${JSON.stringify({ duringInitial, afterInitial })}`);
  assert.equal(afterInitial.loading, false, '首次绘制完成后仍显示读取提示');
  report.checks.push('首次打开原文时，读取提示不推动已显示的页面');

  const initialStable = await sampleStable('initial', 1200);
  assert(initialStable.samples.length > 5, '首次稳定采样不足');
  assert(new Set(initialStable.samples.map(item => `${item.canvasTop}:${item.canvasWidth}:${item.canvasHeight}`)).size === 1, '首次打开后画布尺寸仍在变化');

  const next = page.getByRole('button', { name: '下一页', exact: true });
  if (await next.isEnabled()) {
    const beforePage = await measure();
    await next.click();
    await page.waitForFunction(() => document.querySelector('.ws-paper-page')?.getAttribute('aria-busy') === 'false');
    const afterPage = await measure();
    assert(Math.abs(afterPage.canvasTop - beforePage.canvasTop) <= 1, `翻页导致画布跳动: ${JSON.stringify({ beforePage, afterPage })}`);
    assert.equal(afterPage.loading, false, '翻页完成后重新显示读取提示');
    report.checks.push('翻页不改变阅读区域位置');
  }

  const beforeZoom = await measure();
  await page.getByRole('button', { name: '放大', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.ws-paper-page')?.getAttribute('aria-busy') === 'false');
  const afterZoom = await measure();
  assert(Math.abs(afterZoom.paperTop - beforeZoom.paperTop) <= 1, `缩放导致阅读区域跳动: ${JSON.stringify({ beforeZoom, afterZoom })}`);
  assert.equal(afterZoom.loading, false, '缩放完成后重新显示读取提示');
  report.checks.push('缩放不让阅读器整体闪动');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobile = await sampleStable('mobile', 1000);
  assert(mobile.last && mobile.last.canvasWidth > 0, '手机宽度下 PDF 页面消失');
  assert.equal(mobile.last.loading, false, '手机宽度下仍显示读取提示');
  report.checks.push('手机宽度下页面稳定');
  assert.equal(pageErrors.length, 0, pageErrors.join('; '));
  report.page_errors = pageErrors;
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (error) {
  report.error = String(error);
  report.page_errors = pageErrors;
  await page.screenshot({ path: resolve(output, 'failure.png') });
  await writeFile(resolve(output, 'failure.txt'), await page.locator('body').innerText());
  await writeFile(resolve(output, 'failure.json'), JSON.stringify(report, null, 2));
  throw error;
} finally {
  await browser.close();
}
