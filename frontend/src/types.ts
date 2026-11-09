import type { Feature, FeatureCollection, LineString, Point, Polygon } from 'geojson';

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

/** Features one edit touched: drop every listed id, then add the returned features. */
export interface GeoPatch {
  ways: number[];
  nodes: number[];
  features: {
    ways: Feature<LineString, WayProps>[];
    areas: Feature<Polygon, WayProps>[];
    nodes: Feature<Point, NodeProps>[];
  };
}

/** A patch applied to `base`, with per-source changes the map can apply without a full reload. */
export interface GeoDiff {
  base: Geo;
  target: Geo;
  changes: Record<'ways' | 'areas' | 'nodes', { remove: number[]; add: Feature[] }>;
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
  /** server revision; collaborators' patches are applied strictly in this order */
  rev: number;
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
  locations?: [number, number][] | null;
  /** First option. Older replies only had this; `fixes` is the full list. */
  fix: Fix | null;
  fixes: Fix[];
  levels: string[];
}

export interface ValidationResult {
  issues: Issue[];
  summary: {
    counts: Record<Severity, number>;
    components: number;
    strong_components: number;
    outside_strong: number;
    levels: Record<'matsim' | 'sumo' | 'webots', 'ok' | 'risk' | 'fail'>;
    level_notes: Record<'matsim' | 'sumo' | 'webots', string>;
  };
}

export interface OrganizerResult {
  ok: boolean;
  status: number;
  filename: string;
  message: string;
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

// ------------------------------------------------------------ load model
export type Los = 'A' | 'B' | 'C' | 'D' | 'E' | 'F';

export interface LoadRow {
  id: number;
  name: string;
  highway: string;
  lanes: number;
  length_m: number;
  speed: number;
  capacity: number;
  volume: number;
  base_volume: number;
  custom: boolean;
  vc: number;
  los: Los;
  t0_s: number;
  t_s: number;
}

export interface LoadSummary {
  roads: number;
  length_km: number;
  veh_km: number;
  veh_hours: number;
  delay_hours: number;
  avg_vc: number;
  congested_km: number;
  overloaded: number;
  by_los: Record<Los, number>;
}

export interface SnapshotMeta {
  id: string;
  name: string;
  created: string;
  multiplier: number;
  summary: LoadSummary;
}

export interface LoadState {
  multiplier: number;
  rows: LoadRow[];
  summary: LoadSummary;
  snapshots: SnapshotMeta[];
}

export interface Comparison {
  a: { id: string; name: string; created: string };
  b: { id: string; name: string; created: string };
  totals: Record<string, { before: number; after: number; delta: number }>;
  los_before: Record<Los, number>;
  los_after: Record<Los, number>;
  changed: {
    id: number; name: string; highway: string; status: 'added' | 'removed' | 'changed';
    lanes_before: number | null; lanes_after: number | null;
    capacity_before: number | null; capacity_after: number | null;
    volume_before: number | null; volume_after: number | null;
    vc_before: number | null; vc_after: number | null;
    los_before: Los | null; los_after: Los | null;
  }[];
}

// --------------------------------------------------------- before / after
export interface ChangeEntry {
  kind: Kind;
  id: number;
  status: 'added' | 'modified' | 'deleted';
  name: string;
  highway: string;
  tags: { key: string; before: string | null; after: string | null }[];
  geometry: boolean;
  before_coords?: number[][] | number[] | null;
  after_coords?: number[][] | number[] | null;
}

export interface ChangesResult {
  changes: ChangeEntry[];
  before: ValidationResult['summary'];
  after: ValidationResult['summary'];
}

// ---------------------------------------------------------------- report
export interface ReportData {
  title: string;
  filename: string;
  generated: string;
  stats: {
    classes: { highway: string; label: string; count: number; length_km: number; lane_km: number }[];
    road_km: number;
    lane_km: number;
    buildings: { count: number; footprint_m2: number; floor_area_m2: number; with_levels: number };
    signals: number;
    crossings: number;
    stops: number;
    bridges: number;
  };
  validation: {
    summary: ValidationResult['summary'];
    matrix: { category: string; critical: number; warning: number; info: number }[];
  };
  changes: { added: number; modified: number; deleted: number };
  load: { multiplier: number; summary: LoadSummary };
  comparison: Comparison | null;
}

// ---------------------------------------------------------- collaboration
export interface Peer {
  id: string;
  name: string;
  color: string;
  cursor?: [number, number] | null;
  selection?: Selection;
  tool?: string;
}
