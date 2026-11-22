import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const tempRoot = resolve('.ai-tmp');
const output = resolve(process.argv[2] || resolve(tempRoot, `paper-links-${randomUUID()}`));
const outputRelative = relative(tempRoot, output);
assert(outputRelative && !isAbsolute(outputRelative) && outputRelative !== '..' && !outputRelative.startsWith('..' + sep),
  '浏览器验收输出必须位于本项目 .ai-tmp 内的独立任务目录');
await mkdir(output, { recursive: true });
const sessionPath = resolve(output, 'test-session.json');
let session;
try { session = JSON.parse(await readFile(sessionPath, 'utf8')); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const credentials = session?.credentials ?? { email: `paper-links-${randomUUID()}@example.test`, password: randomUUID(), display_name: '论文入口验收' };
let token;
async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST', signal: AbortSignal.timeout(120000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = await response.json();
  assert(response.ok, `${path}: ${response.status} ${JSON.stringify(payload)}`);
  return payload;
}
await api('/healthz');
token = (await api(session ? '/auth/login' : '/auth/register', credentials)).session.token;
session ??= { credentials };
await writeFile(sessionPath, JSON.stringify(session));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
page.setDefaultTimeout(30000);
const checks = [];
async function openReader(source) {
  await page.locator('.ws-source-text').waitFor();
  assert.equal(await page.locator('.ws-source-title').innerText(), source.title);
  await page.getByRole('button', { name: '查看原文', exact: true }).click();
  await page.locator('.ws-paper-page canvas').waitFor();
  await page.waitForFunction(() => document.querySelector('.ws-paper-page')?.getAttribute('aria-busy') === 'false');
  assert.equal(await page.locator('input[aria-label="论文页码"]').inputValue(), String(source.original.page ?? 1));
  assert.equal(await page.locator('.ws-paper-page [role=alert]').count(), 0);
}
function paperCitations(text) {
  const citations = [...text.matchAll(/\[([^\]]+)\]\(((?:\/api)?\/workspace\/sources\/(paper-[A-Za-z0-9._-]+))\)/g)]
    .map(([, label, url, sourceId]) => ({ label, url, source_id: sourceId }));
  const sourceLinks = [...text.matchAll(/\[[^\]]+\]\(([^)\s]+)\)/g)]
    .map(([, url]) => url).filter(url => url.includes('/workspace/sources/'));
  assert.equal(citations.length, sourceLinks.length, '正文引用地址没有被替换成显示文案或残缺编号');
  return citations;
}
async function clickAnswerCitation(citation, source, expectFetch = true) {
  const button = page.locator('.ws-answer').last().locator('.ws-citation-link')
    .filter({ hasText: citation.label }).first();
  await button.waitFor();
  const sourceResponse = expectFetch ? page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/workspace/sources/' + encodeURIComponent(citation.source_id)
    && response.request().method() === 'GET') : null;
  await button.click();
  if (sourceResponse) {
    const response = await sourceResponse;
    assert.equal(response.status(), 200, '正文实际引用的来源地址可打开');
    assert.equal((await response.json()).source_id, citation.source_id, '点击正文引用打开同一来源');
  }
  await openReader(source);
}
async function finished(cid, rid) {
  const deadline = Date.now() + 240000;
  let nextNotice = 0;
  while (Date.now() < deadline) {
    const run = (await api(`/workspace/conversations/${cid}/snapshot`)).runs.find(item => item.run_id === rid);
    if (run?.status === 'succeeded') return run;
    assert(!['failed', 'cancelled', 'paused', 'waiting_user'].includes(run?.status), `论文任务失败: ${run?.status}`);
    if (Date.now() >= nextNotice) { console.log(JSON.stringify({ waiting: '论文引用', status: run?.status, source_count: run?.sources.length })); nextNotice = Date.now() + 30000; }
    await new Promise(done => setTimeout(done, 1000));
  }
  throw new Error('论文引用任务超过四分钟');
}
try {
  await page.goto(origin + '/login');
  await page.locator('input[name=email]').fill(credentials.email);
  await page.locator('input[name=password]').fill(credentials.password);
  await page.getByRole('button', { name: '登录舟序', exact: true }).click();
  await page.waitForURL(url => url.pathname.startsWith('/workspace'));
  await page.goto(origin + '/workspace/research');
  await page.getByRole('button', { name: '论文资料', exact: true }).click();
  await page.getByRole('textbox', { name: '海事检索关键词' }).fill('FCR 单证 交单义务');
  const searchResponse = page.waitForResponse(response => response.url().endsWith('/api/maritime/search') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '检索', exact: true }).click();
  const search = await (await searchResponse).json();
  assert(search.items.length, '独立检索页找到论文');
  const first = search.items[0];
  assert(first.source_id, `论文检索结果缺少原文入口: ${JSON.stringify(first)}`);
  await page.locator('.md-result').first().getByRole('button', { name: '查看原文' }).click();
  const source = await api('/workspace/sources/' + encodeURIComponent(first.source_id));
  assert(source.original?.page > 0, '论文来源含真实页码');
  await openReader(source);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  assert.equal(await page.locator('input[aria-label="论文页码"]').inputValue(), String(source.original.page + 1));
  await page.getByRole('button', { name: '上一页', exact: true }).click();
  await page.getByRole('button', { name: '放大', exact: true }).click();
  assert.equal(await page.locator('.ws-paper-zoom span').innerText(), '125%');
  await page.getByRole('button', { name: '缩小', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.ws-paper-page')?.getAttribute('aria-busy') === 'false');
  checks.push('独立检索→论文摘录→准确PDF页码，翻页和缩放可用');
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载原文', exact: true }).click();
  const download = await downloadEvent;
  const bytes = await readFile(await download.path());
  assert.equal(createHash('sha256').update(bytes).digest('hex'), source.original.sha256);
  const originalUrl = origin + '/api' + source.original.content_url;
  const unauthenticated = await fetch(originalUrl);
  assert([401, 403].includes(unauthenticated.status), '未登录无法读取原文');
  const wrongVersion = new URL(originalUrl);
  wrongVersion.searchParams.set('sha256', '0'.repeat(64));
  assert.equal((await fetch(wrongVersion, { headers: { Authorization: `Bearer ${token}` } })).status, 409);
  const canonical = await api('/workspace/sources/paper-document-' + source.original.document_id);
  assert.equal(canonical.original.sha256, source.original.sha256);
  assert.equal(canonical.original.page, null, '旧卡迁移到整篇论文，不编造旧卡页码');
  checks.push('预览与下载为同一PDF；旧卡整篇入口可读；未登录和错误版本被拒绝');
  await page.screenshot({ path: resolve(output, 'paper-reader.png') });
  await page.getByRole('button', { name: '关闭', exact: true }).click();

  if (!session.cid) {
    session.cid = (await api('/workspace/conversations', { title: '论文全入口验收', mode: 'work' })).conversation.conversation_id;
    await writeFile(sessionPath, JSON.stringify(session));
  }
  const cid = session.cid;
  await page.goto(`${origin}/workspace/chat/${cid}`);
  if (!session.rid) {
    await page.locator('.ws-model-trigger').click();
    await page.getByRole('menuitem', { name: 'DeepSeek Flash', exact: true }).click();
    const thinking = page.getByRole('button', { name: /^思考档位：/ });
    if (await thinking.getAttribute('aria-label') !== '思考档位：关闭') {
      await thinking.click();
      await page.getByRole('menuitemradio', { name: '关闭', exact: true }).click();
    }
    await page.locator('textarea').fill('请实际调用 information_card_search，检索“FCR 单证 交单义务”，只取 1 条，再调用 information_card_read 读取这张卡的完整内容。只用两三句话引用其中一小段原文，写明真实作者、论文题名，并用完整卡返回的 citation_url 给出该论文的 Markdown 引用链接。不要再检索其他库，不写论文，不生成文件。');
    const submitted = page.waitForResponse(response => response.url().endsWith(`/conversations/${cid}/runs`) && response.request().method() === 'POST');
    await page.getByRole('button', { name: '发送问题', exact: true }).click();
    const response = await submitted;
    assert.equal(response.status(), 202);
    session.rid = (await response.json()).run.run_id;
    await writeFile(sessionPath, JSON.stringify(session));
  }
  const run = await finished(cid, session.rid);
  assert.equal(run.payload.model_snapshot.provider, 'deepseek');
  assert(run.sources.some(item => item.kind === 'paper'), '真实DeepSeek检索结果有论文');
  const citations = paperCitations(run.output);
  assert(citations.length, '真实回答给出完整、可点击的论文引用');
  assert(citations.every(citation => run.sources.some(source => source.kind === 'paper' && source.source_id === citation.source_id)),
    '每个正文论文链接均指向本次返回的真实来源');
  const citation = citations[0];
  const paper = run.sources.find(item => item.kind === 'paper' && item.source_id === citation.source_id);
  const answerSource = await api('/workspace/sources/' + encodeURIComponent(citation.source_id));
  assert.equal(answerSource.source_id, citation.source_id, '按正文实际source id读取原文');
  const history = await api(`/workspace/conversations/${cid}`);
  const historyAnswers = history.messages.filter(message => message.role === 'assistant' && message.run_id === run.run_id);
  assert.equal(historyAnswers.length, 1, '历史中仅保留该任务的一条回答');
  assert.equal(historyAnswers[0].content, run.output, '历史回答与最终回答一致');
  assert.deepEqual(paperCitations(historyAnswers[0].content), citations, '历史回答保留同一组实际引用');
  // Test the body link first: opening a sidebar card must not stand in for this.
  await clickAnswerCitation(citation, answerSource);
  checks.push('完整卡引用→正文实际source id→对应原文，最终回答与历史链接一致');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '关闭资料面板', exact: true }).click();
  await page.locator('.ws-source-summary').last().click();
  await page.locator('.ws-source-filters button').filter({ hasText: '论文资料' }).click();
  const sourceSearch = page.getByRole('searchbox', { name: '搜索本次资料' });
  await sourceSearch.fill(paper.authors || paper.title);
  await page.locator('.ws-research-list button').first().click();
  await openReader(answerSource);
  checks.push('真实DeepSeek回答→资料摘要→论文筛选/搜索→同一篇原文');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '关闭资料面板', exact: true }).click();
  await page.reload();
  await clickAnswerCitation(citation, answerSource);
  const refreshedHistory = await api(`/workspace/conversations/${cid}`);
  assert.equal(refreshedHistory.messages.find(message => message.role === 'assistant' && message.run_id === run.run_id).content,
    run.output, '刷新后历史正文没有替换或破坏引用');
  checks.push('完成后刷新，直接点击历史正文引用仍打开同一source id与原文');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '关闭资料面板', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await clickAnswerCitation(citation, answerSource, false);
  const toolbar = await page.locator('.ws-paper-toolbar').boundingBox();
  assert(toolbar.x >= 0 && toolbar.x + toolbar.width <= 391, '窄屏工具栏在可见范围内');
  assert(await page.getByRole('button', { name: '下载原文', exact: true }).isVisible());
  await page.screenshot({ path: resolve(output, 'paper-mobile.png') });
  checks.push('手机宽度下，回答引用可打开，页码/缩放/下载可操作');
  assert.equal(pageErrors.length, 0, pageErrors.join('; '));
  const report = { checks, conversation_id: cid, run_id: run.run_id, citations,
    paper: { source_id: answerSource.source_id, title: answerSource.title, page: answerSource.original.page },
    page_errors: pageErrors };
  await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (error) {
  await page.screenshot({ path: resolve(output, 'failure.png') });
  await writeFile(resolve(output, 'failure.txt'), await page.locator('body').innerText());
  throw error;
} finally {
  await browser.close();
}
