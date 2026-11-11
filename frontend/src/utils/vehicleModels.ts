import type { FeatureCollection, Polygon } from 'geojson';
import type { SimVehicle } from '../types';

export const CAR_MODEL_MINZOOM = 17;
const COLORS = ['#3B82B6', '#C75C50', '#DADED9', '#D0AB53', '#627B70', '#5D6575'];

/** Metre-scale cars; heading is clockwise from north, as in simulation recordings. */
export function vehicleModels(vehicles: SimVehicle[]): FeatureCollection<Polygon> {
  const features: FeatureCollection<Polygon>['features'] = [];
  for (const [id, lon, lat, heading] of vehicles) {
    const angle = heading * Math.PI / 180, sin = Math.sin(angle), cos = Math.cos(angle);
    const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
    let hash = 0;
    for (const char of id) hash = (Math.imul(hash, 31) + char.charCodeAt(0)) >>> 0;
    const box = (part: string, x: number, y: number, w: number, l: number, base: number, height: number, color: string) => {
      const ring = [[x-w/2,y-l/2],[x+w/2,y-l/2],[x+w/2,y+l/2],[x-w/2,y+l/2],[x-w/2,y-l/2]]
        .map(([px, py]) => [lon+(px*cos+py*sin)/kx, lat+(-px*sin+py*cos)/ky]);
      features.push({ type:'Feature', id: `${id}:${part}`, geometry:{type:'Polygon',coordinates:[ring]}, properties:{vehicleId:id,part,base,height,color} });
    };
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
