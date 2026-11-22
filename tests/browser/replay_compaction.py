"""Real-model replay of a captured compaction input, without mutating a Run.

This supplements, rather than replaces, the real browser long-task journey.
"""
import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from backend.config.settings import load_settings
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.factory import open_model


class Metrics:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


async def replay(args):
    with sqlite3.connect(args.run_store.resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT body FROM run_sessions WHERE id=?', (args.conversation,)).fetchone()
    if row is None:
        raise ValueError('conversation_not_found')
    run = next(r for r in json.loads(row[0])['snapshot']['runs'] if r['run_id'] == args.run)
    state = run['execution_state']
    record_ids = {r['record_id'] for r in state['records']}
    assert set(state['compacted_record_ids']) <= record_ids, 'compacted originals lost'
    summary = next(r['body'] for r in state['records'] if 'covered_record_ids' in r.get('body', {}))
    reference = json.loads(summary['text'])
    resource = next(r for r in state['context_resources'] if r['material_id'] == reference['material_id'])
    originals = json.loads(resource['text'])
    assert {r['id'] for r in originals} == set(summary['covered_record_ids'])
    body = {'current_task': run['payload']['text'],
            'summary_scope': {'covered_record_ids': [r['id'] for r in originals],
                              'other_records_remain_in_working_context': True},
            'records_to_summarize': originals}
    run_id = 'step1-compaction-replay'
    request = ModuleRequest(run_id=run_id, call_id='saved-history', input=ReasoningInput(
        purpose='compaction', perceived=PerceivedContext(run_id=run_id,
            items=[ContextItem(id='summary-input', type='message', source='memory', role='user',
                               kind='history.data', body=body)], assembly=ContextAssembly())))
    metrics = Metrics()
    async with open_model(settings=load_settings(), events=metrics, role='main') as model:
        result = await model.decide(request)
    assert result.kind == 'answer' and result.answer and not result.incomplete, (
        f'compaction incomplete: finish={result.finish_reason}, chars={len(result.answer or "")}')
    assert len(result.answer) < len(resource['text']), 'summary expanded input'
    for required in args.require:
        assert required in result.answer, f'key constraint missing: {required}'
    print(json.dumps({'passed': 'real-model saved-input compaction', 'originals': len(originals),
                      'original_bytes': len(resource['text'].encode()),
                      'summary_chars': len(result.answer), 'summary': result.answer}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-store', type=Path, default=Path('/app/backend/.runtime/runs.sqlite3'))
    parser.add_argument('--conversation', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--require', action='append', default=[])
    asyncio.run(replay(parser.parse_args()))
