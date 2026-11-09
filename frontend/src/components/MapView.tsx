import { forwardRef, memo, useEffect, useImperativeHandle, useRef, useState } from 'react';
import maplibregl, { type GeoJSONSource, type MapGeoJSONFeature, type MapMouseEvent } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { Feature, FeatureCollection, LineString, Point } from 'geojson';
import type { Geo, GeoDiff, Issue, LayerVisibility, Op, Selection, Tool, WayProps } from '../types';

export interface MapHandle {
  flyTo: (lon: number, lat: number, zoom?: number) => void;
  fitBounds: (b: [number, number, number, number]) => void;
}

interface Props {
  geo: Geo | null;
  /** set when `geo` came from a patch: lets the map update only the touched features */
  diff: GeoDiff | null;
  selection: Selection;
  tool: Tool;
  layers: LayerVisibility;
  baseOpacity: number;
  issues: Issue[];
  onSelect: (s: Selection) => void;
  /** resolves false when the backend rejected the edit */
  onOp: (op: Op, opts?: { select?: 'result' | 'keep' }) => void | Promise<boolean>;
  onHint: (text: string | null) => void;
  onCursor: (lonlat: [number, number] | null, zoom: number) => void;
  wayById: (id: number) => WayProps | undefined;
}

type DraftPoint = { lon: number; lat: number; id?: number; way_id?: number };

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
const HIT_PX = 6;

const ROAD_COLOR: maplibregl.ExpressionSpecification = [
  'match', ['get', 'highway'],
  ['motorway', 'trunk', 'motorway_link', 'trunk_link'], '#F2A06B',
  ['primary', 'primary_link'], '#FFD27A',
  ['secondary', 'secondary_link'], '#FFF0A8',
  '#FFFFFF',
];

/** Width by lane count and zoom. `extra` px is added at every stop: MapLibre only
 * accepts ['zoom'] in a top-level interpolate, so it cannot be wrapped in ['+', ...]. */
const laneWidth = (
  k: number,
  extra: number | maplibregl.ExpressionSpecification = 0,
): maplibregl.ExpressionSpecification => [
  'interpolate', ['exponential', 2], ['zoom'],
  13, ['+', ['*', ['get', 'lanes'], 0.6 * k], extra],
  17, ['+', ['*', ['get', 'lanes'], 3.2 * k], extra],
  20, ['+', ['*', ['get', 'lanes'], 22 * k], extra],
];

function arrowImage(): ImageData {
  const s = 24;
  const c = document.createElement('canvas');
  c.width = s;
  c.height = s;
  const ctx = c.getContext('2d')!;
  ctx.strokeStyle = '#3F4652';
  ctx.lineWidth = 2.5;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(8, 6);
  ctx.lineTo(15, 12);
  ctx.lineTo(8, 18);
  ctx.stroke();
  return ctx.getImageData(0, 0, s, s);
}

