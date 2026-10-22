// Run with: node --test tests/research-handoff.test.cjs
// These checks validate design examples, not production parsing, storage or authorization.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const sample = require('../examples/research/handoff.json');
const get = (rows, id) => {
  const row = rows.find((item) => item.id === id);
  assert.ok(row, `Missing example reference: ${id}`);
  return row;
};

test('example is synthetic and all scope, search and material references resolve', () => {
  assert.equal(sample.kind, 'design_example');
  assert.equal(sample.synthetic, true);
  for (const key of ['search_runs', 'hits', 'materials', 'snapshots', 'parse_results', 'research_versions', 'evidence', 'facts', 'claims']) {
    assert.equal(new Set(sample[key].map((row) => row.id)).size, sample[key].length, key);
  }
  for (const run of sample.search_runs) {
    assert.equal(run.scope_id, sample.research_scope.id);
    assert.equal(run.owner_id, sample.research_scope.owner_id);
  }
  for (const hit of sample.hits) {
    const run = get(sample.search_runs, hit.search_run_id);
    const material = get(sample.materials, hit.material_id);
    assert.notEqual(run.status, 'failed');
    assert.equal(run.owner_id, material.owner_id);
    assert.equal(hit.url, material.source_url);
  }
  for (const material of sample.materials) assert.equal(new URL(material.source_url).hostname, 'example.invalid');
});

test('snapshot hashes, parsed paragraph positions and evidence quotes agree', () => {
  for (const snapshot of sample.snapshots) {
    get(sample.materials, snapshot.material_id);
    assert.equal(createHash('sha256').update(snapshot.raw, 'utf8').digest('hex'), snapshot.sha256);
  }
  for (const parsed of sample.parse_results) {
    const snapshot = get(sample.snapshots, parsed.snapshot_id);
    // This literal fixture check is not an HTML parser.
    const paragraphs = [...snapshot.raw.matchAll(/<p>(.*?)<\/p>/g)].map((match) => match[1]);
    assert.equal(parsed.blocks.length, paragraphs.length);
    for (const block of parsed.blocks) assert.equal(block.text, paragraphs[block.paragraph - 1]);
  }
  for (const evidence of sample.evidence) {
    const version = get(sample.research_versions, evidence.research_version_id);
    const parsed = get(sample.parse_results, version.parse_result_id);
    const block = get(parsed.blocks, evidence.block_id);
    const bytes = Buffer.from(block.text, 'utf8');
    assert.ok(Number.isInteger(evidence.start) && Number.isInteger(evidence.end));
    assert.ok(evidence.start >= 0 && evidence.start < evidence.end && evidence.end <= bytes.length);
    assert.equal(bytes.subarray(evidence.start, evidence.end).toString('utf8'), evidence.quote);
  }
  const chineseEvidence = get(sample.evidence, 'evidence-missing');
  const chineseBlock = get(get(sample.parse_results, 'parse-a1').blocks, chineseEvidence.block_id);
  assert.notEqual(chineseBlock.text.slice(chineseEvidence.start, chineseEvidence.end), chineseEvidence.quote);
});

test('revision and reparsing examples retain the old research binding', () => {
  for (const version of sample.research_versions) {
    const snapshot = get(sample.snapshots, version.snapshot_id);
    const parsed = get(sample.parse_results, version.parse_result_id);
    assert.equal(version.scope_id, sample.research_scope.id);
    assert.equal(version.owner_id, sample.research_scope.owner_id);
    assert.equal(get(sample.materials, snapshot.material_id).owner_id, version.owner_id);
    assert.equal(parsed.snapshot_id, snapshot.id);
  }
  const old = get(sample.research_versions, 'research-v1');
  const reparsed = get(sample.research_versions, 'research-reparsed');
  const revised = get(sample.research_versions, 'research-revised');
  assert.equal(old.snapshot_id, 'snapshot-a1');
  assert.equal(old.parse_result_id, 'parse-a1');
  assert.equal(reparsed.snapshot_id, old.snapshot_id);
  assert.notEqual(reparsed.parse_result_id, old.parse_result_id);
  assert.notEqual(revised.snapshot_id, old.snapshot_id);
  assert.equal(get(sample.snapshots, revised.snapshot_id).material_id, get(sample.snapshots, old.snapshot_id).material_id);
  assert.notEqual(get(sample.snapshots, revised.snapshot_id).sha256, get(sample.snapshots, old.snapshot_id).sha256);
});

test('identical content does not imply cross-owner access in the examples', () => {
  const first = get(sample.snapshots, 'snapshot-a1');
  const second = get(sample.snapshots, 'snapshot-b1');
  assert.equal(first.sha256, second.sha256);
  assert.notEqual(first.material_id, second.material_id);
  for (const expectation of sample.access_expectations) {
    const snapshot = get(sample.snapshots, expectation.snapshot_id);
    const material = get(sample.materials, snapshot.material_id);
    assert.equal(expectation.expected, expectation.owner_id === material.owner_id ? 'allowed' : 'denied');
  }
});

test('unknown costs, failed searches and missing facts remain distinct from zero', () => {
  assert.deepEqual(sample.research_scope.budget, { status: 'unconfirmed', amount: null });
  for (const run of sample.search_runs) {
    assert.deepEqual(run.cost, { status: 'unknown', amount: null });
    assert.equal(run.failure.code, 'timeout');
  }
  assert.equal(get(sample.search_runs, 'search-partial').status, 'partial');
  assert.equal(get(sample.search_runs, 'search-failed').status, 'failed');
  assert.equal(sample.hits.some((hit) => hit.search_run_id === 'search-failed'), false);
  assert.equal(sample.hits[0].abstract_status, 'missing');
  assert.equal(sample.hits[0].abstract, null);
  for (const fact of sample.facts) {
    get(sample.evidence, fact.evidence_id);
    assert.equal(fact.statement_type, 'company_self_report');
  }
  assert.equal(get(sample.facts, 'fact-sessions').reporting_period, '2025');
  assert.equal(get(sample.facts, 'fact-sessions').value, 8);
  assert.equal(get(sample.facts, 'fact-sessions').unit, 'sessions');
  const missing = get(sample.facts, 'fact-financing');
  assert.equal(missing.reporting_period, null);
  assert.equal(missing.value_status, 'missing');
  assert.equal(missing.value, null);
  assert.equal(missing.unit, null);
});

test('existing quotations do not turn an unsupported causal claim into support', () => {
  for (const claim of sample.claims) {
    get(sample.research_versions, claim.research_version_id);
    assert.equal(claim.support.method, 'synthetic_manual_expectation');
    assert.ok(claim.support.reason.length > 0);
    for (const id of claim.evidence_ids) assert.equal(get(sample.evidence, id).research_version_id, claim.research_version_id);
  }
  assert.equal(get(sample.claims, 'claim-report').support.status, 'supported');
  const causal = get(sample.claims, 'claim-causation');
  assert.ok(causal.evidence_ids.length > 0);
  assert.equal(causal.support.status, 'insufficient');
});
