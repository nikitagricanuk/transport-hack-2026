import type { FeatureCollection, LineString, Point, Polygon } from 'geojson';

export type Kind = 'node' | 'way' | 'relation';
export type Tags = Record<string, string>;

export interface WayProps {
  id: number;
  kind: 'way';
  layer: 'road' | 'path' | 'rail' | 'building' | 'area' | 'other';
  highway: string;
  name: string;
  lanes: number;
  dir: -1 | 0 | 1;
  bridge: boolean;
  nodes: number[];
  tags: Tags;
}

export interface NodeProps {
  id: number;
  kind: 'node';
  junction: boolean;
  category: 'signal' | 'crossing' | 'stop' | 'tagged' | 'junction';
  tags: Tags;
}

export interface Geo {
  ways: FeatureCollection<LineString, WayProps>;
  areas: FeatureCollection<Polygon, WayProps>;
  nodes: FeatureCollection<Point, NodeProps>;
  bounds: [number, number, number, number] | null; // minlat, minlon, maxlat, maxlon
}

export interface HistoryInfo {
  can_undo: boolean;
  can_redo: boolean;
  undo_label: string | null;
  redo_label: string | null;
  entries: string[];
}

export type DiffCounts = Record<Kind, { added: number; modified: number; deleted: number }>;

export interface DocState {
  doc_id: string;
  filename: string;
  stats: { nodes: number; ways: number; relations: number; signals: number };
  diff: DiffCounts;
  history: HistoryInfo;
  geo: Geo;
}

export type Severity = 'critical' | 'warning' | 'info';

export interface Fix {
  label: string;
  op: string;
  args: Record<string, unknown>;
}

export interface Issue {
  id: string;
  severity: Severity;
  category: 'connectivity' | 'direction' | 'junction' | 'lanes' | 'geometry' | 'data';
  title: string;
  description: string;
  refs: { kind: Kind; id: number }[];
  location: [number, number] | null;
  fix: Fix | null;
  levels: string[];
}

export interface ValidationResult {
  issues: Issue[];
  summary: {
    counts: Record<Severity, number>;
    components: number;
    levels: Record<'matsim' | 'sumo' | 'webots', 'ok' | 'risk' | 'fail'>;
  };
}

export interface SumoResult {
  available: boolean;
  ok: boolean | null;
  errors: string[];
  warnings: string[];
  warning_count?: number;
  stats?: { edges: number; junctions: number; traffic_lights: number };
  message: string;
}

export type Selection = { kind: 'way' | 'node'; id: number } | null;

export type Tool = 'select' | 'node' | 'way' | 'split' | 'merge' | 'signal' | 'crossing' | 'reverse';

export interface Op {
  op: string;
  args: Record<string, unknown>;
}

export interface LayerVisibility {
  roads: boolean;
  paths: boolean;
  rail: boolean;
  signals: boolean;
  crossings: boolean;
  stops: boolean;
  buildings: boolean;
  issues: boolean;
}
