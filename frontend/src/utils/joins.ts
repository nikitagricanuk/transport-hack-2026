import type { Geo, Selection } from '../types';

/** Roads and paths can share a junction. An area=yes plaza is not one of those. */
export function isHighwayLine(layer: string, area: string | undefined): boolean {
  return (layer === 'road' || layer === 'path') && area !== 'yes';
}

/** Node id → [lon, lat] for every node that sits on two or more highway lines. */
export function sharedHighwayNodes(geo: Geo | null): Map<number, [number, number]> {
  const counts = new Map<number, number>();
  const pos = new Map<number, [number, number]>();
  if (!geo) return pos;
  for (const feature of geo.ways.features) {
    const props = feature.properties;
    if (!isHighwayLine(props.layer, props.tags.area)) continue;
    const seen = new Set<number>();
    props.nodes.forEach((id, index) => {
      if (seen.has(id)) return;
      seen.add(id);
      counts.set(id, (counts.get(id) ?? 0) + 1);
      const coord = feature.geometry.coordinates[index];
      if (coord && !pos.has(id)) pos.set(id, [coord[0], coord[1]]);
    });
  }
  for (const [id, count] of counts) {
    if (count < 2) pos.delete(id);
  }
  return pos;
}

export interface JoinHandle {
  id: number;
  lon: number;
  lat: number;
}

/** Orange split points: shared nodes of the selected road, or the selected node itself. */
export function joinHandles(geo: Geo | null, selection: Selection): JoinHandle[] {
  if (!geo || !selection) return [];
  const shared = sharedHighwayNodes(geo);
  if (selection.kind === 'node') {
    const at = shared.get(selection.id);
    return at ? [{ id: selection.id, lon: at[0], lat: at[1] }] : [];
  }
  const way = geo.ways.features.find((feature) => feature.properties.id === selection.id);
  if (!way || !isHighwayLine(way.properties.layer, way.properties.tags.area)) return [];
  const handles: JoinHandle[] = [];
  const seen = new Set<number>();
  for (const id of way.properties.nodes) {
    if (seen.has(id)) continue;
    seen.add(id);
    const at = shared.get(id);
    if (at) handles.push({ id, lon: at[0], lat: at[1] });
  }
  return handles;
}
