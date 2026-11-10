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

test('lane width follows the road class (СП 42) and an explicit width tag', async () => {
  const { laneWidthFor } = await import('./lanes.ts');
  assert.equal(laneWidthFor({ highway: 'motorway' }, 2), 3.75);
  assert.equal(laneWidthFor({ highway: 'secondary_link' }, 1), 3.5);
  assert.equal(laneWidthFor({ highway: 'residential' }, 2), 3.0);
  assert.equal(laneWidthFor({ highway: 'service' }, 1), 2.75);
  assert.equal(laneWidthFor({ highway: 'primary', width: '13' }, 4), 3.25);
  const g = buildLanes([{ id: 1, coords: [[104.28, 52.28], [104.281, 52.28]], forward: 1, backward: 1, laneW: 3 }]);
  assert.equal(g.marks.features.length, 3);
});

test('junctions are where three or more road segments meet, sized by the widest road', async () => {
  const { findJunctions } = await import('./lanes.ts');
  const a = [104.28, 52.28], b = [104.281, 52.28], c = [104.282, 52.28], d = [104.281, 52.281];
  const j = findJunctions([
    { coords: [a, b, c], nodes: [1, 2, 3], halfWidth: 7 },   // passes through node 2
    { coords: [b, d], nodes: [2, 4], halfWidth: 3 },         // ends at node 2: a T junction
    { coords: [c, [104.283, 52.28]], nodes: [3, 5], halfWidth: 7 },   // plain continuation at node 3
  ]);
  assert.deepEqual([...j.keys()], [2]);
  assert.equal(j.get(2)!.radius, 7.5);
});

test('lane lines stop at the edge of a junction instead of running through it', async () => {
  const { cutAround } = await import('./lanes.ts');
  const line = [[104.28, 52.28], [104.282, 52.28]];   // about 136 m east
  const pieces = cutAround(line, [{ coord: [104.281, 52.28], radius: 10 }]);
  assert.equal(pieces.length, 2);
  const kx = 111_320 * Math.cos((52.28 * Math.PI) / 180);
  const gap = (pieces[1][0][0] - pieces[0][pieces[0].length - 1][0]) * kx;
  assert.ok(Math.abs(gap - 20) < 0.5);
  assert.equal(cutAround(line, []).length, 1);
});
