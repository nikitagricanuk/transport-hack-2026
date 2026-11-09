import assert from 'node:assert/strict';
import test from 'node:test';

import { joinHandles, sharedHighwayNodes } from './joins.ts';
import type { Geo } from '../types.ts';

function line(id: number, layer: 'road' | 'path' | 'building', nodes: number[], coords: number[][], area?: string): Geo['ways']['features'][number] {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: coords },
    properties: {
      id, kind: 'way', layer: layer === 'building' ? 'building' : layer, highway: layer === 'road' ? 'residential' : 'footway',
      name: '', lanes: 1, dir: 0, bridge: false, nodes, tags: area ? { area } : {},
      permissions: { foot: true, bicycle: true, horse: true, motorcycle: true, hgv: true, psv: true, moped: true, wheelchair: true },
    },
  };
}

function geo(features: Geo['ways']['features']): Geo {
  return {
    ways: { type: 'FeatureCollection', features },
    areas: { type: 'FeatureCollection', features: [] },
    nodes: { type: 'FeatureCollection', features: [] },
    bounds: null,
  };
}

const east = line(10, 'road', [1, 2, 3], [[0, 0], [1, 0], [2, 0]]);
const north = line(11, 'road', [4, 2, 5], [[1, -1], [1, 0], [1, 1]]);
const house = line(12, 'building', [2, 6, 7, 2], [[1, 0], [1.1, 0], [1.1, 0.1], [1, 0]]);

test('a shared crossing is a split handle and a building corner is not', () => {
  const map = geo([east, north, house]);
  const shared = sharedHighwayNodes(map);
  assert.deepEqual([...shared.keys()], [2]);
  assert.deepEqual(shared.get(2), [1, 0]);
  assert.deepEqual(joinHandles(map, { kind: 'way', id: 10 }), [{ id: 2, lon: 1, lat: 0 }]);
  assert.deepEqual(joinHandles(map, { kind: 'node', id: 2 }), [{ id: 2, lon: 1, lat: 0 }]);
  assert.deepEqual(joinHandles(map, { kind: 'node', id: 1 }), []);
  assert.deepEqual(joinHandles(map, { kind: 'way', id: 12 }), []);
  assert.deepEqual(joinHandles(map, null), []);
});
