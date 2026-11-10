import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildTrees } from './scenery.ts';
import type { Geo } from '../types';
const ring = (a: number, b: number) => [[a,a],[b,a],[b,b],[a,b],[a,a]];
const geo = { areas: { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { tags: { landuse: 'grass' } }, geometry: { type: 'Polygon', coordinates: [ring(0,0.01),ring(0.002,0.008)] } }] }, ways: { features: [] }, nodes: { features: [] } } as unknown as Geo;
test('decorative trees respect green polygon boundaries and holes and stay stable', () => {
  const trees = buildTrees(geo);
  assert.ok(trees.features.length > 0);
  for (const f of trees.features) {
    for (const [x,y] of f.geometry.coordinates[0]) {
      assert.ok(x > 0 && x < 0.01 && y > 0 && y < 0.01);
      assert.ok(!(x > 0.002 && x < 0.008 && y > 0.002 && y < 0.008));
    }
  }
  assert.ok(trees.features.some(f => f.properties!.height > 7));
  assert.ok(trees.features.every(f => f.properties!.height > f.properties!.base));
  assert.deepEqual(buildTrees(geo), trees);
  const residential = structuredClone(geo);
  residential.areas.features[0].properties.tags.landuse = 'residential';
  assert.equal(buildTrees(residential).features.length, 0);
});

test('bus stop models use real heights and omit stop positions and duplicate platforms', async () => {
  const { buildBusStops, SCENERY_MINZOOM } = await import('./scenery.ts');
  const data = structuredClone(geo);
  const stop = (id: number, tags: Record<string, string>, x = 0.005) => ({ type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: [x, 0.005] }, properties: { id, category: 'stop' as const, kind: 'node' as const, junction: false, tags } });
  data.nodes.features = [stop(1, { highway: 'bus_stop' }), stop(2, { highway: 'bus_stop' }),
    stop(3, { bus: 'yes', public_transport: 'stop_position' }, 0.006), stop(4, { railway: 'tram_stop' }, 0.007)];
  const models = buildBusStops(data);
  assert.equal(new Set(models.features.map(f => f.properties!.nodeId)).size, 1);
  assert.ok(models.features.some(f => f.properties!.base === 2.65*1.3 && f.properties!.height === 2.9*1.3));
  assert.ok(models.features.every(f => f.geometry.coordinates[0].length === 5 && f.properties!.height > f.properties!.base));
  data.nodes.features = [stop(5, { highway: 'bus_stop', shelter: 'no' })];
  assert.equal(buildBusStops(data).features.length, 2);
  assert.equal(SCENERY_MINZOOM, 17);
});

test('shelter benches sit behind the opening on either side of a road, regardless of way direction', async () => {
  const { buildBusStops } = await import('./scenery.ts');
  for (const reverse of [false, true]) for (const side of [-1, 1]) {
    const data = structuredClone(geo);
    const lat = 0.005 + side * 0.0001;
    const coords = [[0,0.005],[0.01,0.005]];
    data.ways.features = [{ type:'Feature', properties: { layer:'road' }, geometry:{ type:'LineString', coordinates:reverse ? coords.reverse() : coords } }] as Geo['ways']['features'];
    data.nodes.features = [{ type:'Feature', properties: { id:1,category:'stop',tags:{highway:'bus_stop'} }, geometry:{type:'Point',coordinates:[0.005,lat]} }] as Geo['nodes']['features'];
    const parts = buildBusStops(data).features;
    const back = parts.find(f => f.properties!.color === '#AE8961' && f.properties!.height === 1.05*1.3)!;
    const backLat = back.geometry.coordinates[0].slice(0,4).reduce((sum,p) => sum+p[1],0)/4;
    assert.ok((backLat-lat)*side > 0, 'bench back must face away from the road');
    const roof = parts.find(f => f.properties!.color === '#3D6474')!;
    const xs = roof.geometry.coordinates[0].map(p => p[0]);
    assert.ok(Math.abs((Math.max(...xs)-Math.min(...xs))*111320 - 4.6*1.3) < 0.01);
  }
});

test('traffic light models include poles, housing and static lenses only for signal nodes', async () => {
  const { buildTrafficLights } = await import('./scenery.ts');
  const data = structuredClone(geo);
  data.nodes.features = [
    { type:'Feature', properties:{id:1,category:'signal',tags:{highway:'traffic_signals'}},geometry:{type:'Point',coordinates:[0.005,0.005]}},
    { type:'Feature', properties:{id:2,category:'crossing',tags:{crossing:'traffic_signals'}},geometry:{type:'Point',coordinates:[0.006,0.005]}},
    { type:'Feature', properties:{id:3,category:'stop',tags:{highway:'bus_stop'}},geometry:{type:'Point',coordinates:[0.007,0.005]}},
  ] as Geo['nodes']['features'];
  const parts = buildTrafficLights(data).features;
  assert.equal(parts.length,10);
  assert.deepEqual([...new Set(parts.map(f=>f.properties!.nodeId))],[1,2]);
  assert.ok(parts.every(f=>f.properties!.height > f.properties!.base));
  assert.equal(parts.filter(f=>f.properties!.color === '#26313A').length,2);
});
