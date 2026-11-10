import type { Feature, FeatureCollection, LineString, Polygon } from 'geojson';

/** Default lane width, metres (магистральные улицы, СП 42.13330.2016). */
export const LANE_W = 3.5;

/**
 * Lane width by road class, metres — СП 42.13330.2016, разд. 11 (ширина полосы движения):
 * магистральные дороги 3,75; магистральные улицы общегородского и районного значения 3,5;
 * улицы местного значения в жилой застройке 3,0; проезды 2,75. The mapping of OSM classes
 * to the street categories of the СП is ours.
 */
export const LANE_WIDTH_BY_CLASS: Record<string, number> = {
  motorway: 3.75, trunk: 3.75, primary: 3.5, secondary: 3.5, tertiary: 3.5,
  unclassified: 3.0, residential: 3.0, living_street: 3.0, service: 2.75,
};

/** Lane width for a way: its own width tag split over the lanes, else the class value. */
export function laneWidthFor(tags: Record<string, string>, lanes: number): number {
  const w = parseFloat((tags.width ?? '').replace(',', '.'));
  if (Number.isFinite(w) && w > 0 && lanes > 0) return Math.min(4.5, Math.max(2.5, w / lanes));
  const base = (tags.highway ?? '').replace(/_link$/, '');
  return LANE_WIDTH_BY_CLASS[base] ?? LANE_W;
}
/** From this zoom roads are drawn at their real width with lanes and markings. */
export const LANE_MINZOOM = 15;

const M_PER_DEG_LAT = 110_540;
const M_PER_DEG_LON_EQ = 111_320;

export interface LaneRoad {
  id: number;
  coords: number[][];
  forward: number;
  backward: number;
  /** metres; LANE_W when not given */
  laneW?: number;
  /** junctions on this road: markings and arrows stop at their edge */
  junctions?: LaneJunction[];
}

export interface LaneGeometry {
  surface: FeatureCollection<Polygon>;
  marks: FeatureCollection<LineString>;
  arrows: FeatureCollection<LineString>;
}

/**
 * Offset a lon/lat polyline sideways by `offsetM` metres; positive is to the right of
 * the line direction (where forward lanes are in right-hand traffic). Joins are mitred,
 * with the miter capped so sharp turns do not spike.
 */
export function offsetLine(coords: number[][], offsetM: number): number[][] {
  if (coords.length < 2 || offsetM === 0) return coords.map((c) => [...c]);
  const lat0 = coords.reduce((s, c) => s + c[1], 0) / coords.length;
  const kx = M_PER_DEG_LON_EQ * Math.cos((lat0 * Math.PI) / 180);
  const pts = coords.map((c) => [c[0] * kx, c[1] * M_PER_DEG_LAT]);
  const normals: number[][] = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const dx = pts[i + 1][0] - pts[i][0];
    const dy = pts[i + 1][1] - pts[i][1];
    const len = Math.hypot(dx, dy) || 1;
    normals.push([dy / len, -dx / len]); // right-hand normal
  }
  return pts.map((p, i) => {
    const a = normals[Math.max(0, i - 1)];
    const b = normals[Math.min(normals.length - 1, i)];
    let nx = a[0] + b[0];
    let ny = a[1] + b[1];
    const nl = Math.hypot(nx, ny);
    if (nl < 1e-9) {
      nx = b[0];
      ny = b[1];
    } else {
      nx /= nl;
      ny /= nl;
    }
    const cos = nx * b[0] + ny * b[1];
    const scale = Math.min(2, 1 / Math.max(cos, 0.5)); // miter limit
    return [(p[0] + nx * offsetM * scale) / kx, (p[1] + ny * offsetM * scale) / M_PER_DEG_LAT];
  });
}

/**
 * Road surface, lane markings and per-lane travel direction for the physical view.
 * Lanes are ordered left to right along the way: backward lanes on the left,
 * forward lanes on the right (right-hand traffic).
 */
