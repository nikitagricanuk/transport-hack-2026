import { lengthM } from './geom.ts';
/** Clip a display polyline between the two nearest projected locations. */
export function segmentGeometry(
  coords: number[][],
  start: { lat: number; lon: number },
  end: { lat: number; lon: number },
) {
  if (coords.length < 2) return { coordinates: [], length_m: 0 };
  const kx = 111320 * Math.cos((coords[0][1] * Math.PI) / 180),
    ky = 110540;
  const project = (p: { lat: number; lon: number }) => {
    let best = { distance: Infinity, index: 0, fraction: 0, point: coords[0] };
    for (let i = 0; i < coords.length - 1; i++) {
      const a = coords[i],
        b = coords[i + 1],
        dx = (b[0] - a[0]) * kx,
        dy = (b[1] - a[1]) * ky;
      const fraction = Math.max(
        0,
        Math.min(1, ((p.lon - a[0]) * kx * dx + (p.lat - a[1]) * ky * dy) / (dx * dx + dy * dy || 1)),
      );
      const point = [a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])];
      const distance = Math.hypot((p.lon - point[0]) * kx, (p.lat - point[1]) * ky);
      if (distance < best.distance) best = { distance, index: i, fraction, point };
    }
    return best;
  };
  const [a, b] = [project(start), project(end)].sort((a, b) => a.index + a.fraction - b.index - b.fraction);
  const coordinates = [a.point, ...coords.slice(a.index + 1, b.index + 1), b.point];
  return { coordinates, length_m: lengthM(coordinates) };
}
