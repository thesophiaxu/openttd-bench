import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, readFile, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import ts from 'typescript';

async function load(file) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
}
const { parseAtif, traceToMessages } = await load('../lib/trace.ts');
const { listRuns, readRun } = await load('../lib/server-runs.ts');
const doc = {
  schema_version: 'ATIF-v1.8', session_id: 'root', trajectory_id: 'root', agent: { name: 'test', version: '1' },
  steps: [
    { step_id: 1, source: 'user', message: [{ type: 'text', text: 'hello' }] },
    { step_id: 2, source: 'agent', message: '', tool_calls: [{ tool_call_id: 'x', function_name: 'exec', arguments: { raw: 'program' } }],
      observation: { results: [{ source_call_id: 'x', content: 'first' }, { source_call_id: 'x', content: 'second' }] } },
    { step_id: 3, source: 'agent', message: 'copied', is_copied_context: true },
  ],
  extra: { episode: { id: 'episode' }, source: { records: ['private archival duplicate'] } },
  subagent_trajectories: [{ schema_version: 'ATIF-v1.8', trajectory_id: 'child', agent: { name: 'child', version: '1' },
    steps: [{ step_id: 1, source: 'agent', message: 'child output' }], extra: { source: { records: ['child raw'] } } }],
};

test('ATIF only; subagents independently selectable; copied context optional', () => {
  assert.throws(() => parseAtif(JSON.stringify({ id: 'old', traces: [] })), /ATIF/);
  const run = parseAtif(JSON.stringify(doc));
  assert.equal(run.episodes.length, 1);
  assert.equal(run.episodes[0].traces.length, 2);
  assert.equal(run.episodes[0].traces[1].extra.parent_trajectory_id, 'root');
  const messages = traceToMessages(doc);
  assert.equal(messages.length, 2);
  assert.equal(messages[0].parts[0].text, 'hello');
  assert.equal(messages[1].parts[0].output, 'first\nsecond');
  assert.equal(traceToMessages(doc, true).length, 3);
});

test('discovery ignores raw/archived files; reads ATIF; rejects traversal and symlinks', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'atif-viewer-'));
  const old = process.env.OUTPUTS_DIR;
  process.env.OUTPUTS_DIR = root;
  try {
    const filename = `${'a'.repeat(64)}.atif.json`;
    const index = { format: 'openttd-atif-index-v1', trajectories: [{ file: filename, episode_id: 'e', size: 1 }] };
    for (const run of ['active', 'archived/old']) {
      const dir = path.join(root, run, 'trajectories');
      await mkdir(dir, { recursive: true });
      await writeFile(path.join(dir, 'index.json'), JSON.stringify(index));
      await writeFile(path.join(dir, filename), JSON.stringify(doc));
    }
    await mkdir(path.join(root, 'raw-only'));
    await writeFile(path.join(root, 'raw-only/traces.jsonl'), '{}');
    await symlink(path.join(root, 'active'), path.join(root, 'linked'));
    assert.deepEqual((await listRuns()).map(r => r.name), ['active']);
    const [served] = JSON.parse(await readRun('active/trajectories/index.json'));
    assert.equal(served.extra.source, undefined);
    assert.equal(served.subagent_trajectories[0].extra.source, undefined);
    assert.equal(served.steps.length, 3);
    await assert.rejects(readRun('../outside/trajectories/index.json'));
    await assert.rejects(readRun('raw-only/traces.jsonl'));
    await assert.rejects(readRun('linked/trajectories/index.json'));
    await assert.rejects(readRun('archived/old/trajectories/index.json'));
  } finally {
    if (old === undefined) delete process.env.OUTPUTS_DIR; else process.env.OUTPUTS_DIR = old;
    await rm(root, { recursive: true, force: true });
  }
});
