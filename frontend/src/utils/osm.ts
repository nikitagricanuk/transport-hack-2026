import type { Op, Tags } from '../types';

export const HIGHWAY_CLASSES: { value: string; label: string }[] = [
  { value: 'motorway', label: 'motorway — автомагистраль' },
  { value: 'trunk', label: 'trunk — скоростная' },
  { value: 'primary', label: 'primary — магистральная' },
  { value: 'secondary', label: 'secondary — районная' },
  { value: 'tertiary', label: 'tertiary — местная' },
  { value: 'residential', label: 'residential — жилая улица' },
  { value: 'unclassified', label: 'unclassified' },
  { value: 'living_street', label: 'living_street — жилая зона' },
  { value: 'service', label: 'service — проезд' },
  { value: 'primary_link', label: 'primary_link — съезд' },
  { value: 'secondary_link', label: 'secondary_link — съезд' },
  { value: 'pedestrian', label: 'pedestrian — пешеходная' },
  { value: 'footway', label: 'footway — тротуар' },
];

/** OSM `surface=*` values. The label is Russian; the stored value stays the OSM token. */
export const SURFACES: { value: string; label: string }[] = [
  { value: 'asphalt', label: 'асфальт' },
  { value: 'concrete', label: 'бетон' },
  { value: 'paving_stones', label: 'тротуарная плитка' },
  { value: 'sett', label: 'брусчатка' },
  { value: 'compacted', label: 'уплотнённое покрытие' },
  { value: 'gravel', label: 'гравий' },
  { value: 'unpaved', label: 'без покрытия' },
];
export const SPEEDS = [20, 30, 40, 50, 60, 80];

export const KEY_SUGGESTIONS = [
  'highway', 'name', 'lanes', 'lanes:forward', 'lanes:backward', 'oneway', 'maxspeed', 'surface',
  'turn:lanes', 'turn:lanes:forward', 'turn:lanes:backward', 'bridge', 'tunnel', 'layer', 'junction',
  'crossing', 'traffic_signals', 'traffic_signals:direction', 'access', 'motor_vehicle', 'psv', 'sidewalk',
  'lit', 'ref', 'width', 'railway', 'public_transport', 'bus', 'tram', 'smoothness', 'parking:lane:both',
];

export const VALUE_SUGGESTIONS: Record<string, string[]> = {
  highway: HIGHWAY_CLASSES.map((h) => h.value).concat(['traffic_signals', 'crossing', 'bus_stop', 'stop', 'give_way']),
  oneway: ['yes', 'no', '-1'],
  bridge: ['yes', 'viaduct', 'no'],
  tunnel: ['yes', 'no'],
  layer: ['-1', '1', '2'],
  surface: SURFACES.map((s) => s.value),
  junction: ['roundabout', 'circular'],
  crossing: ['marked', 'traffic_signals', 'uncontrolled', 'unmarked'],
  'traffic_signals:direction': ['forward', 'backward', 'both'],
  access: ['yes', 'no', 'private', 'destination'],
  maxspeed: SPEEDS.map(String),
  'turn:lanes': ['left|through|right', 'left;through|through;right', 'through|through;right'],
  'turn:lanes:forward': ['left;through|through;right', 'left|through|through;right'],
  'turn:lanes:backward': ['left;through|through;right', 'left|through|through;right'],
};

export type OnewayMode = 'no' | 'yes' | '-1';

export function onewayMode(tags: Tags): OnewayMode {
  const v = tags.oneway;
  if (v === 'yes' || v === 'true' || v === '1') return 'yes';
  if (v === '-1' || v === 'reverse') return '-1';
  return 'no';
}

export function laneCounts(tags: Tags): { forward: number; backward: number } {
  const mode = onewayMode(tags);
  const lanes = parseInt(tags.lanes ?? '', 10);
  const fw = parseInt(tags['lanes:forward'] ?? '', 10);
  const bw = parseInt(tags['lanes:backward'] ?? '', 10);
  if (mode !== 'no') return { forward: Number.isFinite(lanes) ? lanes : 1, backward: 0 };
  if (Number.isFinite(fw) && Number.isFinite(bw)) return { forward: fw, backward: bw };
  if (Number.isFinite(lanes)) {
    const f = Math.ceil(lanes / 2);
    return { forward: f, backward: Math.max(1, lanes - f) };
  }
  return { forward: 1, backward: 1 };
}

const TURN_GLYPH: Record<string, string> = {
  left: '↰', slight_left: '↖', sharp_left: '↰', through: '↑', right: '↱', slight_right: '↗',
  sharp_right: '↱', reverse: '↶', merge_to_left: '↑', merge_to_right: '↑', none: '↑', '': '↑',
};

export function turnGlyphs(value: string | undefined, count: number): string[] {
  const parts = value ? value.split('|') : [];
  return Array.from({ length: count }, (_, i) => {
    const p = parts[i];
    if (p === undefined) return '·';
    return p.split(';').map((t) => TURN_GLYPH[t.trim()] ?? '?').join('');
  });
}

export interface RoadTemplate {
  key: string;
  name: string;
  sub: string;
  forward: number;
  backward: number;
  oneway: OnewayMode;
  tags: Tags;
}

export const TEMPLATES: RoadTemplate[] = [
  { key: '1', name: 'Магистраль 2+2', sub: 'primary · 60 км/ч', forward: 2, backward: 2, oneway: 'no',
    tags: { highway: 'primary', maxspeed: '60', surface: 'asphalt' } },
  { key: '2', name: 'Улица 1+1', sub: 'secondary · 40 км/ч', forward: 1, backward: 1, oneway: 'no',
    tags: { highway: 'secondary', maxspeed: '40', surface: 'asphalt' } },
  { key: '3', name: 'Односторонняя, 2', sub: 'oneway · 40 км/ч', forward: 2, backward: 0, oneway: 'yes',
    tags: { highway: 'tertiary', maxspeed: '40', surface: 'asphalt' } },
  { key: '4', name: 'Местный проезд', sub: 'service · 20 км/ч', forward: 1, backward: 1, oneway: 'no',
    tags: { highway: 'service', maxspeed: '20' } },
];

export function templateOps(wayId: number, t: RoadTemplate): Op[] {
  return [
    { op: 'set_tags', args: { kind: 'way', id: wayId, tags: t.tags } },
    { op: 'set_lanes', args: { way_id: wayId, forward: t.forward, backward: t.backward, oneway: t.oneway } },
  ];
}
