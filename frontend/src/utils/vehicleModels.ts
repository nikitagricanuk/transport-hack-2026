import type { FeatureCollection, Polygon } from 'geojson';
import type { SimVehicle } from '../types';

export const CAR_MODEL_MINZOOM = 17;
const COLORS = ['#3B82B6', '#C75C50', '#DADED9', '#D0AB53', '#627B70', '#5D6575'];

/** Size (m) and colours of the kinds that are not drawn as a passenger car. */
const KINDS: Record<string, { length: number; width: number; height: number; base: number; color: string; roof: string; windows: boolean }> = {
  bus: { length: 12, width: 2.5, height: 3.1, base: 0.3, color: '#E8A33D', roof: '#F3F4F6', windows: true },
  tram: { length: 26, width: 2.5, height: 3.4, base: 0.35, color: '#D94141', roof: '#F3F4F6', windows: true },
  truck: { length: 9, width: 2.5, height: 3.4, base: 0.45, color: '#6B7280', roof: '#9CA3AF', windows: false },
  motorcycle: { length: 2.1, width: 0.8, height: 1.1, base: 0.3, color: '#7C3AED', roof: '#4C1D95', windows: false },
  bicycle: { length: 1.8, width: 0.6, height: 1.0, base: 0.3, color: '#16A34A', roof: '#14532D', windows: false },
};

/** Metre-scale cars; heading is clockwise from north, as in simulation recordings. */
export function vehicleModels(vehicles: SimVehicle[]): FeatureCollection<Polygon> {
  const features: FeatureCollection<Polygon>['features'] = [];
  for (const [id, lon, lat, heading, kind] of vehicles) {
    const angle = heading * Math.PI / 180, sin = Math.sin(angle), cos = Math.cos(angle);
    const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
    let hash = 0;
    for (const char of id) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) >>> 0;
    const box = (part: string, x: number, y: number, w: number, l: number, base: number, height: number, color: string) => {
      const ring = [[x-w/2,y-l/2],[x+w/2,y-l/2],[x+w/2,y+l/2],[x-w/2,y+l/2],[x-w/2,y-l/2]]
        .map(([px, py]) => [lon+(px*cos+py*sin)/kx, lat+(-px*sin+py*cos)/ky]);
      features.push({ type:'Feature', id: `${id}:${part}`, geometry:{type:'Polygon',coordinates:[ring]}, properties:{vehicleId:id,part,base,height,color} });
    };
    const k = KINDS[kind ?? 'car'];
    if (k) {
      // a bus, tram, truck or two-wheeler: a body box of its real size, a window band and wheels
      const half = k.length / 2;
      for (const y of [-half * 0.7, half * 0.7]) for (const x of [-k.width / 2 + 0.1, k.width / 2 - 0.1]) box(`wheel:${x}:${y}`, x, y, 0.2, Math.min(0.9, k.length * 0.12), 0.1, 0.7, '#252B32');
      box('body', 0, 0, k.width, k.length, k.base, k.height, k.color);
      if (k.windows) box('windows', 0, 0.2, k.width + 0.02, k.length * 0.85, k.height * 0.55, k.height * 0.85, '#334956');
      box('roof', 0, 0, k.width * 0.9, k.length * 0.95, k.height - 0.05, k.height, k.roof);
      continue;
    }
    for (const x of [-0.88,0.88]) for (const y of [-1.35,1.35]) box(`wheel:${x}:${y}`,x,y,0.22,0.65,0.12,0.65,'#252B32');
    box('body',0,0,1.8,4.4,0.35,1.02,COLORS[hash % COLORS.length]);
    box('cabin',0,-0.15,1.5,2.25,1.02,1.55,'#334956');
    box('roof',0,-0.25,1.5,1.35,1.55,1.64,COLORS[hash % COLORS.length]);
    for (const x of [-0.58,0.58]) {
      box(`headlight:${x}`,x,2.16,0.35,0.1,0.65,0.88,'#FFF0C5');
      box(`taillight:${x}`,x,-2.16,0.35,0.1,0.65,0.88,'#C43E42');
    }
  }
  return {type:'FeatureCollection',features};
}
