import { test } from 'node:test';
import assert from 'node:assert/strict';
import { analysisOverlay, cycleStop, formatRoute, mockHeadways, pickedObject } from './networkAnalysis.ts';

test('stop selections cycle A to B to neither', () => {
  assert.deepEqual(cycleStop([],[],1),[[1],[]]);
  assert.deepEqual(cycleStop([1],[],1),[[],[1]]);
  assert.deepEqual(cycleStop([],[1],1),[[],[]]);
});
test('route overlay preserves original geometry and marks comparison', () => {
  const path = { type: 'Feature' as const, properties: {}, geometry: { type:'LineString' as const, coordinates:[[0,0],[1,1]] } };
  const before = { read_only:true, revision:0, path, issues:[], assumptions:[], sources:[] };
  const overlay = analysisOverlay({ ...before, compare:{before} });
  assert.equal(overlay.features.length,2);
  assert.equal(overlay.features[1].properties?.before,true);
  assert.deepEqual(path.properties,{});
  assert.equal(formatRoute({length_m:1000,time_s:90}),'1000 м · 1.5 мин');
});

test('mock headways are deterministic, bounded, and do not change routes', () => {
  const routes = [{relation_id:1}, {relation_id:25}];
  const values = mockHeadways(routes);
  assert.deepEqual(values, mockHeadways(routes));
  assert.ok(Object.values(values).every(v => v >= 4 && v <= 20));
  assert.deepEqual(routes,[{relation_id:1}, {relation_id:25}]);
  assert.deepEqual(pickedObject({lat:1,lon:2,wayId:3,isBuilding:true}), {way_id:3});
});