const MapView = forwardRef<MapHandle, Props>(function MapView(props, ref) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const propsRef = useRef(props);
  propsRef.current = props;

  const draft = useRef<DraftPoint[]>([]);
  const mergeFirst = useRef<number | null>(null);
  const drag = useRef<{
    nodeId: number; moved: boolean; lon: number; lat: number;
    ways: { coords: number[][]; idx: number[] }[]; frame: number;
  } | null>(null);

  useImperativeHandle(ref, () => ({
    flyTo: (lon, lat, zoom = 18) => mapRef.current?.flyTo({ center: [lon, lat], zoom, speed: 1.6 }),
    fitBounds: (b) => mapRef.current?.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: 40, duration: 0 }),
  }));

  // ------------------------------------------------------------ create map
  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
          },
        },
        layers: [
          { id: 'bg', type: 'background', paint: { 'background-color': '#ECEEE8' } },
          { id: 'base', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.5, 'raster-saturation': -0.6 } },
        ],
      },
      center: [104.28, 52.28],
      zoom: 15,
      maxZoom: 21,
      doubleClickZoom: false,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
    mapRef.current = map;

    map.on('load', () => {
      map.addImage('arrow', arrowImage(), { pixelRatio: 2 });
      for (const id of ['areas', 'ways', 'nodes', 'issues', 'verts', 'draft', 'drag']) {
        map.addSource(id, { type: 'geojson', data: EMPTY });
      }

      map.addLayer({ id: 'areas-fill', type: 'fill', source: 'areas',
        paint: { 'fill-color': ['match', ['get', 'layer'], 'building', '#D9D6CF', '#D3E3C6'], 'fill-opacity': 0.85,
                 'fill-outline-color': '#C2BEB4' } });
      map.addLayer({ id: 'paths', type: 'line', source: 'ways', filter: ['==', ['get', 'layer'], 'path'],
        paint: { 'line-color': '#9CA3AF', 'line-width': 1.4, 'line-dasharray': [2, 1.5] } });
      map.addLayer({ id: 'road-casing', type: 'line', source: 'ways', filter: ['==', ['get', 'layer'], 'road'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': ['case', ['get', 'bridge'], '#4B5260', '#AEB5C0'],
                 'line-width': laneWidth(1, ['case', ['get', 'bridge'], 4, 2]) } });
      map.addLayer({ id: 'road-fill', type: 'line', source: 'ways', filter: ['==', ['get', 'layer'], 'road'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': ROAD_COLOR, 'line-width': laneWidth(1) } });
      map.addLayer({ id: 'rail', type: 'line', source: 'ways', filter: ['==', ['get', 'layer'], 'rail'],
        paint: { 'line-color': '#7C3AED', 'line-width': 2, 'line-dasharray': [3, 2] } });
      map.addLayer({ id: 'way-selected', type: 'line', source: 'ways', filter: ['==', ['get', 'id'], 0],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#2563EB', 'line-width': laneWidth(1, 6), 'line-opacity': 0.35 } });
      map.addLayer({ id: 'way-selected-core', type: 'line', source: 'ways', filter: ['==', ['get', 'id'], 0],
        paint: { 'line-color': '#2563EB', 'line-width': 2.5 } });
      map.addLayer({ id: 'oneway', type: 'symbol', source: 'ways', minzoom: 15,
        filter: ['all', ['==', ['get', 'layer'], 'road'], ['!=', ['get', 'dir'], 0]],
        layout: { 'symbol-placement': 'line', 'symbol-spacing': 60, 'icon-image': 'arrow', 'icon-size': 0.9,
                  'icon-rotate': ['case', ['==', ['get', 'dir'], -1], 180, 0], 'icon-rotation-alignment': 'map',
                  'icon-allow-overlap': true } });
      map.addLayer({ id: 'road-label', type: 'symbol', source: 'ways', minzoom: 15,
        filter: ['all', ['==', ['get', 'layer'], 'road'], ['!=', ['get', 'name'], '']],
        layout: { 'symbol-placement': 'line', 'text-field': ['get', 'name'], 'text-font': ['Open Sans Regular'],
                  'text-size': 11, 'symbol-spacing': 300 },
        paint: { 'text-color': '#374151', 'text-halo-color': '#FFFFFF', 'text-halo-width': 1.4 } });
      map.addLayer({ id: 'ways-hit', type: 'line', source: 'ways',
        filter: ['in', ['get', 'layer'], ['literal', ['road', 'path', 'rail']]],
        paint: { 'line-color': '#000000', 'line-opacity': 0.01, 'line-width': 16 } });

      map.addLayer({ id: 'junctions', type: 'circle', source: 'nodes', minzoom: 16,
        filter: ['==', ['get', 'category'], 'junction'],
        paint: { 'circle-radius': 3.5, 'circle-color': '#FFFFFF', 'circle-stroke-color': '#59616D', 'circle-stroke-width': 1.3 } });
      map.addLayer({ id: 'pois', type: 'circle', source: 'nodes',
        filter: ['in', ['get', 'category'], ['literal', ['signal', 'crossing', 'stop', 'tagged']]],
        paint: {
          'circle-radius': ['match', ['get', 'category'], 'signal', 6, 'stop', 6, 4],
          'circle-color': ['match', ['get', 'category'], 'signal', '#16A34A', 'crossing', '#F8FAFC', 'stop', '#0F766E', '#94A3B8'],
          'circle-stroke-color': ['match', ['get', 'category'], 'crossing', '#111827', '#FFFFFF'],
          'circle-stroke-width': 1.5,
        } });
      map.addLayer({ id: 'node-selected', type: 'circle', source: 'nodes', filter: ['==', ['get', 'id'], 0],
        paint: { 'circle-radius': 8, 'circle-color': 'rgba(37,99,235,0.25)', 'circle-stroke-color': '#2563EB', 'circle-stroke-width': 2.5 } });

      map.addLayer({ id: 'issues', type: 'circle', source: 'issues',
        paint: {
          'circle-radius': 9,
          'circle-color': ['match', ['get', 'severity'], 'critical', '#EA580C', 'warning', '#D97706', '#64748B'],
          'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2, 'circle-opacity': 0.9,
        } });

      // live preview while a vertex is dragged; the big sources are not touched until mouseup
      map.addLayer({ id: 'drag-line', type: 'line', source: 'drag', filter: ['==', ['geometry-type'], 'LineString'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#2563EB', 'line-width': 3 } });
      map.addLayer({ id: 'verts', type: 'circle', source: 'verts',
        paint: { 'circle-radius': ['case', ['get', 'end'], 6, 5], 'circle-color': '#FFFFFF',
                 'circle-stroke-color': '#2563EB', 'circle-stroke-width': 2.2 } });
      map.addLayer({ id: 'drag-pt', type: 'circle', source: 'drag', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 6, 'circle-color': '#2563EB', 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2 } });
      map.addLayer({ id: 'draft-line', type: 'line', source: 'draft', filter: ['==', ['geometry-type'], 'LineString'],
        paint: { 'line-color': '#2563EB', 'line-width': 3, 'line-dasharray': [2, 1] } });
      map.addLayer({ id: 'draft-pts', type: 'circle', source: 'draft', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 5, 'circle-color': '#2563EB', 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2 } });
      setReady(true);
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ----------------------------------------------------------- data sync
  const fitted = useRef<string | null>(null);
  const rendered = useRef<Geo | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !props.geo) return;
    const geo = props.geo;
    const diff = props.diff;
    // After an edit only the touched features change. updateData re-tiles just those;
    // setData would re-send and re-tile all ~1400 ways and buildings.
    const incremental = diff !== null && diff.target === geo && rendered.current === diff.base;
    for (const id of ['ways', 'areas', 'nodes'] as const) {
      const src = map.getSource(id) as GeoJSONSource & { updateData?: (d: unknown) => unknown };
      if (incremental && typeof src.updateData === 'function') {
        const c = diff!.changes[id];
        if (c.remove.length || c.add.length) src.updateData({ remove: c.remove, add: c.add });
      } else {
        src.setData(geo[id]);
      }
    }
    rendered.current = geo;
    clearDrag();
    const key = JSON.stringify(props.geo.bounds);
    if (props.geo.bounds && fitted.current === null) {
      fitted.current = key;
      const b = props.geo.bounds;
      map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: 40, duration: 0 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, props.geo]);

  const clearDrag = () => {
    const src = mapRef.current?.getSource('drag') as GeoJSONSource | undefined;
    src?.setData(EMPTY);
  };

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const feats: Feature<Point>[] = props.issues.flatMap((i) => {
      const pts = i.locations?.length ? i.locations : i.location ? [i.location] : [];
      return pts.map((coordinates, n) => ({
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates },
        properties: { severity: i.severity, id: `${i.id}-${n}` },
      }));
    });
    (map.getSource('issues') as GeoJSONSource).setData({ type: 'FeatureCollection', features: feats });
  }, [ready, props.issues]);

  const updateVerts = () => {
    const map = mapRef.current;
    const p = propsRef.current;
    if (!map || !ready) return;
    const sel = p.selection;
    let data: FeatureCollection = EMPTY;
    if (sel?.kind === 'way') {
      const f = p.geo?.ways.features.find((w) => w.properties.id === sel.id);
      if (f) {
        const coords = f.geometry.coordinates;
        data = { type: 'FeatureCollection', features: coords.map((c, i) => ({
          type: 'Feature', geometry: { type: 'Point', coordinates: c },
          properties: { id: f.properties.nodes[i], end: i === 0 || i === coords.length - 1 } })) };
      }
    }
    (map.getSource('verts') as GeoJSONSource).setData(data);
  };

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const sel = props.selection;
    const wayId = sel?.kind === 'way' ? sel.id : 0;
    const nodeId = sel?.kind === 'node' ? sel.id : 0;
    map.setFilter('way-selected', ['==', ['get', 'id'], wayId]);
    map.setFilter('way-selected-core', ['==', ['get', 'id'], wayId]);
    map.setFilter('node-selected', ['==', ['get', 'id'], nodeId]);
    updateVerts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, props.selection, props.geo]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const L = props.layers;
    const vis = (on: boolean) => (on ? 'visible' : 'none');
    const set = (ids: string[], on: boolean) => ids.forEach((id) => map.setLayoutProperty(id, 'visibility', vis(on)));
    set(['road-casing', 'road-fill', 'oneway', 'road-label', 'junctions'], L.roads);
    set(['paths'], L.paths);
    set(['rail'], L.rail);
    set(['areas-fill'], L.buildings);
    set(['issues'], L.issues);
    const cats = [L.signals && 'signal', L.crossings && 'crossing', L.stops && 'stop', 'tagged'].filter(Boolean);
    map.setFilter('pois', ['in', ['get', 'category'], ['literal', cats]]);
  }, [ready, props.layers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setPaintProperty('base', 'raster-opacity', props.baseOpacity / 100);
  }, [ready, props.baseOpacity]);

  // tool change resets transient state
  useEffect(() => {
    draft.current = [];
    mergeFirst.current = null;
    renderDraft();
    const map = mapRef.current;
    if (map) map.getCanvas().style.cursor = props.tool === 'select' ? '' : 'crosshair';
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.tool]);

  const renderDraft = (cursor?: [number, number]) => {
    const map = mapRef.current;
    if (!map || !map.getSource('draft')) return;
    const pts = draft.current.map((p) => [p.lon, p.lat]);
    const line = cursor && pts.length ? [...pts, cursor] : pts;
    const feats: Feature[] = pts.map((c) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: {} }));
    if (line.length >= 2) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: line } as LineString, properties: {} });
    (map.getSource('draft') as GeoJSONSource).setData({ type: 'FeatureCollection', features: feats });
  };

  // --------------------------------------------------------- interaction
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;

    const pick = (e: MapMouseEvent) => {
      const bbox: [maplibregl.PointLike, maplibregl.PointLike] = [
        [e.point.x - HIT_PX, e.point.y - HIT_PX], [e.point.x + HIT_PX, e.point.y + HIT_PX]];
      const feats = map.queryRenderedFeatures(bbox, { layers: ['verts', 'pois', 'junctions', 'ways-hit', 'areas-fill'] });
      const byLayer = (ids: string[]) => feats.find((f: MapGeoJSONFeature) => ids.includes(f.layer.id));
      const vert = byLayer(['verts']);
      const node = vert ?? byLayer(['pois', 'junctions']);
      const way = byLayer(['ways-hit']) ?? byLayer(['areas-fill']);
      return {
        nodeId: node ? Number(node.properties.id) : undefined,
        wayId: way ? Number(way.properties.id) : undefined,
      };
    };

    const finishDraft = () => {
      const pts = draft.current;
      draft.current = [];
      renderDraft();
      if (pts.length < 2) return;
      propsRef.current.onOp({ op: 'add_way', args: {
        points: pts.map((p) => (p.id !== undefined ? { id: p.id } : p.way_id !== undefined
          ? { lat: p.lat, lon: p.lon, way_id: p.way_id } : { lat: p.lat, lon: p.lon })),
        tags: { highway: 'residential' } } }, { select: 'result' });
    };

    const onClick = (e: MapMouseEvent) => {
      if (drag.current?.moved) return;
      const p = propsRef.current;
      const { nodeId, wayId } = pick(e);
      const { lng, lat } = e.lngLat;
      switch (p.tool) {
        case 'select':
          if (nodeId !== undefined) p.onSelect({ kind: 'node', id: nodeId });
          else if (wayId !== undefined) p.onSelect({ kind: 'way', id: wayId });
          else p.onSelect(null);
          break;
        case 'node':
          if (wayId !== undefined) p.onOp({ op: 'insert_node', args: { way_id: wayId, lat, lon: lng } }, { select: 'result' });
          else p.onOp({ op: 'add_node', args: { lat, lon: lng } }, { select: 'result' });
          break;
        case 'signal':
        case 'crossing': {
          const tags = p.tool === 'signal' ? { highway: 'traffic_signals' } : { highway: 'crossing', crossing: 'marked' };
          if (nodeId !== undefined) p.onOp({ op: 'set_tags', args: { kind: 'node', id: nodeId, tags } });
          else if (wayId !== undefined) p.onOp({ op: 'insert_node', args: { way_id: wayId, lat, lon: lng, tags } }, { select: 'result' });
          break;
        }
        case 'split': {
          if (nodeId === undefined) { p.onHint('Кликните по внутреннему узлу ребра'); break; }
          const selWay = p.selection?.kind === 'way' ? p.selection.id : undefined;
          const candidates = p.geo?.ways.features.filter((f) => f.properties.nodes.includes(nodeId)) ?? [];
          const target = candidates.find((f) => f.properties.id === selWay)
            ?? candidates.find((f) => { const n = f.properties.nodes; return n.indexOf(nodeId) > 0 && n.indexOf(nodeId) < n.length - 1; });
          if (target) p.onOp({ op: 'split_way', args: { way_id: target.properties.id, node_id: nodeId } });
          else p.onHint('Узел не является внутренним ни для одного ребра');
          break;
        }
        case 'merge':
          if (nodeId === undefined) { p.onHint('Кликните по узлу'); break; }
          if (mergeFirst.current === null) {
            mergeFirst.current = nodeId;
            p.onSelect({ kind: 'node', id: nodeId });
            p.onHint('Теперь кликните по узлу, который нужно присоединить');
          } else {
            const keep = mergeFirst.current;
            mergeFirst.current = null;
            if (keep !== nodeId) p.onOp({ op: 'merge_nodes', args: { keep_id: keep, remove_id: nodeId } });
          }
          break;
        case 'reverse':
          if (wayId !== undefined) p.onOp({ op: 'reverse_way', args: { id: wayId } });
          break;
        case 'way': {
          const pt: DraftPoint = { lon: lng, lat };
          if (nodeId !== undefined) {
            const f = p.geo?.nodes.features.find((n) => n.properties.id === nodeId);
            const v = f?.geometry.coordinates ?? findVertex(p.geo, nodeId);
            if (v) { pt.lon = v[0]; pt.lat = v[1]; }
            pt.id = nodeId;
          } else if (wayId !== undefined && p.wayById(wayId)?.layer === 'road') {
            pt.way_id = wayId;
          }
          draft.current = [...draft.current, pt];
          renderDraft();
          break;
        }
      }
    };

    const onDbl = (e: MapMouseEvent) => {
      if (propsRef.current.tool === 'way') {
        e.preventDefault();
        finishDraft();
      }
    };

    const onMove = (e: MapMouseEvent) => {
      propsRef.current.onCursor([e.lngLat.lng, e.lngLat.lat], map.getZoom());
      const d = drag.current;
      if (d) {
        d.moved = true;
        d.lon = e.lngLat.lng;
        d.lat = e.lngLat.lat;
        // mousemove fires faster than the screen refreshes: draw at most once per frame
        if (!d.frame) d.frame = requestAnimationFrame(() => { d.frame = 0; drawDrag(d); });
        return;
      }
      if (propsRef.current.tool === 'way' && draft.current.length) renderDraft([e.lngLat.lng, e.lngLat.lat]);
    };

    const onDown = (e: MapMouseEvent) => {
      const p = propsRef.current;
      if (p.tool !== 'select' || e.originalEvent.button !== 0) return;
      const { nodeId } = pick(e);
      const draggable = nodeId !== undefined && (
        (p.selection?.kind === 'way' && p.wayById(p.selection.id)?.nodes.includes(nodeId)) ||
        (p.selection?.kind === 'node' && p.selection.id === nodeId));
      if (!draggable) return;
      e.preventDefault();
      map.dragPan.disable();
      const ways = (p.geo?.ways.features ?? [])
        .filter((f) => f.properties.nodes.includes(nodeId!))
        .map((f) => ({
          coords: f.geometry.coordinates.map((c) => [...c]),
          idx: f.properties.nodes.flatMap((n, i) => (n === nodeId ? [i] : [])),
        }));
      drag.current = { nodeId: nodeId!, moved: false, lon: e.lngLat.lng, lat: e.lngLat.lat, ways, frame: 0 };
    };

    const onUp = () => {
      const d = drag.current;
      if (!d) return;
      map.dragPan.enable();
      if (d.frame) { cancelAnimationFrame(d.frame); d.frame = 0; drawDrag(d); }
      if (d.moved) {
        // a successful move replaces the preview via the map update; a rejected one must drop it
        Promise.resolve(propsRef.current.onOp({ op: 'move_node', args: { id: d.nodeId, lat: d.lat, lon: d.lon } }, { select: 'keep' }))
          .then((ok) => { if (ok === false) clearDrag(); });
        setTimeout(() => { drag.current = null; }, 0);
      } else {
        drag.current = null;
      }
    };

    const onKey = (e: KeyboardEvent) => {
      if (propsRef.current.tool !== 'way') return;
      if (e.key === 'Enter') finishDraft();
      if (e.key === 'Escape') { draft.current = []; renderDraft(); }
      if (e.key === 'Backspace' && draft.current.length) { draft.current = draft.current.slice(0, -1); renderDraft(); }
    };

    map.on('click', onClick);
    map.on('dblclick', onDbl);
    map.on('mousemove', onMove);
    map.on('mousedown', onDown);
    map.on('mouseup', onUp);
    const onOut = () => propsRef.current.onCursor(null, map.getZoom());
    map.on('mouseout', onOut);
    window.addEventListener('keydown', onKey);
    return () => {
      map.off('click', onClick);
      map.off('dblclick', onDbl);
      map.off('mousemove', onMove);
      map.off('mousedown', onDown);
      map.off('mouseup', onUp);
      map.off('mouseout', onOut);
      window.removeEventListener('keydown', onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  const drawDrag = (d: NonNullable<typeof drag.current>) => {
    const map = mapRef.current;
    if (!map) return;
    const pos = [d.lon, d.lat];
    const feats: Feature[] = d.ways.map((w) => {
      for (const i of w.idx) w.coords[i] = pos;
      return { type: 'Feature', geometry: { type: 'LineString', coordinates: w.coords }, properties: {} };
    });
    feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: pos }, properties: {} });
    (map.getSource('drag') as GeoJSONSource).setData({ type: 'FeatureCollection', features: feats });
  };

  return <div ref={container} className="map" />;
});

function findVertex(geo: Geo | null, nodeId: number): number[] | undefined {
  if (!geo) return undefined;
  for (const f of geo.ways.features) {
    const i = f.properties.nodes.indexOf(nodeId);
    if (i >= 0) return f.geometry.coordinates[i];
  }
  return undefined;
}

export default memo(MapView);
