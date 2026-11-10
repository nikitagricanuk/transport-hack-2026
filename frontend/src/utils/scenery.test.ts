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
  assert.ok(models.features.some(f => f.properties!.base === 2.65 && f.properties!.height === 2.9));
  assert.ok(models.features.every(f => f.geometry.coordinates[0].length === 5 && f.properties!.height > f.properties!.base));
  data.nodes.features = [stop(5, { highway: 'bus_stop', shelter: 'no' })];
  assert.equal(buildBusStops(data).features.length, 2);
  assert.equal(SCENERY_MINZOOM, 17);
});
