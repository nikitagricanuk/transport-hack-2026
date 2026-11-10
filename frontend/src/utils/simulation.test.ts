import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeSampler } from './simulation.ts';

test('interpolates position and heading without inventing positions for new or missing cars', () => {
  const sample = makeSampler({ frames: [
    { time: 0, vehicles: [['a', 104, 52, 350], ['gone', 104, 52, 0]] },
    { time: 1, vehicles: [['a', 104.02, 52, 10], ['new', 104, 52, 0]] },
    { time: 2, vehicles: [] },
  ] });
  const halfway = sample(0.5);
  assert.equal(halfway.length, 2);
  assert.ok(Math.abs(halfway[0][1] - 104.01) < 1e-9);
  assert.equal(halfway[0][3] % 360, 0);
  assert.deepEqual(sample(1).map((car) => car[0]), ['a', 'new']);
  assert.deepEqual(sample(2), []);
});
