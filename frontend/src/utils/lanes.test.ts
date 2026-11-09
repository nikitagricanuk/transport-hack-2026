import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LANE_W, buildLanes, laneSplit, offsetLine } from './lanes.ts';

const M_PER_DEG_LAT = 110_540;

test('offsetLine moves a northbound line east for a positive offset', () => {
  const line = [[104.28, 52.28], [104.28, 52.281]];
  const off = offsetLine(line, LANE_W);
  const kx = 111_320 * Math.cos((52.2805 * Math.PI) / 180);
  assert.ok(Math.abs((off[0][0] - 104.28) * kx - LANE_W) < 0.01);
  assert.ok(Math.abs(off[0][1] - 52.28) * M_PER_DEG_LAT < 0.01);
});

test('laneSplit follows tags and one-way direction', () => {
  assert.deepEqual(laneSplit({ lanes: '4' }, 0), { forward: 2, backward: 2 });
  assert.deepEqual(laneSplit({ lanes: '3', 'lanes:forward': '2' }, 0), { forward: 2, backward: 1 });
  assert.deepEqual(laneSplit({ lanes: '2' }, 1), { forward: 2, backward: 0 });
  assert.deepEqual(laneSplit({ lanes: '2' }, -1), { forward: 0, backward: 2 });
  assert.deepEqual(laneSplit({}, 0), { forward: 1, backward: 1 });
});

test('buildLanes draws one surface, n+1 markings and one arrow line per lane', () => {
  const g = buildLanes([{ id: 1, coords: [[104.28, 52.28], [104.281, 52.28]], forward: 2, backward: 1 }]);
  assert.equal(g.surface.features.length, 1);
  assert.deepEqual(g.marks.features.map((f) => f.properties?.kind), ['edge', 'center', 'lane', 'edge']);
  assert.deepEqual(g.arrows.features.map((f) => f.properties?.dir), ['backward', 'forward', 'forward']);
  // backward lane runs against the way: its first point is at the far end
  const back = g.arrows.features[0].geometry.coordinates;
  assert.ok(back[0][0] > back[back.length - 1][0]);
});