export function buildLanes(roads: LaneRoad[], plates: LaneJunction[] = []): LaneGeometry {
  const surface: Feature<Polygon>[] = [];
  const marks: Feature<LineString>[] = [];
  const arrows: Feature<LineString>[] = [];
  // Join the incident carriageway edges without adding a circular apron.
  for (const j of plates) {
    const ring = junctionPlate(j, roads);
    if (ring.length >= 4) surface.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: { junction: true } });
  }
  const ends=new Map<string,LaneRoad[]>();
  for(const r of roads)for(const p of [r.coords[0],r.coords[r.coords.length-1]])if(p){const k=p.join(',');ends.set(k,[...(ends.get(k)??[]),r]);}
  const used=new Map<string,Set<number>>();
  for(const r of roads)for(const p of r.coords){const key=p.join(',');const ways=used.get(key)??new Set<number>();ways.add(r.id);used.set(key,ways);}
  for (const raw of roads) {
    let display=raw.coords.map(p=>[...p]);
    // Add a display-only knot 25m from either end so a two-node way tapers locally.
    for(const fromStart of [true,false]){
      if(display.length<2)continue;
      const seq=fromStart?display:[...display].reverse();const a=seq[0],b=seq[1];
      const length=Math.hypot((b[0]-a[0])*M_PER_DEG_LON_EQ*Math.cos(a[1]*Math.PI/180),(b[1]-a[1])*M_PER_DEG_LAT);
      if(length>50 && ends.get(a.join(','))?.length===2){const f=25/length;seq.splice(1,0,[a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f]);}
      display=fromStart?seq:seq.reverse();
    }
    const anchors=[0,...display.flatMap((p,i)=>i>0&&i<display.length-1&&(used.get(p.join(','))?.size??0)>1?[i]:[]),display.length-1];
    const rounded:number[][]=[];
    for(let i=0;i<anchors.length-1;i++){const piece=smoothLine(display.slice(anchors[i],anchors[i+1]+1));rounded.push(...(i?piece.slice(1):piece));}
    const r={...raw,coords:rounded.length?rounded:display};
    const cuts = r.junctions ?? [];
    const n = r.forward + r.backward;
    if (n < 1 || r.coords.length < 2) continue;
    const lw = r.laneW ?? LANE_W;
    const half = (n * lw) / 2;
    const left = offsetLine(r.coords, -half);
    const right = offsetLine(r.coords, half);
    surface.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [[...left, ...right.reverse(), left[0]]] },
      properties: { id: r.id },
    });
    // Match width at a two-way join, then return to own width within 25 metres.
    const ownHalf=half;
    const widths=r.coords.map((p)=>{
      const distance=(a:number[],b:number[])=>Math.hypot((a[0]-b[0])*M_PER_DEG_LON_EQ*Math.cos(p[1]*Math.PI/180),(a[1]-b[1])*M_PER_DEG_LAT);
      let width=ownHalf;
      for(const [end,at] of [[r.coords[0],0],[r.coords[r.coords.length-1],r.coords.length-1]] as const){
        const linked=ends.get(end.join(','));
        if(linked?.length===2){const other=linked.find(w=>w.id!==r.id);if(other){const blend=(n*lw+(other.forward+other.backward)*(other.laneW??LANE_W))/4;width+=(blend-ownHalf)*Math.max(0,1-distance(p,end)/25);}}
        void at;
      }
      return width;
    });
    const unitLeft=offsetLine(r.coords,-1),unitRight=offsetLine(r.coords,1);
    const taperedLeft=r.coords.map((p,i)=>p.map((v,j)=>v+(unitLeft[i][j]-v)*widths[i]));
    const taperedRight=r.coords.map((p,i)=>p.map((v,j)=>v+(unitRight[i][j]-v)*widths[i]));
    surface[surface.length-1].geometry.coordinates=[[...taperedLeft,...taperedRight.reverse(),taperedLeft[0]]];
    for (let j = 0; j <= n; j++) {
      const kind = j === 0 || j === n ? 'edge' : j === r.backward && r.backward > 0 && r.forward > 0 ? 'center' : 'lane';
      const line = j === 0 ? left : j === n ? offsetLine(r.coords, half) : offsetLine(r.coords, -half + j * lw);
      for (const piece of cutAround(line, cuts)) {
        marks.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: piece }, properties: { id: r.id, kind } });
      }
    }
    for (let i = 0; i < n; i++) {
      const line = offsetLine(r.coords, -half + lw * (i + 0.5));
      const backward = i < r.backward;
      for (const piece of cutAround(backward ? line.reverse() : line, cuts)) {
        arrows.push({
          type: 'Feature', geometry: { type: 'LineString', coordinates: piece },
          properties: { id: r.id, lane: i, dir: backward ? 'backward' : 'forward' },
        });
      }
    }
  }
  return {
    surface: { type: 'FeatureCollection', features: surface },
    marks: { type: 'FeatureCollection', features: marks },
    arrows: { type: 'FeatureCollection', features: arrows },
  };
}

