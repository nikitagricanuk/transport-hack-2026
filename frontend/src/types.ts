import type { Feature, FeatureCollection, LineString, Point, Polygon } from 'geojson';

export type Kind = 'node' | 'way' | 'relation';
export type Tags = Record<string, string>;

/** OSM access tags edited as checkboxes. Keys match `backend/app/access.py`. */
export interface RoadPermissions {
  foot: boolean;
  bicycle: boolean;
  horse: boolean;
  motorcycle: boolean;
  hgv: boolean;
  psv: boolean;
  moped: boolean;
  wheelchair: boolean;
}

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
  permissions: RoadPermissions;
}

export interface NodeProps {
  stop_routes?: number[];
  id: number;
  kind: 'node';
  junction: boolean;
  category: 'signal' | 'tram_signal' | 'crossing' | 'stop' | 'tagged' | 'junction';
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
  /** newest first: what changed, who changed it and when */
  details?: HistoryEntry[];
}

export interface HistoryEntry {
  reason?: string | null;
  label: string;
  author: string | null;
  at: number;
  refs: { kind: Kind; id: number; status: 'added' | 'modified' | 'deleted'; name: string }[];
  count: number;
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

/** Dry run of a fix: what it would add, delete or change (the document is not touched). */
export interface FixPreview {
  probe?: { found:boolean;stats?:{time_s:number;length_m:number}|null }|null;
  features: Feature<LineString | Point, { kind: Kind; id: number; status: 'added' | 'deleted' | 'modified'; name: string; length_m?: number; fringe?: boolean }>[];
  counts: { added: number; deleted: number; modified: number };
  added_road_m: number;
  removed_road_m: number;
  empty: boolean;
}

export interface Issue {
  id: string;
  severity: Severity;
  category: 'connectivity' | 'direction' | 'junction' | 'lanes' | 'geometry' | 'data' | 'pedestrian';
  title: string;
  description: string;
  refs: { kind: Kind; id: number }[];
  location: [number, number] | null;
  locations?: [number, number][] | null;
  /** First option. Older replies only had this; `fixes` is the full list. */
  fix: Fix | null;
  fixes: Fix[];
  levels: string[];
  /** stable identity (title + objects), used to hide a false alarm */
  key?: string;
  /** the user marked it «Это не ошибка»: hidden from the list and the totals */
  dismissed?: boolean;
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
    /** issues the user hid as false alarms */
    dismissed?: number;
  };
}

export interface OrganizerResult {
  ok: boolean;
  status: number;
  filename: string;
  message: string;
  /** the server's reply with Russian labels */
  fields?: { label: string; value: string }[];
}

/** One kind of netconvert message, interpreted for the user. */
export interface CheckGroup {
  key: string;
  title: string;
  hint: string;
  severity: 'critical' | 'warning' | 'info';
  count: number;
  refs: { kind: Kind; id: number }[];
  examples: string[];
}

/** Seconds, WGS84 longitude/latitude, and heading clockwise from north. */
/** kind: car, bus, tram, truck, motorcycle, bicycle (SUMO runs); recordings may omit it */
export type SimVehicle = [id: string, lon: number, lat: number, angle: number, kind?: string];
export interface SimulationRecording {
  frames: { time: number; vehicles: SimVehicle[] }[];
  /** SUMO run: how many of each kind were requested and placed on the network */
  fleet?: { kind: string; label: string; requested: number; placed: number }[];
  warnings?: string[];
  /** metrics measured on the run; the full report is exported from the server */
  report?: SimulationReport;
}

export interface SimulationReport {
  summary: {
    engine: string; duration_s: number; vehicles: number; avg_on_network: number; max_on_network: number;
    completed: number; completed_share: number; avg_trip_s: number | null; avg_time_in_network_s: number;
    avg_speed_kmh: number | null; avg_delay_s: number; avg_stopped_s: number; stopped_share: number;
    roads_used: number; intensity_total: number; throughput_total: number; network_throughput: number;
    avg_load: number; max_load: number; congested: number; overloaded: number;
    max_queue: number; queued_junctions: number; veh_km: number; veh_hours: number;
    co2_kg: number; co2_g_per_km: number | null; matched_share: number;
  };
  roads: { id: number; name: string; intensity: number; throughput: number; capacity: number; load: number;
    los: string; speed_kmh: number | null; max_queue: number }[];
  junctions: { id: number; name: string; signals: string; max_queue: number; max_queue_m: number }[];
}

export interface SumoResult {
  available: boolean;
  ok: boolean | null;
  errors: string[];
  warnings: string[];
  warning_count?: number;
  stats?: { edges: number; junctions: number; traffic_lights: number };
  message: string;
  report?: { groups: CheckGroup[]; counts: { critical: number; warning: number; info: number } };
}

export type MultiSelection = { kind: 'way' | 'node'; id: number }[];

export type Selection = { kind: 'way' | 'node'; id: number } | null;

export type Tool = 'select' | 'node' | 'way' | 'split' | 'merge' | 'signal' | 'crossing' | 'reverse';

export interface Op {
  op: string;
  args: Record<string, unknown>;
}

