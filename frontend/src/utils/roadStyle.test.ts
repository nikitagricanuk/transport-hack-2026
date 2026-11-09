import assert from 'node:assert/strict';
import test from 'node:test';

import { LINK_HIGHWAYS, PATH_STYLES, ROAD_MARKS, pathFilter, pathSignature, solidRoadFilter } from './roadStyle.ts';

test('each drivable class keeps its own fill, casing and width', () => {
  const fills = ROAD_MARKS.map((mark) => mark.fill);
  const casings = ROAD_MARKS.map((mark) => mark.casing);
  assert.equal(new Set(fills).size, fills.length);
  assert.equal(new Set(casings).size, casings.length);
  for (const mark of ROAD_MARKS) {
    assert.notEqual(mark.fill, mark.casing);
    assert.ok(mark.scale > 0);
    assert.ok(mark.casingExtra > 0);
  }
  const byName = Object.fromEntries(ROAD_MARKS.map((mark) => [mark.highway, mark]));
  assert.equal(byName.motorway.fill, '#DC2626');
  assert.equal(byName.residential.fill, '#FFFFFF');
  assert.equal(byName.service.fill, '#7C3AED');
  assert.ok(byName.motorway.scale > byName.primary.scale);
  assert.ok(byName.primary.scale > byName.residential.scale);
  assert.ok(byName.residential.scale > byName.service.scale);
  assert.ok(byName.motorway.casingExtra > byName.service.casingExtra);
});

test('link roads stay dashed and out of the solid fill', () => {
  assert.deepEqual(LINK_HIGHWAYS, [
    'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link',
  ]);
  const solid = JSON.stringify(solidRoadFilter());
  for (const link of LINK_HIGHWAYS) assert.equal(solid.includes(link), true);
});

test('path classes, including footway and path, do not share a mark', () => {
  const signatures = PATH_STYLES.map(pathSignature);
  assert.equal(new Set(signatures).size, signatures.length);
  const foot = PATH_STYLES.find((style) => style.highways?.includes('footway'));
  const path = PATH_STYLES.find((style) => style.highways?.includes('path'));
  const cycle = PATH_STYLES.find((style) => style.highways?.includes('cycleway'));
  const steps = PATH_STYLES.find((style) => style.highways?.includes('steps'));
  assert.ok(foot?.dash && path?.dash && cycle?.dash && steps?.dash);
  assert.notEqual(foot.color, path.color);
  const construction = PATH_STYLES.find((style) => style.highways?.includes('construction'));
  const busway = PATH_STYLES.find((style) => style.highways?.includes('busway'));
  assert.ok(construction?.dash);
  assert.equal(busway?.dash, undefined);
  const fallback = PATH_STYLES.find((style) => style.highways === null);
  const filter = pathFilter(fallback!);
  assert.equal(JSON.stringify(filter).includes('footway'), true);
  assert.equal(JSON.stringify(filter).includes('busway'), true);
});