/** Lanes per direction from OSM tags; `dir` is +1 / -1 for one-way roads, 0 for two-way. */
export function laneSplit(tags: Record<string, string>, dir: number): { forward: number; backward: number } {
  const num = (k: string) => {
    const v = parseInt(tags[k] ?? '', 10);
    return Number.isFinite(v) && v > 0 ? v : undefined;
  };
  const total = num('lanes');
  if (dir !== 0) {
    const n = total ?? 1;
    return dir > 0 ? { forward: n, backward: 0 } : { forward: 0, backward: n };
  }
  const fw = num('lanes:forward');
  const bw = num('lanes:backward');
  if (fw && bw) return { forward: fw, backward: bw };
  if (total && fw) return { forward: fw, backward: Math.max(1, total - fw) };
  if (total && bw) return { forward: Math.max(1, total - bw), backward: bw };
  if (total) return { forward: Math.ceil(total / 2), backward: Math.max(1, Math.floor(total / 2)) };
  return { forward: 1, backward: 1 };
}

/** A junction on the lane view: where 3+ road segments meet. `radius` covers the widest road, m. */
export interface LaneJunction {
  coord: number[];
  radius: number;
}

/**
 * Junctions of a road network for the lane view: nodes where three or more road segments
 * meet (a road passing through counts twice). The radius is half the widest incident road,
 * so the plate covers every carriageway that ends there.
 */
export function findJunctions(roads: { coords: number[][]; nodes: number[]; halfWidth: number }[]): Map<number, LaneJunction> {
  const acc = new Map<number, { coord: number[]; degree: number; radius: number }>();
  for (const r of roads) {
    r.nodes.forEach((id, i) => {
      const end = i === 0 || i === r.nodes.length - 1;
      const cur = acc.get(id) ?? { coord: r.coords[i], degree: 0, radius: 0 };
      cur.degree += end ? 1 : 2;
      cur.radius = Math.max(cur.radius, r.halfWidth);
      acc.set(id, cur);
    });
  }
  const out = new Map<number, LaneJunction>();
  for (const [id, j] of acc) if (j.degree >= 3) out.set(id, { coord: j.coord, radius: j.radius + 0.5 });
  return out;
}

/** Convex patch bounded by the actual edges of the incident carriageways. */
export function junctionPlate(j: LaneJunction, roads: LaneRoad[]): number[][] {
  const points: number[][] = [];
  for (const road of roads) {
    if (road.coords.length < 2 || road.forward + road.backward < 1) continue;
    const index = road.coords.findIndex(p => p[0] === j.coord[0] && p[1] === j.coord[1]);
    if (index < 0) continue;
    const half = (road.forward + road.backward) * (road.laneW ?? LANE_W) / 2;
    points.push(offsetLine(road.coords, -half)[index], offsetLine(road.coords, half)[index]);
  }
  points.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (a: number[], b: number[], c: number[]) =>
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const chain = (input: number[][]) => {
    const hull: number[][] = [];
    for (const p of input) {
      while (hull.length >= 2 && cross(hull[hull.length - 2], hull[hull.length - 1], p) <= 0) hull.pop();
      hull.push(p);
    }
    return hull.slice(0, -1);
  };
  const hull = [...chain(points), ...chain([...points].reverse())];
  return hull.length >= 3 ? [...hull, hull[0]] : [];
}

/**
 * The parts of a line outside every circle: lane lines and arrows stop at the edge of a
 * junction instead of running through it on top of the crossing road's lanes.
 */
export function cutAround(coords: number[][], circles: LaneJunction[]): number[][][] {
  if (!circles.length || coords.length < 2) return [coords];
  const lat0 = coords[0][1];
  const kx = M_PER_DEG_LON_EQ * Math.cos((lat0 * Math.PI) / 180);
  const toM = (c: number[]) => [c[0] * kx, c[1] * M_PER_DEG_LAT];
  const toLL = (p: number[]) => [p[0] / kx, p[1] / M_PER_DEG_LAT];
  const cs = circles.map((c) => ({ p: toM(c.coord), r: c.radius }));
  const inside = (p: number[]) => cs.some((c) => Math.hypot(p[0] - c.p[0], p[1] - c.p[1]) < c.r);
  const pieces: number[][][] = [];
  let cur: number[][] = [];
  const pts = coords.map(toM);
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    // split the segment where it enters or leaves a circle
    const ts = [0, 1];
    for (const c of cs) {
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const fx = a[0] - c.p[0], fy = a[1] - c.p[1];
      const A = dx * dx + dy * dy, B = 2 * (fx * dx + fy * dy), C = fx * fx + fy * fy - c.r * c.r;
      const disc = B * B - 4 * A * C;
      if (A === 0 || disc <= 0) continue;
      for (const t of [(-B - Math.sqrt(disc)) / (2 * A), (-B + Math.sqrt(disc)) / (2 * A)]) if (t > 0 && t < 1) ts.push(t);
    }
    ts.sort((x, y) => x - y);
    for (let k = 0; k < ts.length - 1; k++) {
      const p0 = [a[0] + (b[0] - a[0]) * ts[k], a[1] + (b[1] - a[1]) * ts[k]];
      const p1 = [a[0] + (b[0] - a[0]) * ts[k + 1], a[1] + (b[1] - a[1]) * ts[k + 1]];
      const mid = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2];
      if (inside(mid)) {
        if (cur.length >= 2) pieces.push(cur.map(toLL));
        cur = [];
        continue;
      }
      if (!cur.length) cur.push(p0);
      cur.push(p1);
    }
  }
  if (cur.length >= 2) pieces.push(cur.map(toLL));
  return pieces;
}

