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
  const junctionRings = new Map<LaneJunction, number[][]>();
  // Join the incident carriageway edges without adding a circular apron.
  for (const j of plates) {
    const ring = junctionPlate(j, roads);
    junctionRings.set(j, ring);
    // Each arm contributes a cross-section followed by its curved curb return.
    for (let i = 0; i + 9 < ring.length; i += 10) {
      marks.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: ring.slice(i + 1, i + 10) }, properties: { kind: 'edge', junction: true } });
    }
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
    const cuts = (r.junctions ?? []).map(j => junctionRings.get(j) ?? []).filter(ring => ring.length >= 4);
    const n = r.forward + r.backward;
    if (n < 1 || r.coords.length < 2) continue;
    const lw = r.laneW ?? LANE_W;
    const half = (n * lw) / 2;
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
    // Every boundary uses the same width profile, including arrow baselines.
    // A degree-two join shares a tangent as well as a width; otherwise a bend
    // between separate OSM ways leaves a wedge-shaped hole.
    const boundary = (fraction: number): number[][] => {
      const unit = offsetLine(r.coords, 1);
      return r.coords.map((p, i) => {
        let normal = [(unit[i][0] - p[0]), (unit[i][1] - p[1])];
        if (i === 0 || i === r.coords.length - 1) {
          const linked = ends.get(p.join(','));
          const other = linked?.length === 2 ? linked.find(w => w.id !== r.id) : undefined;
          if (other) {
            const kx = M_PER_DEG_LON_EQ * Math.cos(p[1] * Math.PI / 180);
            const adjacent = r.coords[i === 0 ? 1 : i - 1];
            const atStart = other.coords[0].join(',') === p.join(',');
            const neighbor = other.coords[atStart ? 1 : other.coords.length - 2];
            const direction = (q: number[]) => {
              const dx = (q[0] - p[0]) * kx, dy = (q[1] - p[1]) * M_PER_DEG_LAT;
              const len = Math.hypot(dx, dy) || 1;
              return [dx / len, dy / len];
            };
            const a = direction(adjacent), b = direction(neighbor);
            const dx = a[0] - b[0], dy = a[1] - b[1];
            const len = Math.hypot(dx, dy);
            if (len > 0.1) {
              const sign = i === 0 ? 1 : -1;
              normal = [sign * dy / len / kx, -sign * dx / len / M_PER_DEG_LAT];
            }
          }
        }
        let offset = widths[i] * fraction;
        // Match lane boundaries at a lane-count change. A disappearing divider
        // converges on the edge, rather than ending abruptly in a live lane.
        if (fraction > -1 && fraction < 1) for (const endIndex of [0, r.coords.length - 1]) {
          const end = r.coords[endIndex];
          const linked = ends.get(end.join(','));
          const other = linked?.length === 2 ? linked.find(w => w.id !== r.id) : undefined;
          if (!other) continue;
          const distance = Math.hypot((p[0]-end[0])*M_PER_DEG_LON_EQ*Math.cos(p[1]*Math.PI/180), (p[1]-end[1])*M_PER_DEG_LAT);
          const weight = Math.max(0, 1-distance/25);
          const reversed = (endIndex === 0) === (other.coords[0].join(',') === end.join(','));
          const backward = reversed ? other.forward : other.backward;
          const forward = reversed ? other.backward : other.forward;
          const otherWidth = other.laneW ?? LANE_W;
          const joinHalf = (n*lw + (backward+forward)*otherWidth)/4;
          const center = ((r.backward-r.forward)*lw + (backward-forward)*otherWidth)/4;
          const lane = (fraction+1)*n/2-r.backward;
          const common = lane < 0 ? Math.min(r.backward,backward) : Math.min(r.forward,forward);
          const target = lane === 0 ? center : common > 0
            ? center + Math.min(1,Math.abs(lane)/common) * (lane < 0 ? -joinHalf-center : joinHalf-center)
            : (lane < 0 ? -joinHalf : joinHalf);
          offset += (target-joinHalf*fraction)*weight;
        }
        return [p[0] + normal[0] * offset, p[1] + normal[1] * offset];
      });
    };
    const taperedLeft = boundary(-1), taperedRight = boundary(1);
    surface.push({
      type: 'Feature', geometry: { type: 'Polygon',
        coordinates: [[...taperedLeft, ...[...taperedRight].reverse(), taperedLeft[0]]] },
      properties: { id: r.id },
    });
    for (let j = 0; j <= n; j++) {
      const kind = j === 0 || j === n ? 'edge' : j === r.backward && r.backward > 0 && r.forward > 0 ? 'center' : 'lane';
      const line = boundary(-1 + 2 * j / n);
      // Four or more lanes use a double solid opposing-flow separator (1.3).
      const lines = kind === 'center' && n >= 4 ? [offsetLine(line, -0.15), offsetLine(line, 0.15)] : [line];
      for (const marking of lines) for (const piece of cutPolygons(marking, cuts)) {
        marks.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: piece }, properties: { id: r.id, kind } });
      }
    }
    for (let i = 0; i < n; i++) {
      const line = boundary(-1 + 2 * (i + 0.5) / n);
      const backward = i < r.backward;
      for (const piece of cutPolygons(backward ? line.reverse() : line, cuts)) {
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

/** Curved curb returns between incident arms, in local metre coordinates.
 * End sections lie inside each road. Quadratic corners avoid the convex hull's
 * pointed "house" shape and are bounded so acute ramps cannot grow spikes.
 */
export function junctionPlate(j: LaneJunction, roads: LaneRoad[]): number[][] {
  const kx = M_PER_DEG_LON_EQ * Math.cos(j.coord[1] * Math.PI / 180);
  const arms: { x: number; y: number; half: number; reach: number; angle: number }[] = [];
  for (const road of roads) {
    const index = road.coords.findIndex(p => p[0] === j.coord[0] && p[1] === j.coord[1]);
    if (index < 0 || road.forward + road.backward < 1) continue;
    for (const adjacent of [index - 1, index + 1]) {
      const p = road.coords[adjacent];
      if (!p) continue;
      const dx = (p[0] - j.coord[0]) * kx, dy = (p[1] - j.coord[1]) * M_PER_DEG_LAT;
      const len = Math.hypot(dx, dy);
      if (len < 0.01) continue;
      arms.push({ x: dx / len, y: dy / len,
        half: (road.forward + road.backward) * (road.laneW ?? LANE_W) / 2,
        reach: Math.min(j.radius * 2, len / 2), angle: Math.atan2(dy, dx) });
    }
  }
  arms.sort((a,b) => a.angle - b.angle);
  const ring: number[][] = [];
  const add = (p: number[]) => ring.push([j.coord[0] + p[0] / kx, j.coord[1] + p[1] / M_PER_DEG_LAT]);
  const edge = (a: typeof arms[number], side: number) =>
    [a.x * a.reach - a.y * a.half * side, a.y * a.reach + a.x * a.half * side];
  for (let i = 0; i < arms.length; i++) {
    const a = arms[i], b = arms[(i + 1) % arms.length];
    add(edge(a, -1));
    const from = edge(a, 1), to = edge(b, -1);
    add(from);
    const cross = a.x * b.y - a.y * b.x;
    let control = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
    if (Math.abs(cross) > 0.05) {
      const t = ((to[0] - from[0]) * b.y - (to[1] - from[1]) * b.x) / cross;
      const candidate = [from[0] + t * a.x, from[1] + t * a.y];
      if (Math.hypot(...candidate) < j.radius * 3) control = candidate;
    }
    for (let step = 1; step <= 8; step++) {
      const t = step / 8, u = 1 - t;
      add([u*u*from[0] + 2*u*t*control[0] + t*t*to[0], u*u*from[1] + 2*u*t*control[1] + t*t*to[1]]);
    }
  }
  return ring.length >= 3 ? [...ring, ring[0]] : [];
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

/** Clip markings to the actual junction footprint, rather than a circle that
 * leaves crossing edge lines visible or erases unrelated nearby carriageways. */
export function cutPolygons(coords: number[][], rings: number[][][]): number[][][] {
  if (!rings.length || coords.length < 2) return [coords];
  const origin = coords[0];
  const kx = M_PER_DEG_LON_EQ * Math.cos(origin[1] * Math.PI / 180);
  const project = (p: number[]) => [(p[0]-origin[0])*kx, (p[1]-origin[1])*M_PER_DEG_LAT];
  const restore = (p: number[]) => [origin[0]+p[0]/kx, origin[1]+p[1]/M_PER_DEG_LAT];
  const polygons = rings.map(r => r.map(project));
  const inside = (p: number[], ring: number[][]) => {
    let hit = false;
    for (let i=0, j=ring.length-1; i<ring.length; j=i++) {
      const a=ring[i], b=ring[j];
      if ((a[1]>p[1]) !== (b[1]>p[1]) && p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]) hit=!hit;
    }
    return hit;
  };
  const pieces: number[][][] = [];
  let current: number[][] = [];
  for (let i=1; i<coords.length; i++) {
    const a=project(coords[i-1]), b=project(coords[i]);
    const dx=b[0]-a[0], dy=b[1]-a[1];
    const ts=[0,1];
    for (const ring of polygons) for (let j=1;j<ring.length;j++) {
      const c=ring[j-1], d=ring[j], ex=d[0]-c[0], ey=d[1]-c[1];
      const det=dx*ey-dy*ex;
      if (Math.abs(det)<1e-9) continue;
      const t=((c[0]-a[0])*ey-(c[1]-a[1])*ex)/det;
      const u=((c[0]-a[0])*dy-(c[1]-a[1])*dx)/det;
      if(t>0 && t<1 && u>=0 && u<=1) ts.push(t);
    }
    ts.sort((a,b)=>a-b);
    for(let j=1;j<ts.length;j++) {
      if(ts[j]-ts[j-1]<1e-9)continue;
      const at=(t:number)=>[a[0]+dx*t,a[1]+dy*t];
      if(polygons.some(ring=>inside(at((ts[j]+ts[j-1])/2),ring))) {
        if(current.length>1)pieces.push(current.map(restore));
        current=[];
      } else {
        if(!current.length)current.push(at(ts[j-1]));
        current.push(at(ts[j]));
      }
    }
  }
  if(current.length>1)pieces.push(current.map(restore));
  return pieces;
}
