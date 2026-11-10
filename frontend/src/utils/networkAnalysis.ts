import type { Feature, FeatureCollection, Geometry } from 'geojson';
import type { Issue } from '../types';

export type AnalysisPoint = { lat?: number; lon?: number; node_id?: number; way_id?: number };
export type PickedPoint = { lat: number; lon: number; nodeId?: number; wayId?: number; isBuilding?: boolean };
export type RouteQuery = { from: AnalysisPoint; to: AnalysisPoint; mode: 'car' | 'walk'; metric: 'time' | 'length'; walk_kmh: number; use_closures: boolean; compare: 'original' | null };
export interface Band { minutes: number; area_km2: number; road_km: number; ring_road_km: number; geometry: Geometry }
export interface AnalysisResult {
  read_only: boolean;
  revision: number;
  found?: boolean;
  path?: Feature | null;
  stats?: { length_m: number; time_s: number; avg_kmh: number; edges: number; ways: { id: number; name: string }[]; restrictions_applied: number; restrictions_skipped: number[] } | null;
  issues: Issue[];
  assumptions: { key: string; label: string }[];
  sources: { label: string; url?: string }[];
  bands?: Band[];
  boundary_limited?: boolean;
  polygons?: FeatureCollection;
  building_features?: FeatureCollection;
  buildings?: { total: number; both: number; one_side: number; none: number };
  stops?: { id: number; coord: number[]; set?: string; reached?: boolean }[];
  network?: { walk_km_total: number; walk_km_reached_a: number; walk_km_reached_b: number; components_with_stops: number };
  compare?: { before: AnalysisResult; delta_m?: number; delta_s?: number } | null;
}
export const formatRoute = (s: { length_m: number; time_s: number }) => `${Math.round(s.length_m)} м · ${(s.time_s / 60).toFixed(1)} мин`;
export function cycleStop(a: number[], b: number[], id: number): [number[], number[]] {
  return a.includes(id) ? [a.filter(n => n !== id), [...b.filter(n => n !== id), id]]
    : b.includes(id) ? [a, b.filter(n => n !== id)] : [[...a, id], b];
}
export function analysisOverlay(result: AnalysisResult | null, a?: AnalysisPoint | null, b?: AnalysisPoint | null): FeatureCollection {
  const features: Feature[] = [];
  const add = (r: AnalysisResult, before = false) => {
    if (r.path) features.push({ ...r.path, properties: { ...r.path.properties, before, colour: '#2563eb' } });
    for (const band of [...(r.bands ?? [])].reverse()) features.push({ type: 'Feature', geometry: band.geometry,
      properties: { before, band: band.minutes, colour: ['#16a34a', '#65a30d', '#eab308', '#f97316', '#dc2626'][Math.round(((r.bands ?? []).findIndex(b => b.minutes === band.minutes) / Math.max(1, (r.bands?.length ?? 1) - 1)) * 4)] ?? '#16a34a' } });
    for (const f of r.polygons?.features ?? []) features.push({ ...f, properties: { ...f.properties, before,
      colour: ({ A: '#2563eb', B: '#f97316', AB: '#9333ea', uncovered: '#64748b' } as Record<string,string>)[String(f.properties?.set)] } });
    if (!before) for (const f of r.building_features?.features ?? []) features.push({ ...f, properties: { ...f.properties,
      colour: ({ both: '#9333ea', one_side: '#f97316', none: '#64748b' } as Record<string,string>)[String(f.properties?.building_class)] } });
  };
  if (result) {
    add(result);
    if (result.compare?.before) add(result.compare.before, true);
    for (const stop of result.stops ?? []) if (stop.set && stop.coord.length === 2) features.push({
      type: 'Feature', geometry: { type: 'Point', coordinates: stop.coord }, properties: { label: stop.set, colour: stop.set === 'A' ? '#2563eb' : '#f97316' },
    });
  }
  for (const [label, p] of [['А', a], ['Б', b]] as const) if (p?.lat !== undefined && p.lon !== undefined) features.push({
    type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { label, colour: label === 'А' ? '#2563eb' : '#f97316' },
  });
  return { type: 'FeatureCollection', features };
}

export function mockHeadways(routes: { relation_id: number }[]): Record<string, number> {
  return Object.fromEntries(routes.map(r => [String(r.relation_id), 4 + Math.abs(r.relation_id) % 17]));
}
export function pickedObject(p: PickedPoint): AnalysisPoint {
  return p.isBuilding && p.wayId !== undefined ? { way_id: p.wayId } : p.nodeId !== undefined ? { node_id: p.nodeId } : { lat: p.lat, lon: p.lon };
}
