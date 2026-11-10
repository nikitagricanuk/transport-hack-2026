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
    const [x,y] = f.geometry.coordinates;
    assert.ok(x > 0 && x < 0.01 && y > 0 && y < 0.01);
    assert.ok(!(x > 0.002 && x < 0.008 && y > 0.002 && y < 0.008));
  }
  assert.deepEqual(buildTrees(geo), trees);
  const residential = structuredClone(geo);
  residential.areas.features[0].properties.tags.landuse = 'residential';
  assert.equal(buildTrees(residential).features.length, 0);
});
