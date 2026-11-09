import type { Feature, FeatureCollection, Geometry } from 'geojson';
import type { Geo, GeoDiff, GeoPatch } from '../types';

function replace<G extends Geometry, P extends { id: number }>(
  fc: FeatureCollection<G, P>,
  drop: Set<number>,
  add: Feature<G, P>[],
): { fc: FeatureCollection<G, P>; removed: number[] } {
  const removed: number[] = [];
  const kept = fc.features.filter((f) => {
    if (!drop.has(f.properties.id)) return true;
    removed.push(f.id as number);
    return false;
  });
  return { fc: { ...fc, features: kept.concat(add) }, removed };
}

/** Merge a backend patch into the current map data (what a full reload would return). */
export function applyPatch(base: Geo, patch: GeoPatch): GeoDiff {
  const ways = new Set(patch.ways);
  const nodes = new Set(patch.nodes);
  const w = replace(base.ways, ways, patch.features.ways);
  const a = replace(base.areas, ways, patch.features.areas);
  const n = replace(base.nodes, nodes, patch.features.nodes);
  return {
    base,
    target: { ...base, ways: w.fc, areas: a.fc, nodes: n.fc },
    changes: {
      ways: { remove: w.removed, add: patch.features.ways },
      areas: { remove: a.removed, add: patch.features.areas },
      nodes: { remove: n.removed, add: patch.features.nodes },
    },
  };
}