/** Chaikin rounds the display line only, retaining both OSM endpoints. */
export function smoothLine(coords:number[][], iterations=2):number[][] {
  let points=coords.map(p=>[...p]);
  if(points.length<3)return points;
  for(let i=0;i<iterations;i++) {
    const next=[points[0]];
    for(let j=0;j<points.length-1;j++){
      const a=points[j],b=points[j+1];
      next.push([.75*a[0]+.25*b[0],.75*a[1]+.25*b[1]],[.25*a[0]+.75*b[0],.25*a[1]+.75*b[1]]);
    }
    next.push(points[points.length-1]);points=next;
  }
  return points;
}
/** Polygon whose full width interpolates between two lane counts. */
export function taperPolygon(coords:number[][], startLanes:number,endLanes:number):number[][] {
  const lengths=coords.map((p,i)=>i ? Math.hypot((p[0]-coords[i-1][0])*M_PER_DEG_LON_EQ*Math.cos(p[1]*Math.PI/180),(p[1]-coords[i-1][1])*M_PER_DEG_LAT):0);
  const total=lengths.reduce((a,b)=>a+b,0)||1;
  let along=0;
  const left:number[][]=[],right:number[][]=[];
  coords.forEach((_,i)=>{along+=lengths[i];const half=(startLanes+(endLanes-startLanes)*along/total)*LANE_W/2;left.push(offsetLine(coords,-half)[i]);right.push(offsetLine(coords,half)[i]);});
  return [...left,...right.reverse(),left[0]];
}

/**
 * Display-only attachment of a branching road to the lanes of the wider road it joins.
 *
 * In OSM (and for SUMO) every connection is a node on the road axis. Drawn like that,
 * a slip road or a Y branch starts from the centre line of the main road. Here the end
 * of a road that meets a wider road is shifted sideways, towards the side the branch
 * leaves on, by (half width of the wider road − own half width): the outer edges line
 * up, so a ramp leaves from the outer lane and incoming Y roads join their own side.
 * The file is not changed.
 */
export function attachToLanes(roads: { id: number; coords: number[][]; nodes: number[]; halfWidth: number }[]): Map<number, number[][]> {
  const at = new Map<number, { road: (typeof roads)[number]; i: number }[]>();
  for (const r of roads) r.nodes.forEach((n, i) => at.set(n, [...(at.get(n) ?? []), { road: r, i }]));
  const out = new Map<number, number[][]>();
  for (const r of roads) {
    if (r.coords.length < 2) continue;
    let coords: number[][] | null = null;
    for (const end of [0, r.coords.length - 1]) {
      const node = r.nodes[end];
      // the parent: the widest other road at this node, clearly wider than this one
      const parent = (at.get(node) ?? []).filter((x) => x.road.id !== r.id && x.road.halfWidth > r.halfWidth + 0.5)
        .sort((a, b) => b.road.halfWidth - a.road.halfWidth)[0];
      if (!parent) continue;
      const p = parent.road.coords;
      const pi = parent.i;
      const a = p[Math.max(0, pi - 1)], b = p[Math.min(p.length - 1, pi + 1)];
      const kx = M_PER_DEG_LON_EQ * Math.cos((r.coords[end][1] * Math.PI) / 180);
      const dx = (b[0] - a[0]) * kx, dy = (b[1] - a[1]) * M_PER_DEG_LAT;
      const len = Math.hypot(dx, dy);
      if (len < 1e-6) continue;
      const nx = dy / len, ny = -dx / len;   // right-hand normal of the parent
      const inner = r.coords[end === 0 ? 1 : r.coords.length - 2];
      const vx = (inner[0] - r.coords[end][0]) * kx, vy = (inner[1] - r.coords[end][1]) * M_PER_DEG_LAT;
      const side = vx * nx + vy * ny >= 0 ? 1 : -1;   // which side of the parent the branch goes to
      const shift = side * (parent.road.halfWidth - r.halfWidth);
      coords = coords ?? r.coords.map((c) => [...c]);
      coords[end] = [r.coords[end][0] + (nx * shift) / kx, r.coords[end][1] + (ny * shift) / M_PER_DEG_LAT];
    }
    if (coords) out.set(r.id, coords);
  }
  return out;
}
