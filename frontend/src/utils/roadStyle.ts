import type { ExpressionSpecification, FilterSpecification } from 'maplibre-gl';

/**
 * One colour per highway class. Links are a lighter shade of their parent and
 * are drawn dashed (see MapView) so a ramp is not the same mark as the main road.
 * Paths are not in this match: they live on their own layers with their own dash.
 */
export function roadColor(): ExpressionSpecification {
  return [
    'match', ['get', 'highway'],
    'motorway', '#DC2626',
    'motorway_link', '#F87171',
    'trunk', '#C2410C',
    'trunk_link', '#FB923C',
    'primary', '#D97706',
    'primary_link', '#FBBF24',
    'secondary', '#CA8A04',
    'secondary_link', '#FACC15',
    'tertiary', '#64748B',
    'tertiary_link', '#94A3B8',
    'unclassified', '#FDE68A',
    'residential', '#FFFFFF',
    'living_street', '#4ADE80',
    'service', '#7C3AED',
    'road', '#F9A8D4',
    '#F9A8D4',
  ];
}

/** Relative width. Service stays narrower than a residential street, a motorway wider. */
export function classScale(): ExpressionSpecification {
  return [
    'match', ['get', 'highway'],
    'motorway', 1.45,
    'trunk', 1.28,
    'primary', 1.16,
    'secondary', 1.05,
    'tertiary', 0.96,
    'unclassified', 0.9,
    'residential', 0.88,
    'living_street', 0.74,
    'service', 0.62,
    'road', 0.9,
    'motorway_link', 0.9,
    'trunk_link', 0.82,
    'primary_link', 0.76,
    'secondary_link', 0.7,
    'tertiary_link', 0.64,
    0.85,
  ];
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

export const LINK_HIGHWAYS = [
  'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link',
] as const;

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

/** Pedestrian and footway used to share one grey dash. Each class is its own mark. */
export const PATH_STYLES: PathStyle[] = [
  { id: 'path-pedestrian', highways: ['pedestrian'], color: '#DB2777', width: 3.2 },
  { id: 'path-footway', highways: ['footway'], color: '#475569', width: 1.6, dash: [1.6, 1.2] },
  { id: 'path-cycleway', highways: ['cycleway'], color: '#1D4ED8', width: 2.3, dash: [4, 1.6] },
  { id: 'path-path', highways: ['path'], color: '#15803D', width: 1.7, dash: [0.8, 1.3] },
  { id: 'path-steps', highways: ['steps'], color: '#EA580C', width: 2.1, dash: [0.7, 0.9] },
  { id: 'path-track', highways: ['track'], color: '#92400E', width: 2, dash: [3, 1.8] },
  { id: 'path-bridleway', highways: ['bridleway'], color: '#4D7C0F', width: 1.8, dash: [6, 1.5] },
  { id: 'path-corridor', highways: ['corridor'], color: '#7C3AED', width: 1.5, dash: [2, 2] },
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
