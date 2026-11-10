import assert from 'node:assert/strict';
import test from 'node:test';
import { NODE_PRESETS, presetPatch, isPreset } from './nodePresets.ts';
import { toggleSelection, boxSelection } from './selection.ts';
import { analysisOverlay, cycleStop, formatRoute } from './networkAnalysis.ts';
import { layerVisibilityOps, allLayersOn } from './layers.ts';
import { taperPolygon, LANE_W } from './lanes.ts';

test('stop conversion clears all classification tags without deleting other rail tags', () => {
  const current = { highway: 'bus_stop', public_transport: 'platform', bus: 'yes', railway: 'tram_stop' };
  const patch = presetPatch(current, NODE_PRESETS[0]);
  assert.equal(patch.public_transport, null);
  assert.equal(patch.railway, null);
  assert.equal(isPreset(current, NODE_PRESETS[3]), true);
  assert.equal(presetPatch({ railway: 'signal' }, NODE_PRESETS[0]).railway, undefined);
});
test('selection toggle and box deduplicate', () => {
  const a = { kind: 'way' as const, id: 1 },
    b = { kind: 'node' as const, id: 1 };
  assert.deepEqual(toggleSelection([a], a), []);
  assert.deepEqual(toggleSelection([a], b), [a, b]);
  assert.deepEqual(boxSelection([a, a, b]), [a, b]);
});
test('cycling stops keeps sets disjoint', () => {
  assert.deepEqual(cycleStop([1], [], 1), [[], [1]]);
  assert.deepEqual(cycleStop([], [1], 1), [[], []]);
});
test('visibility operations do not contain data', () => {
  const a = allLayersOn();
  assert.deepEqual(layerVisibilityOps(a, { ...a, roads: false }), [{ key: 'roads', visible: false }]);
});
test('analysis overlay and units', () => {
  assert.deepEqual(analysisOverlay(null).features, []);
  assert.equal(formatRoute({time_s:90,length_m:100}), '100 м · 1.5 мин');
});
test('lane taper widths match lane counts', () => {
  const p = taperPolygon(
    [
      [104, 52],
      [104.001, 52],
    ],
    3,
    2,
  );
  assert.ok(Math.abs(Math.abs((p[3][1] - p[0][1]) * 110540) - 3 * LANE_W) < 0.01);
  assert.ok(Math.abs(Math.abs((p[2][1] - p[1][1]) * 110540) - 2 * LANE_W) < 0.01);
});

test('changing one load colour does not repaint unchanged roads', async()=>{
  const {loadColorChanges}=await import('./loadColors.ts');
  assert.deepEqual(loadColorChanges(new Map([[1,.4],[2,.5]]),new Map([[1,.4],[2,.8]])),{remove:[],set:[{id:2,vc:.8}]});
});

test('segment clipping projects endpoints, includes bends, and ignores click order',async()=>{
  const {segmentGeometry}=await import('./segments.ts');
  const coords=[[104,52],[104.001,52],[104.001,52.001]];
  const a={lat:52,lon:104.0005},b={lat:52.0005,lon:104.001};
  const g=segmentGeometry(coords,a,b);
  assert.equal(g.coordinates.length,3);
  assert.ok(g.length_m>85 && g.length_m<100);
  assert.deepEqual(segmentGeometry(coords,b,a),g);
});
