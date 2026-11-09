/** Planar metrics on lon/lat coordinates (local equirectangular projection, city-scale accurate). */
const M_PER_DEG_LAT = 110_540;
const M_PER_DEG_LON_EQ = 111_320;

function projector(coords: number[][]) {
  const lat0 = coords.reduce((s, c) => s + c[1], 0) / Math.max(coords.length, 1);
  const kx = M_PER_DEG_LON_EQ * Math.cos((lat0 * Math.PI) / 180);
  return (c: number[]) => [c[0] * kx, c[1] * M_PER_DEG_LAT];
}

export function lengthM(coords: number[][]): number {
  const p = projector(coords);
  let s = 0;
  for (let i = 1; i < coords.length; i++) {
    const [x0, y0] = p(coords[i - 1]);
    const [x1, y1] = p(coords[i]);
    s += Math.hypot(x1 - x0, y1 - y0);
  }
  return s;
}

export function areaM2(ring: number[][]): number {
  if (ring.length < 4) return 0;
  const p = projector(ring);
  let s = 0;
  for (let i = 1; i < ring.length; i++) {
    const [x0, y0] = p(ring[i - 1]);
    const [x1, y1] = p(ring[i]);
    s += x0 * y1 - x1 * y0;
  }
  return Math.abs(s) / 2;
}

export const fmt = (n: number, digits = 0) =>
  n.toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: digits });
