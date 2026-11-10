import type { FeatureCollection, Point } from 'geojson';
import type { Geo } from '../types';

/** Decorative canopies only: never inserted into the editable OSM document. */
export function buildTrees(geo: Geo): FeatureCollection<Point> {
  const features: FeatureCollection<Point>['features'] = [];
  const inside = (p: number[], ring: number[][]) => {
    let hit = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) hit = !hit;
    }
    return hit;
  };
  for (const area of geo.areas.features) {
    const t = area.properties.tags;
    if (!(['grass', 'forest', 'meadow', 'village_green', 'recreation_ground'].includes(t.landuse)
      || ['wood', 'scrub', 'grassland'].includes(t.natural) || ['park', 'garden'].includes(t.leisure))) continue;
    const rings = area.geometry.coordinates;
    const ring = rings[0];
    if (!ring.length) continue;
    const xs = ring.map(p => p[0]), ys = ring.map(p => p[1]);
    const west = Math.min(...xs), east = Math.max(...xs), south = Math.min(...ys), north = Math.max(...ys);
    const dx = 20 / (111320 * Math.cos(south * Math.PI / 180)), dy = 20 / 110540;
    // Bound work even for very large forests; retain a stable geographic grid.
    const stride = Math.max(1, Math.ceil(Math.sqrt((east - west) * (north - south) / (dx * dy) / 1500)));
    for (let y = Math.ceil(south / (dy * stride)); y * dy * stride < north; y++) {
      for (let x = Math.ceil(west / (dx * stride)); x * dx * stride < east; x++) {
        const seed = Math.abs(Math.sin(x * 12.9898 + y * 78.233));
        const p = [(x + (seed - 0.5) * 0.6) * dx * stride, (y + (seed * 7 % 1 - 0.5) * 0.6) * dy * stride];
        if (!inside(p, ring) || rings.slice(1).some(hole => inside(p, hole))) continue;
        features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: { shade: Math.floor(seed * 3), size: 0.8 + seed * 0.4 } });
        if (features.length >= 12000) return { type: 'FeatureCollection', features };
      }
    }
  }
  return { type: 'FeatureCollection', features };
}
