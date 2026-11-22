// Real HTTP regression for the reported GPT 6.1/6.2 work-mode research issue.
// It intentionally uses one short law lookup per model and records only the
// public run snapshot, stages, and source identities.
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const origin = process.env.ZHOUXU_TEST_URL || 'http://127.0.0.1:18080';
const output = resolve(process.env.ZHOUXU_TEST_OUTPUT || '.ai-tmp/gpt61-62-research');
await mkdir(output, { recursive: true });
const credentials = {
  email: `gpt-research-${randomUUID()}@example.test`,
  password: randomUUID(),
  display_name: 'GPT 6.1/6.2 资料回归',
};
let token;

async function api(path, body) {
  const response = await fetch(origin + '/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    signal: AbortSignal.timeout(30000),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const text = await response.text();
  let payload = {};
  try { payload = JSON.parse(text); } catch {}
  return { status: response.status, payload };
}

function check(condition, label) {
  if (!condition) throw new Error(label);
  console.log(JSON.stringify({ passed: label }));
}

const report = {
  query: '请查找现行《海商法》第22条，并给出资料来源。只需回答该条当前内容和来源。',
  models: {},
};
check((await api('/healthz')).status === 200, 'API 已就绪');
const registration = await api('/auth/register', credentials);
check(registration.status === 201, '已创建本次临时验收账号');
token = registration.payload.session.token;
const catalog = (await api('/workspace/models')).payload;

for (const modelId of ['gpt-6.1-sol', 'gpt-6.2-sol']) {
  const model = catalog.groups.flatMap(group => group.models).find(item => item.model_id === modelId);
  check(Boolean(model), `${modelId} 出现在模型目录`);
  check(model.available && model.capabilities.tools === 'confirmed', `${modelId} 已通过工具能力验证`);
  check(JSON.stringify(model.thinking_modes) === JSON.stringify(['low', 'medium', 'high', 'xhigh', 'max']), `${modelId} 显示五档已实测思考`);
  report.models[modelId] = {
    catalog: {
      protocol: model.protocol,
      tools: model.capabilities.tools,
      streaming: model.capabilities.streaming,
      thinking_modes: model.thinking_modes,
    },
  };
}

async function latest(conversationId) {
  return (await api(`/workspace/conversations/${conversationId}/snapshot`)).payload.runs.at(-1);
}

for (const modelId of ['gpt-6.1-sol', 'gpt-6.2-sol']) {
  const conversation = await api('/workspace/conversations', { title: `资料回归 ${modelId}`, mode: 'work' });
  check(conversation.status === 201, `${modelId} 工作对话已创建`);
  const conversationId = conversation.payload.conversation.conversation_id;
  const submitted = await api(`/workspace/conversations/${conversationId}/runs`, {
    conversation_id: conversationId,
    client_request_id: randomUUID(),
    text: report.query,
    model_profile_id: `relay:${modelId}`,
    thinking_mode: 'low',
  });
  check(submitted.status === 202, `${modelId} 工作请求已提交`);
  const runId = submitted.payload.run.run_id;
  const deadline = Date.now() + 180000;
  let run;
  while (Date.now() < deadline) {
    run = await latest(conversationId);
    if (run?.run_id === runId && ['succeeded', 'failed', 'cancelled', 'paused', 'waiting_user'].includes(run.status)) break;
    await new Promise(resolvePromise => setTimeout(resolvePromise, 1000));
  }
  check(run?.run_id === runId, `${modelId} 运行记录可见`);
  check(run.status === 'succeeded', `${modelId} 工作请求完成`);
  check(run.sources.length > 0, `${modelId} 返回了资料来源`);
  check((run.stages || []).some(stage => stage.id === 'research'), `${modelId} 经过查找资料阶段`);
  report.models[modelId].run = {
    conversation_id: conversationId,
    run_id: runId,
    status: run.status,
    source_ids: run.sources.map(source => source.source_id),
    stages: run.stages.map(stage => ({ id: stage.id, label: stage.label, details: stage.details })),
    output_preview: (run.output || '').slice(0, 500),
  };
}

await writeFile(resolve(output, 'result.json'), JSON.stringify(report, null, 2));
