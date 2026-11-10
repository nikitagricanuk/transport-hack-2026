import type { FeatureCollection, Polygon } from 'geojson';
import type { Geo } from '../types';

/** Decorative tree volumes in metres; never inserted into the editable OSM document. */
export function buildTrees(geo: Geo): FeatureCollection<Polygon> {
  const features: FeatureCollection<Polygon>['features'] = [];
  const inside = (p: number[], ring: number[][]) => {
    let hit = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) hit = !hit;
    }
    return hit;
  };
  for (const area of geo.areas.features) {
    const t = area.properties.tags;
    if (!(['grass', 'forest', 'meadow', 'village_green', 'recreation_ground'].includes(t.landuse)
      || ['wood', 'scrub', 'grassland'].includes(t.natural) || ['park', 'garden'].includes(t.leisure))) continue;
    const rings = area.geometry.coordinates;
    const ring = rings[0];
    if (!ring.length) continue;
    const xs = ring.map(p => p[0]), ys = ring.map(p => p[1]);
    const west = Math.min(...xs), east = Math.max(...xs), south = Math.min(...ys), north = Math.max(...ys);
    const dx = 20 / (111320 * Math.cos(south * Math.PI / 180)), dy = 20 / 110540;
    // Bound work even for very large forests; retain a stable geographic grid.
    const stride = Math.max(1, Math.ceil(Math.sqrt((east - west) * (north - south) / (dx * dy) / 1500)));
    for (let y = Math.ceil(south / (dy * stride)); y * dy * stride < north; y++) {
      for (let x = Math.ceil(west / (dx * stride)); x * dx * stride < east; x++) {
        const seed = Math.abs(Math.sin(x * 12.9898 + y * 78.233));
        if (seed < 0.35) continue;
        const p = [(x + (seed - 0.5) * 0.6) * dx * stride, (y + (seed * 7 % 1 - 0.5) * 0.6) * dy * stride];
        if (!inside(p, ring) || rings.slice(1).some(hole => inside(p, hole))) continue;
        const size = 0.85 + seed * 0.5;
        // A trunk and three stacked, narrowing crown volumes, in real metres.
        // Each silhouette is checked too: crowns cannot spill outside the grass.
        const parts = [
          { radius: 0.35, base: 0, height: 3, color: '#79624B' },
          { radius: 2.9, base: 2.2, height: 4.4, color: '#4D7853' },
          { radius: 3.3, base: 3.6, height: 6, color: '#628E5B' },
          { radius: 2.3, base: 5.6, height: 7.5, color: '#7BA46A' },
        ].map(part => {
          const crown = Array.from({ length: 10 }, (_, i) => {
            const angle = i * Math.PI / 5 + seed * Math.PI;
            const radius = part.radius * size * (1 + 0.1 * Math.sin(i * 2.4 + seed * 6));
            return [p[0] + Math.cos(angle) * radius / (111320 * Math.cos(p[1] * Math.PI / 180)),
              p[1] + Math.sin(angle) * radius / 110540];
          });
          crown.push([...crown[0]]);
          return { type: 'Feature' as const, geometry: { type: 'Polygon' as const, coordinates: [crown] },
            properties: { base: part.base * size, height: part.height * size, color: part.color } };
        });
        if (parts.some(part => part.geometry.coordinates[0].some(c => !inside(c, ring) || rings.slice(1).some(hole => inside(c, hole))))) continue;
        features.push(...parts);
        if (features.length >= 16000) return { type: 'FeatureCollection', features };
      }
    }
  }
  return { type: 'FeatureCollection', features };
}

/** Closest street tangent and signed direction from the model to the carriageway. */
function streetFrame(geo: Geo, lon: number, lat: number) {
  const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
  let angle = 0, nearest = 60, side = 0;
  for (const road of geo.ways.features) {
    if (road.properties.layer !== 'road') continue;
    const coords = road.geometry.coordinates;
    for (let i = 1; i < coords.length; i++) {
      const ax = (coords[i-1][0]-lon)*kx, ay = (coords[i-1][1]-lat)*ky;
      const dx = (coords[i][0]-coords[i-1][0])*kx, dy = (coords[i][1]-coords[i-1][1])*ky;
      const length = dx*dx+dy*dy;
      if (!length) continue;
      const t = Math.max(0, Math.min(1, -(ax*dx+ay*dy)/length));
      const x = ax+t*dx, y = ay+t*dy, distance = Math.hypot(x,y);
      if (distance < nearest) {
        nearest = distance;
        angle = Math.atan2(dy,dx);
        side = -Math.sin(angle)*x + Math.cos(angle)*y;
      }
    }
  }
  return { angle, side };
}

