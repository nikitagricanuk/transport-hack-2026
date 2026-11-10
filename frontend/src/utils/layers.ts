import type { LayerVisibility } from '../types';

/** Graph-issue categories. `road` ones clear as soon as the roads layer is turned off. */
export const ISSUE_FILTERS = [
  { key: 'connectivity', label: 'Связность', road: true },
  { key: 'direction', label: 'Направление', road: true },
  { key: 'junction', label: 'Перекрёстки', road: true },
  { key: 'lanes', label: 'Полосы', road: true },
  { key: 'geometry', label: 'Геометрия', road: true },
  { key: 'data', label: 'Данные', road: true },
] as const;

export type IssueFilterKey = (typeof ISSUE_FILTERS)[number]['key'];
export type IssueFilters = Record<IssueFilterKey, boolean>;

/** Roads and the road-error filters start off. Other map layers stay on. */
export const DEFAULT_LAYERS: LayerVisibility = {
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
    data: false,
  };
}

export function allLayersOn(): LayerVisibility {
  return {
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
    data: true,
  };
}

/** Every value false: the second press of «Выбрать всё» clears all checkboxes. */
export function allOff<T extends object>(values: T): T {
  return Object.fromEntries(Object.keys(values).map((k) => [k, false])) as T;
}

export function allOn(values: object): boolean {
  return Object.values(values).every(Boolean);
}

export function anyIssueFilter(filters: IssueFilters): boolean {
  return ISSUE_FILTERS.some((item) => filters[item.key]);
}

/**
 * Turning the roads layer off also clears the issues layer and every road-related
 * error filter in the same update. Turning the issues layer on while every filter
 * is off selects those filters, so the checkbox is not an empty map layer.
 */
export function applyLayerChange(
  prevLayers: LayerVisibility,
  prevFilters: IssueFilters,
  nextLayers: LayerVisibility,
): { layers: LayerVisibility; filters: IssueFilters } {
  if (prevLayers.roads && !nextLayers.roads) {
    const filters = { ...prevFilters };
    for (const item of ISSUE_FILTERS) {
      if (item.road) filters[item.key] = false;
    }
    return { layers: { ...nextLayers, issues: false }, filters };
  }
  if (!prevLayers.issues && nextLayers.issues && !anyIssueFilter(prevFilters)) {
    return { layers: nextLayers, filters: allIssueFiltersOn() };
  }
  return { layers: nextLayers, filters: prevFilters };
}

export function issueVisible(filters: IssueFilters, category: string): boolean {
  return ISSUE_FILTERS.some((item) => item.key === category && filters[item.key]);
}
