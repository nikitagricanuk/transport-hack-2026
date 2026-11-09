import type { ExpressionSpecification, FilterSpecification } from 'maplibre-gl';

/**
 * One colour per highway class, kept from the merged road-style palette.
 * Links are a lighter shade of their parent and are drawn dashed (see MapView)
 * so a ramp is not the same mark as the main road.
 * `casing` and `casingExtra` extend that palette: the outline is its own colour
 * and thickness, so the class is readable before the fill is wide enough to see.
 * Paths are not in this table: they live on their own layers with their own dash.
 */
export interface RoadMark {
  highway: string;
  fill: string;
  casing: string;
  scale: number;
  /** Extra casing pixels added at every zoom stop. */
  casingExtra: number;
  link?: boolean;
}

export const ROAD_MARKS: RoadMark[] = [
  { highway: 'motorway', fill: '#DC2626', casing: '#7F1D1D', scale: 1.45, casingExtra: 3.6 },
  { highway: 'motorway_link', fill: '#F87171', casing: '#B91C1C', scale: 0.9, casingExtra: 2.6, link: true },
  { highway: 'trunk', fill: '#C2410C', casing: '#7C2D12', scale: 1.28, casingExtra: 3.2 },
  { highway: 'trunk_link', fill: '#FB923C', casing: '#C2410C', scale: 0.82, casingExtra: 2.4, link: true },
  { highway: 'primary', fill: '#D97706', casing: '#92400E', scale: 1.16, casingExtra: 2.8 },
  { highway: 'primary_link', fill: '#FBBF24', casing: '#B45309', scale: 0.76, casingExtra: 2.2, link: true },
  { highway: 'secondary', fill: '#CA8A04', casing: '#854D0E', scale: 1.05, casingExtra: 2.5 },
  { highway: 'secondary_link', fill: '#FACC15', casing: '#A16207', scale: 0.7, casingExtra: 2, link: true },
  { highway: 'tertiary', fill: '#64748B', casing: '#334155', scale: 0.96, casingExtra: 2.2 },
  { highway: 'tertiary_link', fill: '#94A3B8', casing: '#475569', scale: 0.64, casingExtra: 1.8, link: true },
  { highway: 'unclassified', fill: '#FDE68A', casing: '#78350F', scale: 0.9, casingExtra: 2 },
  { highway: 'residential', fill: '#FFFFFF', casing: '#6B7280', scale: 0.88, casingExtra: 2 },
  { highway: 'living_street', fill: '#4ADE80', casing: '#166534', scale: 0.74, casingExtra: 1.8 },
  { highway: 'service', fill: '#7C3AED', casing: '#4C1D95', scale: 0.62, casingExtra: 1.3 },
  { highway: 'road', fill: '#F9A8D4', casing: '#9D174D', scale: 0.9, casingExtra: 2 },
];

const FALLBACK_FILL = '#F9A8D4';
const FALLBACK_CASING = '#9D174D';
const FALLBACK_SCALE = 0.85;
const FALLBACK_CASING_EXTRA = 2;

function matchHighway(value: (mark: RoadMark) => string | number, fallback: string | number): ExpressionSpecification {
  const expr: (string | number | string[])[] = ['match', ['get', 'highway']];
  for (const mark of ROAD_MARKS) expr.push(mark.highway, value(mark));
  expr.push(fallback);
  return expr as unknown as ExpressionSpecification;
}

export function roadColor(): ExpressionSpecification {
  return matchHighway((mark) => mark.fill, FALLBACK_FILL);
}

/** Relative width. Service stays narrower than a residential street, a motorway wider. */
export function classScale(): ExpressionSpecification {
  return matchHighway((mark) => mark.scale, FALLBACK_SCALE);
}

/** Darker outline of the same class. Bridges keep a near-black casing on top. */
export function casingColor(): ExpressionSpecification {
  return ['case', ['get', 'bridge'], '#1F2937', matchHighway((mark) => mark.casing, FALLBACK_CASING)];
}

