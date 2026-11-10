import type { Feature, FeatureCollection, Geometry } from 'geojson';
import type { Issue } from '../types';
export interface AnalysisResult {
  stops?: { id: number; coord: number[]; reached: boolean }[];
  explain?: { sample_points: { coord: number[]; walk_s: number; wait_s: number; ride_s: number }[] };
  read_only: boolean;
  revision: number;
  found?: boolean;
  path?: Feature | null;
  stats?: {
    length_m: number;
    time_s: number;
    avg_kmh: number;
    edges: number;
    ways: { id: number; name: string; length_m: number }[];
    restrictions_applied: number;
    restrictions_skipped: number[];
  };
  issues: Issue[];
  assumptions: { key: string; label: string }[];
  sources: { label: string; url?: string }[];
  polygons?: FeatureCollection;
  bands?: {
    minutes: number;
    geometry?: Geometry;
    no_transfer?: { geometry: Geometry; area_km2: number; stops_reached: number };
    with_transfer?: { geometry: Geometry; area_km2: number; stops_reached: number };
    area_km2: number;
    road_km: number;
    ring_road_km: number;
  }[];
  boundary_limited?: boolean;
  buildings?: { total: number; both: number; one_side: number; none: number; none_ids: number[] };
  compare?: { before: AnalysisResult; delta_m?: number | null; delta_s?: number | null };
}
export const formatLength = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(2)} км` : `${Math.round(n)} м`);
export const formatTime = (n: number) => `${(n / 60).toFixed(1)} мин`;
export function analysisOverlay(r: AnalysisResult | null): FeatureCollection {
  const features: Feature[] = [];
  if (r?.path) features.push(r.path);
  features.push(...(r?.polygons?.features ?? []));
  for (const [i, b] of (r?.bands ?? []).entries())
    features.push({
      type: 'Feature',
      geometry: b.geometry ?? b.with_transfer!.geometry,
      properties: {
        kind: b.with_transfer ? 'pt' : 'isochrone',
        band: i,
        minutes: b.minutes,
        color: ['#22c55e', '#84cc16', '#eab308', '#f97316', '#ef4444'][i % 5],
      },
    });
  for (const s of r?.stops ?? [])
    if (s.coord)
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: s.coord },
        properties: { color: s.reached ? '#16a34a' : '#64748b', kind: 'pt-stop', id: s.id },
      });
  for (const s of r?.explain?.sample_points ?? [])
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: s.coord },
      properties: { ...s, color: '#2563eb', kind: 'pt-explain' },
    });
  if (r?.compare?.before)
    features.push(
      ...analysisOverlay(r.compare.before).features.map((f) => ({
        ...f,
        properties: { ...f.properties, before: true },
      })),
    );
  return { type: 'FeatureCollection', features };
}
export function cycleStop(a: number[], b: number[], id: number): [number[], number[]] {
  return a.includes(id)
    ? [a.filter((n) => n !== id), [...b, id]]
    : b.includes(id)
      ? [a, b.filter((n) => n !== id)]
      : [[...a, id], b];
}
