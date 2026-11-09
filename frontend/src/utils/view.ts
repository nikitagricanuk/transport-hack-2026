/** Camera the map opens with, before a file is fitted. Irkutsk. */
export const DEFAULT_CENTER: [number, number] = [104.28, 52.28];
export const DEFAULT_ZOOM = 15;

export type DefaultCamera =
  | { kind: 'fit'; bounds: [[number, number], [number, number]]; bearing: 0; pitch: 0 }
  | { kind: 'ease'; center: [number, number]; zoom: number; bearing: 0; pitch: 0 };

/**
 * North-up, no tilt, and the zoom the editor treats as default:
 * the loaded area when a file is open, otherwise the initial city view.
 * `bounds` is `[minlat, minlon, maxlat, maxlon]`.
 */
export function defaultCamera(bounds: [number, number, number, number] | null): DefaultCamera {
  if (bounds) {
    const [minLat, minLon, maxLat, maxLon] = bounds;
    return { kind: 'fit', bounds: [[minLon, minLat], [maxLon, maxLat]], bearing: 0, pitch: 0 };
  }
  return { kind: 'ease', center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, bearing: 0, pitch: 0 };
}