/** Casing thickness. A bridge adds a little so the deck reads above the fill. */
export function casingWidthExtra(): ExpressionSpecification {
  return ['+', matchHighway((mark) => mark.casingExtra, FALLBACK_CASING_EXTRA), ['case', ['get', 'bridge'], 1.6, 0]];
}

/** Normal colour, or green → red while a load scenario is painted via feature-state. */
export function roadLineColor(): ExpressionSpecification {
  return [
    'case',
    ['<', ['to-number', ['coalesce', ['feature-state', 'vc'], -1]], 0],
    roadColor(),
    ['step', ['to-number', ['coalesce', ['feature-state', 'vc'], 0]],
      '#22C55E', 0.6, '#EAB308', 0.85, '#F97316', 1.0, '#DC2626'],
  ];
}

export const LINK_HIGHWAYS = ROAD_MARKS.filter((mark) => mark.link).map((mark) => mark.highway);

export function linkFilter(): FilterSpecification {
  return ['all', ['==', ['get', 'layer'], 'road'], ['in', ['get', 'highway'], ['literal', [...LINK_HIGHWAYS]]]];
}

export function solidRoadFilter(): FilterSpecification {
  return ['all', ['==', ['get', 'layer'], 'road'], ['!', ['in', ['get', 'highway'], ['literal', [...LINK_HIGHWAYS]]]]];
}

export interface PathStyle {
  id: string;
  /** null = every other path class (steps-like leftovers included via the fallback layer) */
  highways: string[] | null;
  color: string;
  width: number;
  /** omitted = solid. Each pair is dash, gap, in line widths. */
  dash?: [number, number];
}

/**
 * Pedestrian and footway used to share one grey dash. Each class is its own mark,
 * including the non-drivable highway classes (busway, construction, raceway, escape)
 * that the graph draws on the path layer.
 */
export const PATH_STYLES: PathStyle[] = [
  { id: 'path-pedestrian', highways: ['pedestrian'], color: '#DB2777', width: 3.2 },
  { id: 'path-footway', highways: ['footway'], color: '#475569', width: 1.6, dash: [1.6, 1.2] },
  { id: 'path-cycleway', highways: ['cycleway'], color: '#1D4ED8', width: 2.3, dash: [4, 1.6] },
  { id: 'path-path', highways: ['path'], color: '#15803D', width: 1.7, dash: [0.8, 1.3] },
  { id: 'path-steps', highways: ['steps'], color: '#EA580C', width: 2.1, dash: [0.7, 0.9] },
  { id: 'path-track', highways: ['track'], color: '#92400E', width: 2, dash: [3, 1.8] },
  { id: 'path-bridleway', highways: ['bridleway'], color: '#4D7C0F', width: 1.8, dash: [6, 1.5] },
  { id: 'path-corridor', highways: ['corridor'], color: '#7C3AED', width: 1.5, dash: [2, 2] },
  { id: 'path-busway', highways: ['busway', 'bus_guideway'], color: '#0891B2', width: 3.4 },
  { id: 'path-construction', highways: ['construction', 'proposed'], color: '#4B5563', width: 2.4, dash: [5, 3] },
  { id: 'path-raceway', highways: ['raceway'], color: '#BE123C', width: 2.6 },
  { id: 'path-escape', highways: ['escape'], color: '#B45309', width: 1.8, dash: [1.2, 1.4] },
  { id: 'path-other', highways: null, color: '#9CA3AF', width: 1.4, dash: [2, 1.5] },
];

export const PATH_LAYER_IDS = PATH_STYLES.map((p) => p.id);

export function pathFilter(style: PathStyle): FilterSpecification {
  const base: FilterSpecification = ['==', ['get', 'layer'], 'path'];
  const known = PATH_STYLES.flatMap((p) => p.highways ?? []);
  if (style.highways) {
    return ['all', base, ['in', ['get', 'highway'], ['literal', [...style.highways]]]];
  }
  return ['all', base, ['!', ['in', ['get', 'highway'], ['literal', known]]]];
}

/** Colour plus dash, so two classes can share neither and still be told apart. */
export function pathSignature(style: PathStyle): string {
  return `${style.color}|${style.dash ? style.dash.join(',') : 'solid'}|${style.width}`;
}