export interface LayerVisibility {
  roadsSub?: Record<string,boolean>;
  nodes: boolean;
  tramSignals: boolean;
  roads: boolean;
  paths: boolean;
  rail: boolean;
  signals: boolean;
  crossings: boolean;
  stops: boolean;
  buildings: boolean;
  /** lawns, parks, landuse areas: a layer of its own, apart from buildings */
  zones: boolean;
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
  transit_pcu?: number;
  /** added in the editor: no observed demand */
  new?: boolean;
  needs_intensity?: boolean;
  closed?: boolean;
  detour?: boolean;
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

export interface VehicleType {
  key: string;
  label: string;
  /** share of the flow, 0..1; cars take what the others leave */
  share: number;
  /** passenger-car equivalent */
  pcu: number;
  default_pcu: number;
  custom_pcu: boolean;
  source: string;
}

export type VehicleMixInput = Record<string, { share: number; pcu?: number }>;

export interface StopLoad {
  id: number; nodes: number[]; name: string; coord: [number, number]; routes: string[];
  vehicles_per_hour: number; capacity_per_hour: number; residents: number; demand_per_hour: number;
  load: number; level: 'низкая' | 'средняя' | 'высокая' | 'нет маршрутов';
}

export interface StopAdvice {
  kind: 'gap' | 'overload' | 'no_routes' | 'spacing' | 'district';
  severity: 'warning' | 'info';
  title: string; text: string;
  coord?: [number, number]; node_id?: number; way_id?: number | null;
}

export interface StopsAnalysis {
  summary: {
    stops: number; stop_nodes: number; routes: number; residents: number; covered: number; uncovered: number;
    coverage: number | null; buildings: number; uncovered_buildings: number; by_level: Record<string, number>;
  };
  stops: StopLoad[];
  gaps: { residents: number; buildings: number; coord: [number, number]; nearest_stop_m: number | null; road_id: number | null; road_name: string }[];
  districts: { id: number; name: string; residents: number; uncovered: number; coverage: number; buildings: number }[];
  spacing: { route: string; a: string; b: string; distance_m: number; kind: 'close' | 'far' }[];
  advice: StopAdvice[];
  features: import('geojson').FeatureCollection;
  params: { radius_m: number; trips_per_resident: number; peak_share: number };
  assumptions: string[];
  sources: { label: string; url?: string }[];
}

export interface LoadState {
  settings?: {time_of_day?:string;weather?:string;truck_share?:number;bus_share?:number;vehicle_mix?:VehicleMixInput};
  vehicle_mix?: VehicleType[];
  /** p = sum of share x car equivalent */
  pcu_factor?: number;
  segments?: {id:number;ways:number[];name:string;vc:number;los:Los;length_m:number;category:string}[];
  multiplier: number;
  rows: LoadRow[];
  summary: LoadSummary;
  snapshots: SnapshotMeta[];
  closures?: Closure[];
}

export interface Closure {
  alternatives?: {ways:number[];time_s:number;length_m:number;lines:number[][][]}[];
  selected_alternative?: number;
  issues?: Issue[];
  id: number;
  name: string;
  ways: number[];
  volume: number;
  found: boolean;
  detour_s: number;
  /** vehicles/h that found a detour */
  moved?: number;
  /** a two-way road where only one direction has a way round */
  partial?: boolean;
}

export interface OdMatrix {
  zones: { id: string; name: string; residents: number; jobs: number; center: [number, number] }[];
  trips: number[][];
  shares: number[][];
  minutes: (number | null)[][];
  total: number;
  grid: number;
  trip_rate: number;
  beta: number;
}

export interface TaskCriterion {
  type: 'no_issues' | 'components_max' | 'max_vc' | 'avg_vc' | 'tag' | 'signal_at' | 'lanes_min';
  label?: string;
  [k: string]: unknown;
}

export interface Task {
  id: string;
  builtin: boolean;
  title: string;
  description: string;
  criteria: TaskCriterion[];
}

export interface TaskResult {
  task_id: string;
  passed: number;
  total: number;
  done: boolean;
  results: { label: string; ok: boolean; detail: string }[];
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

// ------------------------------------------------------- transit, signals
export interface RouteSummary {
  id: number;
  ref: string;
  name: string;
  mode: string;
  mode_label: string;
  from: string;
  to: string;
  colour: string;
  stops: number;
  frequency: number;
  custom_frequency: boolean;
}

export interface RouteDetail extends RouteSummary {
  lines: number[][][];
  stop_list: { id: number; name: string; coord: number[] }[];
}

export interface StopInfo {
  id: number;
  name: string;
  routes: RouteSummary[];
  vehicles_per_hour: number;
  residents_400m: number;
  load_label: string;
}

/** One phase of a signal program: its green and the movements (and arrow sections) it releases. */
export interface SignalPhase { name: string; green_s: number; moves: string[] }

export interface SignalPlan {
  node_id: number;
  cycle: number;
  main_share: number;
  custom: boolean;
  approaches: { way_id: number; name: string; highway: string; bearing: number; group: 'main' | 'cross';
    volume: number | null; capacity: number | null; vc: number | null }[];
  phases: { name: string; group: 'main' | 'cross' | 'arrow'; green_s: number; yellow_s: number; all_red_s?: number; moves: string[]; moves_ru: string[] }[];
  /** the phases come from an edited program (with arrows), not from cycle and split */
  program: boolean;
  effective_green: { main: number; cross: number };
  moves: Record<string, string>;
  turn_share: { left: number; right: number };
  turn_source: string;
  flow_ratio: { main: number; cross: number };
  webster: { cycle: number; main_share: number; saturated: boolean };
}
