import type { LayerVisibility } from '../types';

/** Independent graph issue filters. */
export const ISSUE_FILTERS = [
  { key: 'connectivity', label: 'Связность', road: true },
  { key: 'direction', label: 'Направление', road: true },
  { key: 'junction', label: 'Перекрёстки', road: true },
  { key: 'lanes', label: 'Полосы', road: true },
  { key: 'geometry', label: 'Геометрия', road: true },
  { key: 'pedestrian', label: 'Пешеходы', road: false },
  { key: 'data', label: 'Данные', road: true },
] as const;

export type IssueFilterKey = (typeof ISSUE_FILTERS)[number]['key'];
export type IssueFilters = Record<IssueFilterKey, boolean>;

/** Roads and the road-error filters start off. Other map layers stay on. */
export const DEFAULT_LAYERS: LayerVisibility = {
  nodes: true,
  tramSignals: true,
  roads: false,
  paths: true,
  rail: true,
  signals: true,
  crossings: true,
  stops: true,
  buildings: true,
  issues: false,
};

export function defaultIssueFilters(): IssueFilters {
  return {
    connectivity: false,
    direction: false,
    junction: false,
    lanes: false,
    geometry: false,
    pedestrian: false,
    data: false,
  };
}

export function allLayersOn(): LayerVisibility {
  return {
    roadsSub: Object.fromEntries(SUBGROUPS.map(([k])=>[k,true])),
    nodes: true,
    tramSignals: true,
    roads: true,
    paths: true,
    rail: true,
    signals: true,
    crossings: true,
    stops: true,
    buildings: true,
    issues: true,
  };
}

export function allIssueFiltersOn(): IssueFilters {
  return {
    connectivity: true,
    direction: true,
    junction: true,
    lanes: true,
    geometry: true,
    pedestrian: true,
    data: true,
  };
}

/** Every value false: the second press of «Выбрать всё» clears all checkboxes. */
export function allOff<T extends object>(values: T): T {
  return Object.fromEntries(Object.entries(values).map(([k,v]) => [k, typeof v === 'object' && v ? allOff(v) : false])) as T;
}

export function allOn(values: object): boolean {
  return Object.values(values).every(v => typeof v === 'object' && v ? allOn(v) : Boolean(v));
}

export function anyIssueFilter(filters: IssueFilters): boolean {
  return ISSUE_FILTERS.some((item) => filters[item.key]);
}

/** Turning issues on with no filters selects every category. */
export function applyLayerChange(
  prevLayers: LayerVisibility,
  prevFilters: IssueFilters,
  nextLayers: LayerVisibility,
): { layers: LayerVisibility; filters: IssueFilters } {
  if (!prevLayers.issues && nextLayers.issues && !anyIssueFilter(prevFilters)) {
    return { layers: nextLayers, filters: allIssueFiltersOn() };
  }
  return { layers: nextLayers, filters: prevFilters };
}

export function issueVisible(filters: IssueFilters, category: string): boolean {
  return ISSUE_FILTERS.some((item) => item.key === category && filters[item.key]);
}

export const SUBGROUPS = [
  ['motorway','Автомагистрали'],['trunk','Скоростные'],['primary','Магистральные'],['secondary','Районные'],
  ['tertiary','Местные'],['residential','Жилые'],['service','Проезды'],['unclassified','Прочие'],
  ['motorway_link','Съезды автомагистралей'],['trunk_link','Съезды скоростных'],['primary_link','Магистральные съезды'],
  ['secondary_link','Районные съезды'],['tertiary_link','Местные съезды'],
  ['footway','Тротуары'],['path','Дорожки'],['steps','Лестницы'],['pedestrian','Пешеходные улицы'],['cycleway','Велодорожки'],['track','Грунтовые дорожки'],
] as const;
/** Only changed boolean flags generate layout operations, never GeoJSON. */
export function layerVisibilityOps(prev: LayerVisibility, next: LayerVisibility) {
  return Object.entries(next).filter(([k,v])=>typeof v==='boolean' && v!==prev[k as keyof LayerVisibility]).map(([key,visible])=>({key,visible}));
}