function modelBox(lon: number, lat: number, angle: number, nodeId: number, scale = 1) {
  const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
  return (x: number, y: number, w: number, d: number, base: number, height: number, color: string) => {
    const ring = [[x-w/2,y-d/2],[x+w/2,y-d/2],[x+w/2,y+d/2],[x-w/2,y+d/2],[x-w/2,y-d/2]]
      .map(([px,py]) => [lon+scale*(px*Math.cos(angle)-py*Math.sin(angle))/kx,
        lat+scale*(px*Math.sin(angle)+py*Math.cos(angle))/ky]);
    return { type: 'Feature' as const, geometry: { type: 'Polygon' as const, coordinates: [ring] },
      properties: { base: base*scale, height: height*scale, color, nodeId } };
  };
}

/** Small schematic shelters, aligned with the nearest street. No models on stop_positions. */
export function buildBusStops(geo: Geo): FeatureCollection<Polygon> {
  const features: FeatureCollection<Polygon>['features'] = [];
  const placed: number[][] = [];
  for (const node of geo.nodes.features) {
    const tags = node.properties.tags;
    if (node.properties.category !== 'stop' || tags.public_transport === 'stop_position'
      || tags.railway === 'tram_stop' || tags.bus === 'no'
      || !(tags.highway === 'bus_stop' || tags.bus === 'yes')) continue;
    const [lon, lat] = node.geometry.coordinates;
    const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
    if (placed.some(p => Math.hypot((p[0] - lon) * kx, (p[1] - lat) * ky) < 8)) continue;
    placed.push([lon, lat]);
    const street = streetFrame(geo, lon, lat);
    // The bench back is +Y; the open front (-Y) must face the street.
    // Flip by the actual street side, independently of OSM way direction.
    const angle = street.angle + (street.side > 0 ? Math.PI : 0);
    const makeBox = modelBox(lon, lat, angle, node.properties.id, 1.3);
    const box = (...args: Parameters<typeof makeBox>) => { features.push(makeBox(...args)); };
    // A recognisable stop pole remains even where shelter=no is explicitly mapped.
    box(2.8, 0, 0.12, 0.12, 0, 3.2, '#637787');
    box(2.8, 0, 0.65, 0.16, 2.5, 3.2, '#267AA6');
    if (tags.shelter === 'no') continue;
    box(0, 0, 4.6, 2, 2.65, 2.9, '#3D6474');
    for (const x of [-1.95, 1.95]) for (const y of [-0.7, 0.7]) box(x, y, 0.13, 0.13, 0, 2.65, '#647A82');
    box(0, 0.55, 2.8, 0.48, 0.42, 0.56, '#AE8961');
    box(0, 0.75, 2.8, 0.12, 0.56, 1.05, '#AE8961');
  }
  return { type: 'FeatureCollection', features };
}

/** Decorative volumes stay out of the neighbourhood overview. */
export const SCENERY_MINZOOM = 17;

/** Static schematic traffic lights, not a representation of live signal phases. */
export function buildTrafficLights(geo: Geo): FeatureCollection<Polygon> {
  const features: FeatureCollection<Polygon>['features'] = [];
  for (const node of geo.nodes.features) {
    const tags = node.properties.tags;
    if (node.properties.category !== 'signal' && tags.highway !== 'traffic_signals'
      && tags.crossing !== 'traffic_signals') continue;
    if (node.properties.category === 'tram_signal') continue;
    const [lon, lat] = node.geometry.coordinates;
    const street = streetFrame(geo, lon, lat);
    // Face approaching traffic along the nearest street. Honour mapped direction.
    const angle = street.angle - Math.PI/2 + (tags['traffic_signals:direction'] === 'backward' ? Math.PI : 0);
    const box = modelBox(lon, lat, angle, node.properties.id);
    features.push(box(0, 0, 0.18, 0.18, 0, 4.4, '#6C7881'));
    features.push(box(0, -0.08, 0.66, 0.42, 3.1, 4.65, '#26313A'));
    // Shallow coloured lenses on the front face, with dark housing behind.
    for (const [base, color] of [[4.21, '#C66A63'], [3.76, '#C7AB61'], [3.31, '#63977B']] as const) {
      features.push(box(0, -0.31, 0.37, 0.08, base, base+0.3, color));
    }
  }
  return { type: 'FeatureCollection', features };
}
