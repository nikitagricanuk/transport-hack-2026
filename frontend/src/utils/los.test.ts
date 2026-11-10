import assert from 'node:assert/strict';
import test from 'node:test';
import { levelOfService } from './los.ts';

test('LOS changes at the ODM boundaries and capacity itself is level E', () => {
  const samples = [0.199, 0.20, 0.449, 0.45, 0.699, 0.70, 0.899, 0.90, 1, 1.001];
  assert.deepEqual(samples.map(levelOfService), ['A', 'B', 'B', 'C', 'C', 'D', 'D', 'E', 'E', 'F']);
});
