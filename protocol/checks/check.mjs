#!/usr/bin/env node
// OTP-02 aggregate: fresh candidate generation and independent evidence before
// the contract suites. Reports only live under build/protocol/ and are never
// accepted as pre-existing evidence.
import { rmSync, existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildEvidenceInventory } from './evidence.mjs';

const root = join(fileURLToPath(new URL('../..', import.meta.url)));
const build = join(root, 'build', 'protocol');
const crypto = join(root, 'protocol', 'v1', 'fixtures', 'crypto.json');
const manifest = join(root, 'protocol', 'v1', 'manifest.json');

function requireVersion(command, minimum, label) {
    const result = spawnSync(command, ['--version'], { cwd: root, encoding: 'utf8' });
    if (result.error || result.status !== 0) {
        throw new Error(`${label} ${minimum[0]}.${minimum[1]}+ is required`);
    }
    const match = `${result.stdout}${result.stderr}`.match(/(\d+)\.(\d+)/);
    if (match === null || Number(match[1]) < minimum[0] || (Number(match[1]) === minimum[0] && Number(match[2]) < minimum[1])) {
        throw new Error(`${label} ${minimum[0]}.${minimum[1]}+ is required; found ${`${result.stdout}${result.stderr}`.trim()}`);
    }
}

function run(command, arguments_) {
    const result = spawnSync(command, arguments_, { cwd: root, encoding: 'utf8', stdio: 'inherit' });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exit(result.status ?? 1);
}

function readReport(name) {
    const path = join(build, name);
    if (!existsSync(path)) throw new Error(`missing fresh evidence ${name}`);
    return JSON.parse(readFileSync(path, 'utf8'));
}

function validateManifestEvidence() {
    const value = JSON.parse(readFileSync(manifest, 'utf8'));
    const statuses = new Set(value.status_values);
    const ids = new Set();
    for (const entry of value.entries) {
        if (ids.has(entry.id)) throw new Error(`manifest has duplicate requirement id ${entry.id}`);
        ids.add(entry.id);
        if (!statuses.has(entry.status)) throw new Error(`manifest ${entry.id} has unknown status ${entry.status}`);
        if (entry.status === 'specified_evidence_collected' && (!Array.isArray(entry.evidence_ids) || entry.evidence_ids.length < 2)) {
            throw new Error(`manifest ${entry.id}: collected status requires fixture/check evidence`);
        }
        if (entry.status === 'specified_awaiting_controller_review' && (!Array.isArray(entry.evidence_ids) || entry.evidence_ids.length !== 1)) {
            throw new Error(`manifest ${entry.id}: awaiting-review entries are documentary-only (anchor evidence)`);
        }
        if (!Array.isArray(entry.evidence_ids) || new Set(entry.evidence_ids).size !== entry.evidence_ids.length) {
            throw new Error(`manifest ${entry.id} has invalid evidence_ids`);
        }
        if ((entry.status === 'reviewed' || entry.status === 'accepted') && entry.evidence_ids.length === 0) {
            throw new Error(`manifest ${entry.id} cannot be ${entry.status} without evidence`);
        }
    }
    if (value.entries.some((entry) => entry.status === 'accepted')) {
        throw new Error('manifest cannot claim OTP-02 acceptance before Task 7 review evidence');
    }
}

requireVersion(process.execPath, [22, 0], 'Node');
requireVersion('python3', [3, 8], 'python3');
validateManifestEvidence();
if (buildEvidenceInventory(join(root, 'protocol', 'v1')).entries.length !== 156) throw new Error('evidence inventory must cover 156 requirements');
run(process.execPath, ['protocol/tools/sync-manifest-evidence.mjs', '--check']);
run(process.execPath, ['protocol/tools/generate-fragmentation-vectors.mjs', '--check']);
rmSync(build, { recursive: true, force: true });
run(process.execPath, ['protocol/tools/generate-vectors.mjs']);
run('python3', ['protocol/tools/crosscheck-vectors/oracle.py', crypto]);
run(process.execPath, ['protocol/tools/crosscheck-vectors/openssl-check.mjs', crypto]);
run('python3', ['protocol/tools/crosscheck-vectors/oracle.py', join(build, 'crypto-candidate.json')]);
run(process.execPath, ['protocol/tools/crosscheck-vectors/openssl-check.mjs']);
for (const [oracle, openssl] of [
    ['crosscheck-oracle-committed.json', 'crosscheck-openssl-committed.json'],
    ['crosscheck-oracle.json', 'crosscheck-openssl.json'],
]) {
    const reports = [readReport(oracle), readReport(openssl)];
    for (const fixture of JSON.parse(readFileSync(crypto, 'utf8')).fixtures) {
        if (!reports.some((report) => report.fixtures?.[fixture.id]?.status === 'ok')) {
            throw new Error(`${oracle}/${openssl} has no successful independent evidence for ${fixture.id}`);
        }
    }
}
if (!process.argv.includes('--evidence-only')) {
    run(process.execPath, [
        '--test',
        'protocol/checks/contract.test.mjs',
        'protocol/checks/bridge.test.mjs',
        'protocol/checks/state.test.mjs',
        'protocol/checks/crypto.test.mjs',
    ]);
    run(process.execPath, ['protocol/tools/check-contract-text.mjs']);
}
