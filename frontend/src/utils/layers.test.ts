import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_LAYERS,
  ISSUE_FILTERS,
  allIssueFiltersOn,
  allLayersOn,
  allOff,
  allOn,
  applyLayerChange,
  defaultIssueFilters,
  issueVisible,
} from './layers.ts';

test('roads and road errors start unchecked', () => {
  assert.equal(DEFAULT_LAYERS.roads, false);
  assert.equal(DEFAULT_LAYERS.issues, false);
  assert.equal(DEFAULT_LAYERS.paths, true);
  assert.equal(DEFAULT_LAYERS.buildings, true);
  const filters = defaultIssueFilters();
  for (const item of ISSUE_FILTERS) assert.equal(filters[item.key], false);
});

test('select all turns every layer on', () => {
  const next = allLayersOn();
  assert.equal(allOn(next), true);
});

test('unchecking roads preserves issues and filters', () => {
  const filters = allIssueFiltersOn();
  const prev = allLayersOn();
  const next = applyLayerChange(prev, filters, { ...prev, roads: false });
  assert.equal(next.layers.roads, false);
  assert.equal(next.layers.issues, true);
  assert.equal(next.layers.paths, true);
  for (const item of ISSUE_FILTERS) {
    if (item.road) assert.equal(next.filters[item.key], true);
  }
  assert.equal(issueVisible(next.filters, 'junction'), true);
});

test('unchecking roads preserves hidden error filters', () => {
  const prev = { ...allLayersOn(), issues: false };
  const next = applyLayerChange(prev, allIssueFiltersOn(), { ...prev, roads: false });
  assert.equal(next.layers.issues, false);
  assert.equal(issueVisible(next.filters, 'lanes'), true);
});

test('turning the issues layer on with empty filters selects every error type', () => {
  const next = applyLayerChange(DEFAULT_LAYERS, defaultIssueFilters(), { ...DEFAULT_LAYERS, issues: true });
  assert.equal(next.layers.issues, true);
  assert.equal(next.layers.roads, false);
  for (const item of ISSUE_FILTERS) assert.equal(next.filters[item.key], true);
});

test('select all errors checks every filter without turning roads on', () => {
  const filters = allIssueFiltersOn();
  for (const item of ISSUE_FILTERS) assert.equal(filters[item.key], true);
  assert.equal(issueVisible(filters, 'geometry'), true);
  assert.equal(issueVisible(filters, 'other'), false);
});

test('a non-road layer toggle leaves error filters alone', () => {
  const filters = allIssueFiltersOn();
  const prev = allLayersOn();
  const next = applyLayerChange(prev, filters, { ...prev, buildings: false });
  assert.equal(next.layers.buildings, false);
  assert.equal(next.layers.roads, true);
  assert.deepEqual(next.filters, filters);
});

test('second «select all» clears every checkbox', () => {
  const on = allLayersOn();
  assert.equal(allOn(on), true);
  const off = allOff(on);
  assert.equal(Object.values(off).every(v => typeof v === 'object' ? Object.values(v).every(x=>!x) : !v), true);
  assert.equal(allOn(defaultIssueFilters()), false);
  assert.equal(allOn(allIssueFiltersOn()), true);
});
