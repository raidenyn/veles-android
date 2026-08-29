import assert from 'node:assert/strict';
import test from 'node:test';

import { evaluateNpmLicense } from '../lib/license-policy.mjs';
import policy from '../../.license-policy.json' with { type: 'json' };

const allowedLicenses = [
  'MIT',
  'MIT-0',
  'Apache-2.0',
  'Apache-2.0 WITH LLVM-exception',
  '0BSD',
  'BSD-1-Clause',
  'BSD-2-Clause',
  'BSD-2-Clause-Patent',
  'BSD-3-Clause',
  'BSD-3-Clause-Clear',
  'BSD-4-Clause',
  'BSD-4-Clause-Shortened',
  'BSD-4-Clause-UC',
  'ISC',
  'Zlib',
  'BlueOak-1.0.0',
  'Python-2.0',
  'Unicode-3.0',
  'MPL-2.0',
  'BSL-1.0',
];

const deniedLicenses = [
  'GPL-2.0-only',
  'AGPL-3.0-or-later',
  'LGPL-2.1-only',
  'SSPL-1.0',
  'BUSL-1.1',
  'Business-Source-License-1.1',
  'Elastic-2.0',
  'CC0-1.0',
  'Unlicense',
];

test('allows every reviewed SPDX identifier and the LLVM exception', () => {
  for (const expression of allowedLicenses) {
    assert.deepEqual(evaluateNpmLicense(expression, policy), {
      allowed: true,
      selectedLicense: expression,
    });
  }
});

test('denies every prohibited license family', () => {
  for (const expression of deniedLicenses) {
    assert.equal(evaluateNpmLicense(expression, policy).allowed, false, expression);
  }
});

test('allows an OR expression through its allowed branch', () => {
  assert.deepEqual(evaluateNpmLicense('MIT OR CC0-1.0', policy), {
    allowed: true,
    selectedLicense: 'MIT',
  });
});

test('denies an AND expression when either required license is prohibited', () => {
  assert.equal(evaluateNpmLicense('MIT AND CC0-1.0', policy).allowed, false);
});

test('reports package, version, detected text, and policy path for rejected expressions', () => {
  const result = evaluateNpmLicense('Proprietary-License', {
    ...policy,
    diagnostic: {
      package: '@example/package',
      version: '1.2.3',
      licenseText: 'Custom license text',
    },
  });

  assert.equal(result.allowed, false);
  assert.equal(result.selectedLicense, null);
  assert.match(result.diagnostic, /@example\/package@1\.2\.3/);
  assert.match(result.diagnostic, /Proprietary-License/);
  assert.match(result.diagnostic, /Custom license text/);
  assert.match(result.diagnostic, /\.license-policy\.json/);
});

test('denies unknown, missing, and malformed expressions', () => {
  for (const expression of ['Unknown-1.0', '', null, 'MIT OR', '(MIT']) {
    assert.equal(evaluateNpmLicense(expression, policy).allowed, false, String(expression));
  }
});
