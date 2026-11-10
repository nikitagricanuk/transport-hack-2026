import { forwardRef, memo, useEffect, useImperativeHandle, useRef, useState } from 'react';
import maplibregl, { type GeoJSONSource, type MapGeoJSONFeature, type MapMouseEvent } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { Feature, FeatureCollection, LineString, Point } from 'geojson';
import type { ChangesResult, FixPreview, Geo, GeoDiff, Issue, LayerVisibility, Op, Peer, Selection, Tool, WayProps } from '../types';
import type { BaseMap, ImageOverlay } from './Extras';
import { LANE_MINZOOM, buildLanes, laneSplit, type LaneRoad } from '../utils/lanes';
import { joinHandles } from '../utils/joins';
import { claimDraftPointKey, isEscapeKey } from '../utils/keys';
import { PATH_LAYER_IDS, PATH_STYLES, casingColor, casingWidthExtra, classScale, linkFilter, pathFilter, roadLineColor, solidRoadFilter } from '../utils/roadStyle';
import { buildTrees } from '../utils/scenery';
import { defaultCamera } from '../utils/view';

export interface MapHandle {
  /** collaborators' cursors and selections; called often, does not re-render React */
  updatePeers: (peers: Peer[]) => void;
  /** move the camera, e.g. to follow a collaborator */
  jumpTo: (center: [number, number], zoom: number) => void;
  /** visible area as [west, south, east, north] */
  getBounds: () => [number, number, number, number] | undefined;
  zoomBy: (delta: number) => void;
  fitData: () => void;
  /** north-up, no tilt, and the default zoom (the loaded area, or the city view) */
  resetView: () => void;
  /** CSS fullscreen. F toggles it; Escape does not leave it. */
  toggleFullscreen: () => void;
  /** Points in the way currently being drawn. Zero when that draft is idle. */
  draftPointCount: () => number;
  flyTo: (lon: number, lat: number, zoom?: number) => void;
  fitBounds: (b: [number, number, number, number]) => void;
}

export type DrawGroup = 'zones' | 'buildings' | 'paths' | 'roads' | 'rail';

/** Transit route drawn over the map while a stop's route is opened. */
export interface RouteOverlay {
  colour: string;
  lines: number[][][];
  stops: { id: number; name: string; coord: number[] }[];
}

interface Props {
  geo: Geo | null;
  /** map groups from bottom to top */
  drawOrder: DrawGroup[];
  route: RouteOverlay | null;
  base: BaseMap;
  overlay: ImageOverlay | null;
  onOverlay: (o: ImageOverlay | null) => void;
  threeD: boolean;
  /** dry run of a fix under review: added (green), deleted (red, dashed), changed (blue) */
  fixPreview: FixPreview | null;
  /** elements to flash, e.g. those touched by a history entry */
  highlight: { kind: string; id: number }[];
  /** camera after each move, sent to collaborators who follow this editor */
  onView?: (center: [number, number], zoom: number) => void;
  /** set when `geo` came from a patch: lets the map update only the touched features */
  diff: GeoDiff | null;
  selection: Selection;
  tool: Tool;
  layers: LayerVisibility;
  baseOpacity: number;
  issues: Issue[];
  /** v/c per way id: roads are coloured by congestion while set */
  loadVc: Map<number, number> | null;
  /** edits vs the loaded file, drawn as "before" (dashed) and "after" overlays while set */
  changes: ChangesResult | null;
  onSelect: (s: Selection) => void;
  /** resolves false when the backend rejected the edit */
  onOp: (op: Op | Op[], opts?: { select?: 'result' | 'keep'; label?: string }) => void | Promise<boolean>;
  onHint: (text: string | null) => void;
  onCursor: (lonlat: [number, number] | null, zoom: number) => void;
  wayById: (id: number) => WayProps | undefined;
}

type DraftPoint = { lon: number; lat: number; id?: number; way_id?: number };

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
const HIT_PX = 6;

/** Width by lane count, highway class and zoom. `extra` px is added at every stop.
 * MapLibre only accepts ['zoom'] as the input of a top-level interpolate, so the
 * zoom expression is never wrapped in ['+', ...]. */
const laneWidth = (
  extra: number | maplibregl.ExpressionSpecification = 0,
): maplibregl.ExpressionSpecification => [
  'interpolate', ['exponential', 2], ['zoom'],
  13, ['+', ['*', ['get', 'lanes'], ['*', classScale(), 0.6]], extra],
  17, ['+', ['*', ['get', 'lanes'], ['*', classScale(), 3.2]], extra],
  20, ['+', ['*', ['get', 'lanes'], ['*', classScale(), 22]], extra],
];

const filterIf = (map: maplibregl.Map, id: string, filter: maplibregl.FilterSpecification) => {
  if (map.getLayer(id)) map.setFilter(id, filter);
};
const showIf = (map: maplibregl.Map, ids: string[], on: boolean) => {
  const visibility = on ? 'visible' : 'none';
  for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility);
};

function arrowImage(color = '#3F4652'): ImageData {
  const s = 24;
  const c = document.createElement('canvas');
  c.width = s;
  c.height = s;
  const ctx = c.getContext('2d')!;
  ctx.strokeStyle = color;
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
  // building whose corners are editable: a click selects a building as a whole,
  // a double click switches it to vertex editing until the selection changes
  const vertexEdit = useRef<number | null>(null);
  const drag = useRef<{
    mode: 'node' | 'way';
    nodeId?: number;
    wayId?: number;
    moved: boolean;
    startX: number;
    startY: number;
    lon: number;
    lat: number;
    originLon: number;
    originLat: number;
    ways: { base: number[][]; coords: number[][]; idx: number[] }[];
    frame: number;
  } | null>(null);

  const fitData = () => {
    const b = propsRef.current.geo?.bounds;
    if (b) mapRef.current?.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: 40, duration: 400 });
  };
  const resetView = () => {
    const map = mapRef.current;
    if (!map) return;
    const camera = defaultCamera(propsRef.current.geo?.bounds ?? null);
    if (camera.kind === 'fit') {
      map.fitBounds(camera.bounds, { padding: 40, duration: 400, bearing: camera.bearing, pitch: camera.pitch });
    } else {
      map.easeTo({
        center: camera.center, zoom: camera.zoom, bearing: camera.bearing, pitch: camera.pitch, duration: 400,
      });
    }
  };
  const [draftCount, setDraftCount] = useState(0);
  const finishRef = useRef<() => void>(() => undefined);
  const cancelDraft = () => {
    draft.current = [];
    renderDraft();
  };

  const peerMarkers = useRef(new Map<string, { marker: maplibregl.Marker; label: HTMLSpanElement }>());
  const peersRef = useRef<Peer[]>([]);
  const drawPeerSelections = () => {
    const map = mapRef.current;
    const geo = propsRef.current.geo;
    const src = map?.getSource('peer-sel') as GeoJSONSource | undefined;
    if (!map || !src) return;
    const feats: Feature[] = [];
    for (const peer of peersRef.current) {
      const sel = peer.selection;
      if (!sel || !geo) continue;
      if (sel.kind === 'way') {
        const line = geo.ways.features.find((f) => f.properties.id === sel.id);
        const area = line ? undefined : geo.areas.features.find((f) => f.properties.id === sel.id);
        const coords = line ? line.geometry.coordinates : area?.geometry.coordinates[0];
        if (coords) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: { color: peer.color } });
      } else {
        const node = geo.nodes.features.find((f) => f.properties.id === sel.id)?.geometry.coordinates ?? findVertex(geo, sel.id);
        if (node) feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: node }, properties: { color: peer.color } });
      }
    }
    src.setData({ type: 'FeatureCollection', features: feats });
  };
  const updatePeers = (peers: Peer[]) => {
    const map = mapRef.current;
    if (!map) return;
    peersRef.current = peers;
    const seen = new Set<string>();
    for (const peer of peers) {
      if (!peer.cursor) continue;
      seen.add(peer.id);
      let m = peerMarkers.current.get(peer.id);
      if (!m) {
        const el = document.createElement('div');
        el.className = 'peer-cursor';
        el.innerHTML = '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M2 1l13 7-6 1.6L6.4 16z" stroke="#fff" stroke-width="1.4"/></svg>';
        const label = document.createElement('span');
        el.appendChild(label);
        m = { marker: new maplibregl.Marker({ element: el, anchor: 'top-left' }).setLngLat(peer.cursor).addTo(map), label };
        peerMarkers.current.set(peer.id, m);
      }
      m.marker.setLngLat(peer.cursor);
      const el = m.marker.getElement();
      el.style.setProperty('--peer', peer.color);
      m.label.textContent = peer.name;
    }
    for (const [id, m] of peerMarkers.current) {
      if (!seen.has(id)) {
        m.marker.remove();
        peerMarkers.current.delete(id);
      }
    }
    drawPeerSelections();
  };

  useImperativeHandle(ref, () => ({
    updatePeers,
    jumpTo: (center, zoom) => mapRef.current?.easeTo({ center, zoom, duration: 300 }),
    getBounds: () => {
      const b = mapRef.current?.getBounds();
      return b ? [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()] : undefined;
    },
    zoomBy: (delta) => mapRef.current?.easeTo({ zoom: (mapRef.current?.getZoom() ?? 15) + delta, duration: 200 }),
    fitData,
    resetView,
    toggleFullscreen: () => toggleMapFullscreen(mapRef.current),
    draftPointCount: () => draft.current.length,
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
          sat: {
            type: 'raster',
            tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
            tileSize: 256,
            maxzoom: 19,
            attribution: 'Снимки © Esri, Maxar, Earthstar Geographics',
          },
        },
        layers: [
          { id: 'bg', type: 'background', paint: { 'background-color': '#ECEEE8' } },
          { id: 'base', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.5, 'raster-saturation': -0.6 } },
          { id: 'base-sat', type: 'raster', source: 'sat', layout: { visibility: 'none' }, paint: { 'raster-opacity': 0.5 } },
        ],
      },
      center: [104.28, 52.28],
      zoom: 15,
      maxZoom: 22,
      doubleClickZoom: false,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    // CSS fullscreen covers the map with its hints and toasts. It is not the
    // browser Fullscreen API, so Escape cannot leave it (Windows, macOS, iOS).
    map.addControl(new FullscreenToggle(), 'bottom-right');
    map.addControl(new FitControl(() => fitData()), 'bottom-right');
    map.addControl(new ResetViewControl(() => resetView()), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
    mapRef.current = map;
    if (import.meta.env.DEV || localStorage.getItem('uds-debug')) (window as unknown as { __udsMap?: maplibregl.Map }).__udsMap = map;
    const onFullscreenEscape = (e: KeyboardEvent) => {
      if (!mapIsFullscreen(map)) return;
      if (!isEscapeKey(e)) return;
      // Browsers that still honour preventDefault will not treat Escape as
      // "leave fullscreen". This mode is not native fullscreen, so the
      // shortcut has nothing to exit; the editor keeps Escape for cancel.
      e.preventDefault();
    };
    window.addEventListener('keydown', onFullscreenEscape, true);

    map.on('load', () => {
      map.addImage('arrow', arrowImage(), { pixelRatio: 2 });
      map.addImage('arrow-white', arrowImage('#F8FAFC'), { pixelRatio: 2 });
      for (const id of ['areas', 'ways', 'nodes', 'issues', 'verts', 'joins', 'draft', 'drag', 'cmp-before', 'cmp-after',
        'trees', 'peer-sel', 'lane-surface', 'lane-marks', 'lane-arrows', 'route-lines', 'route-stops', 'hist-hl', 'fix-preview']) {
        map.addSource(id, { type: 'geojson', data: EMPTY });
      }

      // translucent plans: the base map stays readable under buildings and zones
      map.addLayer({ id: 'zones-fill', type: 'fill', source: 'areas', filter: ['!=', ['get', 'layer'], 'building'],
        layout: { 'fill-sort-key': ['coalesce', ['get', 'z'], 0] },
        paint: { 'fill-color': ['match', ['coalesce', ['get', 'landuse', ['get', 'tags']], ''], 'residential', '#D9D5CB', 'industrial', '#CDCBD4', 'commercial', '#E2D3CB', 'forest', '#84AB79', '#9CCB86'], 'fill-opacity': 0.45 } });
      map.addLayer({ id: 'zones-line', type: 'line', source: 'areas', filter: ['!=', ['get', 'layer'], 'building'],
        paint: { 'line-color': '#6A9E55', 'line-width': 1, 'line-opacity': 0.8 } });
      map.addLayer({ id: 'tree-shadows', type: 'circle', source: 'trees', minzoom: 14,
        paint: { 'circle-radius': ['interpolate', ['exponential', 2], ['zoom'], 14, 1, 18, 8, 21, 40],
          'circle-color': '#284735', 'circle-opacity': 0.18, 'circle-translate': [2, 3], 'circle-pitch-alignment': 'map' } });
      map.addLayer({ id: 'tree-canopies', type: 'circle', source: 'trees', minzoom: 14,
        paint: { 'circle-radius': ['interpolate', ['exponential', 2], ['zoom'], 14, 1, 18, ['*', 6, ['get', 'size']], 21, ['*', 32, ['get', 'size']]],
          'circle-color': ['match', ['get', 'shade'], 0, '#689665', 1, '#80A76E', '#527E59'],
          'circle-stroke-color': '#496F4C', 'circle-stroke-width': 0.5, 'circle-opacity': 0.85, 'circle-pitch-alignment': 'map' } });
      map.addLayer({ id: 'buildings-fill', type: 'fill', source: 'areas', filter: ['==', ['get', 'layer'], 'building'],
        layout: { 'fill-sort-key': ['coalesce', ['get', 'z'], 0] },
        paint: { 'fill-color': '#B8B2A6', 'fill-opacity': 0.6 } });
      map.addLayer({ id: 'buildings-line', type: 'line', source: 'areas', filter: ['==', ['get', 'layer'], 'building'],
        paint: { 'line-color': '#8A8273', 'line-width': 1, 'line-opacity': 0.9 } });
      map.addLayer({ id: 'building-numbers', type: 'symbol', source: 'areas', minzoom: 15,
        filter: ['==', ['get', 'layer'], 'building'],
        layout: { 'text-field': ['coalesce', ['get', 'addr:housenumber', ['get', 'tags']], ''],
          'text-font': ['Open Sans Regular'], 'text-size': 11 },
        paint: { 'text-color': '#565C61', 'text-halo-color': '#F4F2EC', 'text-halo-width': 1.2 } });
      map.addLayer({ id: 'area-selected', type: 'line', source: 'areas', filter: ['==', ['get', 'id'], 0],
        paint: { 'line-color': '#2563EB', 'line-width': 2.5 } });
      for (const style of PATH_STYLES) {
        map.addLayer({
          id: style.id, type: 'line', source: 'ways', filter: pathFilter(style),
          layout: { 'line-cap': style.dash ? 'butt' : 'round', 'line-join': 'round' },
          paint: {
            'line-color': style.color,
            'line-width': style.width,
            ...(style.dash ? { 'line-dasharray': style.dash } : {}),
          },
        });
      }
      map.addLayer({ id: 'road-casing', type: 'line', source: 'ways', filter: ['==', ['get', 'layer'], 'road'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': casingColor(), 'line-width': laneWidth(casingWidthExtra()) } });
      map.addLayer({ id: 'road-fill', type: 'line', source: 'ways', filter: solidRoadFilter(),
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          // no v/c state (-1) -> class colour; otherwise green / yellow / orange / red by congestion
          'line-color': roadLineColor(),
          'line-width': laneWidth(),
        } });
      // Links stay out of the solid fill so the dash reads against the casing.
      map.addLayer({ id: 'road-link', type: 'line', source: 'ways', filter: linkFilter(),
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: { 'line-color': roadLineColor(), 'line-width': laneWidth(), 'line-dasharray': [2, 1.4] } });
      map.addLayer({ id: 'lane-surface', type: 'fill', source: 'lane-surface', minzoom: LANE_MINZOOM,
        paint: { 'fill-color': '#3F4652', 'fill-opacity': ['interpolate', ['linear'], ['zoom'], LANE_MINZOOM, 0, LANE_MINZOOM + 0.5, 0.92] } });
      map.addLayer({ id: 'lane-marks', type: 'line', source: 'lane-marks', minzoom: LANE_MINZOOM,
        filter: ['!=', ['get', 'kind'], 'lane'],
        paint: { 'line-color': '#F8FAFC', 'line-width': ['match', ['get', 'kind'], 'center', 2.2, 1.4] } });
      map.addLayer({ id: 'lane-dashes', type: 'line', source: 'lane-marks', minzoom: LANE_MINZOOM,
        filter: ['==', ['get', 'kind'], 'lane'],
        paint: { 'line-color': '#F8FAFC', 'line-width': 1.4, 'line-dasharray': [4, 4] } });
      map.addLayer({ id: 'lane-arrows', type: 'symbol', source: 'lane-arrows', minzoom: LANE_MINZOOM + 0.5,
        layout: { 'symbol-placement': 'line', 'symbol-spacing': 70, 'icon-image': 'arrow-white', 'icon-size': 0.85,
                  'icon-rotation-alignment': 'map', 'icon-allow-overlap': true } });
      map.addLayer({ id: 'rail', type: 'line', source: 'ways', filter: ['==', ['get', 'layer'], 'rail'],
        paint: { 'line-color': '#7C3AED', 'line-width': 2, 'line-dasharray': [3, 2] } });
      map.addLayer({ id: 'way-selected', type: 'line', source: 'ways', filter: ['==', ['get', 'id'], 0],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#2563EB', 'line-width': laneWidth(6), 'line-opacity': 0.35 } });
      map.addLayer({ id: 'way-selected-core', type: 'line', source: 'ways', filter: ['==', ['get', 'id'], 0],
        paint: { 'line-color': '#2563EB', 'line-width': 2.5 } });
      map.addLayer({ id: 'oneway', type: 'symbol', source: 'ways', minzoom: 15, maxzoom: LANE_MINZOOM + 0.5,
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

      map.addLayer({ id: 'cmp-before-line', type: 'line', source: 'cmp-before', filter: ['==', ['geometry-type'], 'LineString'],
        paint: { 'line-color': '#DC2626', 'line-width': 2.5, 'line-dasharray': [2, 1.5], 'line-opacity': 0.9 } });
      map.addLayer({ id: 'cmp-after-line', type: 'line', source: 'cmp-after', filter: ['==', ['geometry-type'], 'LineString'],
        layout: { 'line-cap': 'round' },
        paint: { 'line-color': ['match', ['get', 'status'], 'added', '#16A34A', '#F59E0B'], 'line-width': 5, 'line-opacity': 0.75 } });
      map.addLayer({ id: 'cmp-pts', type: 'circle', source: 'cmp-after', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 6, 'circle-color': ['match', ['get', 'status'], 'added', '#16A34A', '#F59E0B'],
                 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 1.5 } });
      map.addLayer({ id: 'cmp-before-pts', type: 'circle', source: 'cmp-before', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 5, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#DC2626', 'circle-stroke-width': 2 } });
      map.addLayer({ id: 'peer-sel-line', type: 'line', source: 'peer-sel', filter: ['==', ['geometry-type'], 'LineString'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': ['get', 'color'], 'line-width': 7, 'line-opacity': 0.45 } });
      map.addLayer({ id: 'peer-sel-pt', type: 'circle', source: 'peer-sel', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 3 } });
      // 3D: buildings extruded by storeys (3 m each), only while the 3D view is on
      map.addLayer({ id: 'buildings-3d', type: 'fill-extrusion', source: 'areas', filter: ['==', ['get', 'layer'], 'building'],
        layout: { visibility: 'none' },
        paint: { 'fill-extrusion-color': ['case', ['get', 'planned'], '#7DD3FC', '#C8C1B4'],
                 'fill-extrusion-height': ['*', ['max', 1, ['coalesce', ['get', 'levels'], 3]], 3],
                 'fill-extrusion-opacity': 0.85 } });
      // planned objects (uds:planned=yes): dashed cyan outline over their translucent body
      map.addLayer({ id: 'planned-line', type: 'line', source: 'ways', filter: ['==', ['get', 'planned'], true],
        paint: { 'line-color': '#06B6D4', 'line-width': 2, 'line-dasharray': [2, 1.5] } });
      map.addLayer({ id: 'planned-area', type: 'line', source: 'areas', filter: ['==', ['get', 'planned'], true],
        paint: { 'line-color': '#06B6D4', 'line-width': 2, 'line-dasharray': [2, 1.5] } });
      map.addLayer({ id: 'hist-hl', type: 'line', source: 'hist-hl', filter: ['==', ['geometry-type'], 'LineString'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#F59E0B', 'line-width': 9, 'line-opacity': 0.55 } });
      map.addLayer({ id: 'hist-hl-pt', type: 'circle', source: 'hist-hl', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 10, 'circle-color': 'rgba(245,158,11,0.35)', 'circle-stroke-color': '#F59E0B', 'circle-stroke-width': 2 } });
      const FIX_COLOUR = ['match', ['get', 'status'], 'added', '#22C55E', 'deleted', '#EF4444', '#3B82F6'];
      // line-dasharray cannot depend on data, so deletions get their own dashed layer
      map.addLayer({ id: 'fix-preview-line', type: 'line', source: 'fix-preview',
        filter: ['all', ['==', ['geometry-type'], 'LineString'], ['!=', ['get', 'status'], 'deleted']],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': FIX_COLOUR as never, 'line-width': 7, 'line-opacity': 0.85 } });
      map.addLayer({ id: 'fix-preview-del', type: 'line', source: 'fix-preview',
        filter: ['all', ['==', ['geometry-type'], 'LineString'], ['==', ['get', 'status'], 'deleted']],
        paint: { 'line-color': '#EF4444', 'line-width': 7, 'line-opacity': 0.9, 'line-dasharray': [1.2, 1] } });
      map.addLayer({ id: 'fix-preview-pt', type: 'circle', source: 'fix-preview', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 7, 'circle-color': FIX_COLOUR as never, 'circle-stroke-color': '#0B1220', 'circle-stroke-width': 2 } });
      map.addLayer({ id: 'route-lines', type: 'line', source: 'route-lines',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': ['get', 'colour'], 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 4, 18, 9], 'line-opacity': 0.75 } });
      map.addLayer({ id: 'route-stops', type: 'circle', source: 'route-stops',
        paint: { 'circle-radius': 6, 'circle-color': '#FFFFFF', 'circle-stroke-color': ['get', 'colour'], 'circle-stroke-width': 3 } });
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
      // Shared junction of the selection. A click pulls the other roads off this point.
      map.addLayer({ id: 'join-handles', type: 'circle', source: 'joins',
        paint: { 'circle-radius': 8, 'circle-color': '#F97316', 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2 } });
      map.addLayer({ id: 'drag-pt', type: 'circle', source: 'drag', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 6, 'circle-color': '#2563EB', 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2 } });
      map.addLayer({ id: 'draft-line', type: 'line', source: 'draft', filter: ['==', ['geometry-type'], 'LineString'],
        paint: { 'line-color': '#2563EB', 'line-width': 3, 'line-dasharray': [2, 1] } });
      map.addLayer({ id: 'draft-pts', type: 'circle', source: 'draft', filter: ['==', ['geometry-type'], 'Point'],
        paint: { 'circle-radius': 5, 'circle-color': '#2563EB', 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2 } });
      setReady(true);
    });

    return () => {
      window.removeEventListener('keydown', onFullscreenEscape, true);
      document.documentElement.classList.remove(FULLSCREEN_ROOT);
      map.getContainer().parentElement?.classList.remove(FULLSCREEN_CLASS);
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
    (map.getSource('trees') as GeoJSONSource).setData(buildTrees(geo));
    rendered.current = geo;
    clearDrag();
    drawPeerSelections();
    const key = JSON.stringify(props.geo.bounds);
    if (props.geo.bounds && fitted.current === null) {
      fitted.current = key;
      const b = props.geo.bounds;
      map.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: 40, duration: 0 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, props.geo]);

  // congestion colouring through feature-state: no re-upload of the road source
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !props.geo) return;
    map.removeFeatureState({ source: 'ways' });
    if (!props.loadVc) return;
    for (const f of props.geo.ways.features) {
      const vc = props.loadVc.get(f.properties.id);
      if (vc !== undefined && f.id !== undefined) map.setFeatureState({ source: 'ways', id: f.id }, { vc });
    }
  }, [ready, props.loadVc, props.geo]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const before: Feature[] = [];
    const after: Feature[] = [];
    const geom = (c: number[][] | number[]) => (typeof c[0] === 'number'
      ? { type: 'Point' as const, coordinates: c as number[] }
      : { type: 'LineString' as const, coordinates: c as number[][] });
    for (const c of props.changes?.changes ?? []) {
      if (c.kind === 'relation') continue;
      const props_ = { status: c.status };
      if (c.before_coords && (c.status === 'deleted' || c.geometry)) {
        before.push({ type: 'Feature', geometry: geom(c.before_coords), properties: props_ });
      }
      if (c.after_coords && c.status !== 'deleted') {
        after.push({ type: 'Feature', geometry: geom(c.after_coords), properties: props_ });
      }
    }
    (map.getSource('cmp-before') as GeoJSONSource).setData({ type: 'FeatureCollection', features: before });
    (map.getSource('cmp-after') as GeoJSONSource).setData({ type: 'FeatureCollection', features: after });
  }, [ready, props.changes]);

  // base map: OSM scheme, satellite imagery or none
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setLayoutProperty('base', 'visibility', props.base === 'osm' ? 'visible' : 'none');
    map.setLayoutProperty('base-sat', 'visibility', props.base === 'sat' ? 'visible' : 'none');
  }, [ready, props.base]);

  // planned objects are drawn translucent
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const translucent = (solid: number, planned: number): maplibregl.ExpressionSpecification =>
      ['case', ['==', ['get', 'planned'], true], planned, solid];
    for (const id of ['road-fill', 'road-casing', 'road-link']) {
      if (map.getLayer(id)) map.setPaintProperty(id, 'line-opacity', translucent(1, 0.4));
    }
    if (map.getLayer('buildings-fill')) map.setPaintProperty('buildings-fill', 'fill-opacity', translucent(0.6, 0.2));
    if (map.getLayer('zones-fill')) map.setPaintProperty('zones-fill', 'fill-opacity', translucent(0.45, 0.15));
  }, [ready]);

  // 3D view: tilt the camera and extrude buildings
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setLayoutProperty('buildings-3d', 'visibility', props.threeD ? 'visible' : 'none');
    map.setLayoutProperty('buildings-fill', 'visibility', props.threeD || !props.layers.buildings ? 'none' : 'visible');
    map.easeTo({ pitch: props.threeD ? 55 : 0, duration: 600 });
  }, [ready, props.threeD, props.layers.buildings]);

  // history highlight
  useEffect(() => {
    const map = mapRef.current;
    const geo = props.geo;
    if (!ready || !map || !geo) return;
    const feats: Feature[] = [];
    for (const r of props.highlight) {
      if (r.kind === 'way') {
        const line = geo.ways.features.find((f) => f.properties.id === r.id);
        const area = line ? undefined : geo.areas.features.find((f) => f.properties.id === r.id);
        const coords = line ? line.geometry.coordinates : area?.geometry.coordinates[0];
        if (coords) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} });
      } else if (r.kind === 'node') {
        const c = geo.nodes.features.find((f) => f.properties.id === r.id)?.geometry.coordinates ?? findVertex(geo, r.id);
        if (c) feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: {} });
      }
    }
    (map.getSource('hist-hl') as GeoJSONSource).setData({ type: 'FeatureCollection', features: feats });
  }, [ready, props.highlight, props.geo]);

  // fix under review
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource('fix-preview') as GeoJSONSource).setData(
      props.fixPreview ? { type: 'FeatureCollection', features: props.fixPreview.features } : EMPTY);
    // above roads and selection, which the draw-order effect keeps moving up
    if (props.fixPreview) {
      for (const id of ['fix-preview-del', 'fix-preview-line', 'fix-preview-pt']) if (map.getLayer(id)) map.moveLayer(id);
    }
  }, [ready, props.fixPreview]);

  // camera for followers
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const onMoveEnd = () => {
      const c = map.getCenter();
      propsRef.current.onView?.([c.lng, c.lat], map.getZoom());
    };
    map.on('moveend', onMoveEnd);
    return () => { map.off('moveend', onMoveEnd); };
  }, [ready]);

  // georeferenced screenshot: image source with four draggable corners
  const overlayMarkers = useRef<maplibregl.Marker[]>([]);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const o = props.overlay;
    const src = map.getSource('overlay') as maplibregl.ImageSource | undefined;
    if (!o) {
      overlayMarkers.current.forEach((m) => m.remove());
      overlayMarkers.current = [];
      if (map.getLayer('overlay')) map.removeLayer('overlay');
      if (src) map.removeSource('overlay');
      return;
    }
    const coords = o.corners as [[number, number], [number, number], [number, number], [number, number]];
    if (!src) {
      map.addSource('overlay', { type: 'image', url: o.url, coordinates: coords });
      map.addLayer({ id: 'overlay', type: 'raster', source: 'overlay', paint: { 'raster-opacity': o.opacity } }, 'zones-fill');
    } else {
      src.setCoordinates(coords);
    }
    map.setPaintProperty('overlay', 'raster-opacity', o.opacity);
    if (overlayMarkers.current.length !== 4) {
      overlayMarkers.current.forEach((m) => m.remove());
      overlayMarkers.current = coords.map((c, i) => {
        const el = document.createElement('div');
        el.className = 'overlay-corner';
        el.title = 'Перетащите, чтобы совместить снимок с картой';
        const m = new maplibregl.Marker({ element: el, draggable: true }).setLngLat(c).addTo(map);
        const update = () => {
          const cur = propsRef.current.overlay;
          if (!cur) return;
          const ll = m.getLngLat();
          const corners = cur.corners.map((x, j) => (j === i ? [ll.lng, ll.lat] : x)) as [number, number][];
          (map.getSource('overlay') as maplibregl.ImageSource | undefined)?.setCoordinates(
            corners as [[number, number], [number, number], [number, number], [number, number]]);
        };
        m.on('drag', update);
        m.on('dragend', () => {
          const cur = propsRef.current.overlay;
          if (!cur) return;
          const ll = m.getLngLat();
          propsRef.current.onOverlay({ ...cur, corners: cur.corners.map((x, j) => (j === i ? [ll.lng, ll.lat] : x)) as [number, number][] });
        });
        return m;
      });
    } else {
      overlayMarkers.current.forEach((m, i) => m.setLngLat(coords[i]));
    }
  }, [ready, props.overlay]);

  // groups from bottom to top: each is moved just under the selection highlight, so the
  // last one ends up on top
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const ids: Record<DrawGroup, string[]> = {
      zones: ['zones-fill', 'zones-line', 'tree-shadows', 'tree-canopies'],
      buildings: ['buildings-fill', 'buildings-line', 'building-numbers'],
      paths: PATH_LAYER_IDS,
      roads: ['road-casing', 'road-fill', 'road-link', 'lane-surface', 'lane-marks', 'lane-dashes', 'lane-arrows', 'oneway', 'road-label'],
      rail: ['rail'],
    };
    for (const g of props.drawOrder) {
      for (const id of ids[g]) if (map.getLayer(id)) map.moveLayer(id, 'way-selected');
    }
    if (map.getLayer('area-selected')) map.moveLayer('area-selected', 'way-selected');
  }, [ready, props.drawOrder]);

  // physical view: roads at real width with lanes, markings and travel direction
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !props.geo) return;
    const roads: LaneRoad[] = props.geo.ways.features
      .filter((f) => f.properties.layer === 'road')
      .map((f) => ({ id: f.properties.id, coords: f.geometry.coordinates, ...laneSplit(f.properties.tags, f.properties.dir) }));
    const g = buildLanes(roads);
    (map.getSource('lane-surface') as GeoJSONSource).setData(g.surface);
    (map.getSource('lane-marks') as GeoJSONSource).setData(g.marks);
    (map.getSource('lane-arrows') as GeoJSONSource).setData(g.arrows);
  }, [ready, props.geo]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const r = props.route;
    const colour = r?.colour || '#E11D48';
    (map.getSource('route-lines') as GeoJSONSource).setData({ type: 'FeatureCollection', features: (r?.lines ?? []).map((c) => ({
      type: 'Feature', geometry: { type: 'LineString', coordinates: c }, properties: { colour } })) });
    (map.getSource('route-stops') as GeoJSONSource).setData({ type: 'FeatureCollection', features: (r?.stops ?? []).map((st) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: st.coord }, properties: { colour, name: st.name } })) });
    if (r && r.lines.length) {
      const all = r.lines.flat();
      const lons = all.map((c) => c[0]);
      const lats = all.map((c) => c[1]);
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 60, duration: 500, maxZoom: 17 });
    }
  }, [ready, props.route]);

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
      const line = p.geo?.ways.features.find((w) => w.properties.id === sel.id);
      const area = line ? undefined : p.geo?.areas.features.find((w) => w.properties.id === sel.id);
      const f = line ?? area;
      const wholeBuilding = area?.properties.layer === 'building' && vertexEdit.current !== sel.id;
      if (f && !wholeBuilding) {
        const coords = line ? line.geometry.coordinates : area!.geometry.coordinates[0];
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
    if (vertexEdit.current !== null && vertexEdit.current !== wayId) {
      vertexEdit.current = null;
      propsRef.current.onHint(null);  // drop the «Режим вершин» hint
    }
    filterIf(map, 'way-selected', ['==', ['get', 'id'], wayId]);
    filterIf(map, 'way-selected-core', ['==', ['get', 'id'], wayId]);
    filterIf(map, 'node-selected', ['==', ['get', 'id'], nodeId]);
    filterIf(map, 'area-selected', ['==', ['get', 'id'], wayId]);
    updateVerts();
    const handles = joinHandles(props.geo, props.selection);
    (map.getSource('joins') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: handles.map((handle) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [handle.lon, handle.lat] },
        properties: { id: handle.id },
      })),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, props.selection, props.geo]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const L = props.layers;
    showIf(map, ['road-casing', 'road-fill', 'road-link', 'oneway', 'road-label', 'junctions',
      'lane-surface', 'lane-marks', 'lane-dashes', 'lane-arrows'], L.roads);
    showIf(map, PATH_LAYER_IDS, L.paths);
    showIf(map, ['rail'], L.rail);
    showIf(map, ['zones-fill', 'zones-line', 'tree-shadows', 'tree-canopies', 'buildings-fill', 'buildings-line', 'building-numbers'], L.buildings);
    showIf(map, ['issues'], L.issues);
    const cats = [L.signals && 'signal', L.crossings && 'crossing', L.stops && 'stop', 'tagged'].filter(Boolean);
    filterIf(map, 'pois', ['in', ['get', 'category'], ['literal', cats]]);
  }, [ready, props.layers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.setPaintProperty('base', 'raster-opacity', props.baseOpacity / 100);
    map.setPaintProperty('base-sat', 'raster-opacity', props.baseOpacity / 100);
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
    setDraftCount(draft.current.length);
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
      const feats = map.queryRenderedFeatures(bbox, {
        layers: ['join-handles', 'verts', 'pois', 'junctions', 'ways-hit', 'buildings-fill', 'zones-fill'] });
      const byLayer = (ids: string[]) => feats.find((f: MapGeoJSONFeature) => ids.includes(f.layer.id));
      const vert = byLayer(['verts']);
      const node = vert ?? byLayer(['pois', 'junctions']);
      // the topmost area under the cursor wins, so a house over a lawn picks the house;
      // among buildings a click takes the whole building, Ctrl+click its part («корпус», building:part)
      const areas = feats.filter((f: MapGeoJSONFeature) => f.layer.id === 'buildings-fill' || f.layer.id === 'zones-fill');
      const isPart = (f: MapGeoJSONFeature) => !!propsRef.current.wayById(Number(f.properties.id))?.tags['building:part'];
      const wantPart = e.originalEvent.ctrlKey || e.originalEvent.metaKey;
      const area = areas.find((f) => (f.layer.id === 'buildings-fill' ? isPart(f) === wantPart : false))
        ?? areas[0];
      const way = byLayer(['ways-hit']) ?? area;
      const join = byLayer(['join-handles']);
      return {
        nodeId: node ? Number(node.properties.id) : undefined,
        wayId: way ? Number(way.properties.id) : undefined,
        joinId: join ? Number(join.properties.id) : undefined,
      };
    };

    const finishDraft = (closed = false) => {
      const pts = draft.current;
      draft.current = [];
      renderDraft();
      if (pts.length < 2) return;
      propsRef.current.onOp({ op: 'add_way', args: {
        points: pts.map((p) => (p.id !== undefined ? { id: p.id } : p.way_id !== undefined
          ? { lat: p.lat, lon: p.lon, way_id: p.way_id } : { lat: p.lat, lon: p.lon })),
        tags: { highway: 'residential' }, ...(closed ? { closed: true } : {}) } }, { select: 'result' });
    };

    const onClick = (e: MapMouseEvent) => {
      if (drag.current?.moved) return;
      const p = propsRef.current;
      const { nodeId, wayId, joinId } = pick(e);
      const { lng, lat } = e.lngLat;
      switch (p.tool) {
        case 'select': {
          if (joinId !== undefined) {
            p.onOp({ op: 'separate_ways', args: { node_id: joinId } });
            break;
          }
          if (nodeId !== undefined) p.onSelect({ kind: 'node', id: nodeId });
          else if (wayId !== undefined) p.onSelect({ kind: 'way', id: wayId });
          else p.onSelect(null);
          break;
        }
        case 'node':
          if (wayId !== undefined) p.onOp({ op: 'insert_node', args: { way_id: wayId, lat, lon: lng } }, { select: 'result' });
          else p.onOp({ op: 'add_node', args: { lat, lon: lng } }, { select: 'result' });
          break;
        case 'signal':
          // only junctions and crossings; a click near one snaps to it (server rule)
          if (nodeId !== undefined) p.onOp({ op: 'place_signal', args: { node_id: nodeId } }, { select: 'result', label: 'Светофор' });
          else if (wayId !== undefined && p.wayById(wayId)?.layer === 'road') {
            p.onOp({ op: 'place_signal', args: { way_id: wayId, lat, lon: lng } }, { select: 'result', label: 'Светофор' });
          } else p.onHint('Светофор ставится на перекрёсток или пешеходный переход — кликните по дороге рядом с ним');
          break;
        case 'crossing': {
          const signal = false;
          if (nodeId !== undefined) {
            const cur = p.geo?.nodes.features.find((n) => n.properties.id === nodeId)?.properties.tags ?? {};
            // a signal on a crossing makes it a signalised crossing instead of erasing the crossing
            const signalised = signal ? cur.highway === 'crossing' : cur.highway === 'traffic_signals';
            const tags: Record<string, string> = signalised
              ? { highway: 'crossing', crossing: 'traffic_signals' }
              : signal ? { highway: 'traffic_signals' } : { highway: 'crossing', crossing: 'marked' };
            const ops: Op[] = [{ op: 'set_tags', args: { kind: 'node', id: nodeId, tags } }];
            const onRoad = p.geo?.ways.features.some((f) => f.properties.layer === 'road' && f.properties.nodes.includes(nodeId));
            if (!onRoad) ops.push({ op: 'attach_to_road', args: { node_id: nodeId } });  // snap onto the nearest road
            p.onOp(ops, { select: 'result', label: signalised ? 'Переход со светофором' : signal ? 'Светофор' : 'Пешеходный переход' });
          } else if (wayId !== undefined) {
            if (p.wayById(wayId)?.layer !== 'road') { p.onHint('Светофор и переход ставятся на проезжую часть — кликните по дороге'); break; }
            const tags = signal ? { highway: 'traffic_signals' } : { highway: 'crossing', crossing: 'marked' };
            p.onOp({ op: 'insert_node', args: { way_id: wayId, lat, lon: lng, tags } }, { select: 'result' });
          }
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
          const prev = draft.current;
          // a click on the point just placed (a second click in place) ends the road there
          const last = prev[prev.length - 1];
          if (last && pt.id !== undefined && last.id === pt.id) { finishDraft(); break; }
          draft.current = [...prev, pt];
          // reaching an existing point (a node of the graph, or the draft's own first point)
          // ends the road: it is joined to that node and becomes one connected object
          const first = prev[0];
          const closesLoop = first !== undefined && prev.length >= 2
            && Math.hypot(e.point.x - map.project([first.lon, first.lat]).x, e.point.y - map.project([first.lon, first.lat]).y) < HIT_PX;
          if (closesLoop) {
            draft.current = prev;
            finishDraft(true);
            p.onHint('Контур замкнут и завершён');
          } else if (prev.length >= 1 && pt.id !== undefined) {
            finishDraft();
            p.onHint('Ребро присоединено к существующей точке и завершено');
          } else renderDraft();
          break;
        }
      }
    };

    const onDbl = (e: MapMouseEvent) => {
      const p = propsRef.current;
      if (p.tool === 'way') {
        e.preventDefault();
        finishDraft();
        return;
      }
      if (p.tool !== 'select') return;
      // double click on a building: edit its corners (vector mode)
      const { wayId } = pick(e);
      if (wayId === undefined || p.wayById(wayId)?.layer !== 'building') return;
      e.preventDefault();
      vertexEdit.current = wayId;
      p.onSelect({ kind: 'way', id: wayId });
      updateVerts();
      p.onHint('Режим вершин: перетаскивайте точки здания · клик мимо или Esc — выход');
    };

    const onMove = (e: MapMouseEvent) => {
      propsRef.current.onCursor([e.lngLat.lng, e.lngLat.lat], map.getZoom());
      const d = drag.current;
      if (d) {
        // A click on the orange split point must not count as a drag.
        if (!d.moved && Math.hypot(e.point.x - d.startX, e.point.y - d.startY) < 4) return;
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
      const { nodeId, wayId } = pick(e);
      const shapes = [
        ...(p.geo?.ways.features ?? []).map((f) => ({ nodes: f.properties.nodes, coords: f.geometry.coordinates })),
        ...(p.geo?.areas.features ?? []).map((f) => ({ nodes: f.properties.nodes, coords: f.geometry.coordinates[0] })),
      ];
      const copy = (nodes: number[], coords: number[][], moving: (id: number) => boolean) => ({
        base: coords.map((c) => [...c]),
        coords: coords.map((c) => [...c]),
        idx: nodes.flatMap((n, i) => (moving(n) ? [i] : [])),
      });
      // a selected building moves as a whole until a double click turns on its corners
      const selWay = p.selection?.kind === 'way' ? p.wayById(p.selection.id) : undefined;
      const cornersLocked = selWay?.layer === 'building' && vertexEdit.current !== selWay.id;
      const vertexDrag = nodeId !== undefined && !cornersLocked && (
        (selWay?.nodes.includes(nodeId)) ||
        (p.selection?.kind === 'node' && p.selection.id === nodeId));
      if (vertexDrag) {
        e.preventDefault();
        map.dragPan.disable();
        drag.current = {
          mode: 'node', nodeId, moved: false, startX: e.point.x, startY: e.point.y,
          lon: e.lngLat.lng, lat: e.lngLat.lat, originLon: e.lngLat.lng, originLat: e.lngLat.lat,
          ways: shapes.filter((f) => f.nodes.includes(nodeId!)).map((f) => copy(f.nodes, f.coords, (n) => n === nodeId)),
          frame: 0,
        };
        return;
      }
      // Dragging the selected line or polygon, not one corner, moves every node of it.
      const selectedId = p.selection?.kind === 'way' ? p.selection.id : undefined;
      if (selectedId === undefined || wayId !== selectedId) return;
      const way = p.wayById(selectedId);
      if (!way) return;
      e.preventDefault();
      map.dragPan.disable();
      const ids = new Set(way.nodes);
      drag.current = {
        mode: 'way', wayId: selectedId, moved: false, startX: e.point.x, startY: e.point.y,
        lon: e.lngLat.lng, lat: e.lngLat.lat, originLon: e.lngLat.lng, originLat: e.lngLat.lat,
        ways: shapes.filter((f) => f.nodes.some((n) => ids.has(n))).map((f) => copy(f.nodes, f.coords, (n) => ids.has(n))),
        frame: 0,
      };
    };

    const onUp = () => {
      const d = drag.current;
      if (!d) return;
      map.dragPan.enable();
      if (d.frame) { cancelAnimationFrame(d.frame); d.frame = 0; drawDrag(d); }
      if (d.moved) {
        const op = d.mode === 'way'
          ? { op: 'move_way', args: { id: d.wayId, dlat: d.lat - d.originLat, dlon: d.lon - d.originLon } }
          : { op: 'move_node', args: { id: d.nodeId, lat: d.lat, lon: d.lon } };
        // a successful move replaces the preview via the map update; a rejected one must drop it
        Promise.resolve(propsRef.current.onOp(op, { select: 'keep' }))
          .then((ok) => { if (ok === false) clearDrag(); });
        setTimeout(() => { drag.current = null; }, 0);
      } else {
        drag.current = null;
      }
    };

    finishRef.current = finishDraft;
    const onKey = (e: KeyboardEvent) => {
      if (propsRef.current.tool !== 'way' || !draft.current.length) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return;
      // macOS can swallow Esc (fullscreen, system shortcuts); check every form and offer
      // the on-map buttons and right click as alternatives
      const esc = e.key === 'Escape' || e.key === 'Esc' || e.code === 'Escape' || e.keyCode === 27;
      if (e.key === 'Enter') { e.preventDefault(); finishDraft(); }
      else if (esc) { e.preventDefault(); e.stopPropagation(); cancelDraft(); }
      else if (e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        e.stopPropagation();
        // stopPropagation() does not skip other listeners on this same window.
        // Stop them too, and claim the key, so a selected object is not deleted.
        e.stopImmediatePropagation();
        claimDraftPointKey();
        draft.current = draft.current.slice(0, -1);
        renderDraft();
      }
    };
    // right click while drawing cancels the line (works with any keyboard)
    const onContext = (e: MapMouseEvent) => {
      if (propsRef.current.tool === 'way' && draft.current.length) {
        e.preventDefault();
        cancelDraft();
      }
    };
    map.on('contextmenu', onContext);

    map.on('click', onClick);
    map.on('dblclick', onDbl);
    map.on('mousemove', onMove);
    map.on('mousedown', onDown);
    map.on('mouseup', onUp);
    const onOut = () => propsRef.current.onCursor(null, map.getZoom());
    map.on('mouseout', onOut);
    window.addEventListener('keydown', onKey, true);
    return () => {
      map.off('click', onClick);
      map.off('dblclick', onDbl);
      map.off('mousemove', onMove);
      map.off('mousedown', onDown);
      map.off('mouseup', onUp);
      map.off('mouseout', onOut);
      window.removeEventListener('keydown', onKey, true);
      map.off('contextmenu', onContext);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  const drawDrag = (d: NonNullable<typeof drag.current>) => {
    const map = mapRef.current;
    if (!map) return;
    const dlon = d.lon - d.originLon;
    const dlat = d.lat - d.originLat;
    const feats: Feature[] = d.ways.map((w) => {
      for (const i of w.idx) {
        w.coords[i] = d.mode === 'node' ? [d.lon, d.lat] : [w.base[i][0] + dlon, w.base[i][1] + dlat];
      }
      return { type: 'Feature', geometry: { type: 'LineString', coordinates: w.coords }, properties: {} };
    });
    if (d.mode === 'node') {
      feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [d.lon, d.lat] }, properties: {} });
    }
    (map.getSource('drag') as GeoJSONSource).setData({ type: 'FeatureCollection', features: feats });
  };

  return (
    <>
      <div ref={container} className="map" />
      {props.tool === 'way' && draftCount > 0 && (
        <div className="draft-bar" role="group" aria-label="Рисование ребра">
          <span className="muted small">точек: {draftCount}</span>
          <button className="btn small" onClick={() => { draft.current = draft.current.slice(0, -1); renderDraft(); }}>
            Шаг назад <kbd>⌫</kbd>
          </button>
          <button className="btn small" onClick={cancelDraft}>Отменить <kbd>Esc</kbd> / ПКМ</button>
          <button className="btn small primary" onClick={() => finishRef.current()} disabled={draftCount < 2}>
            Готово <kbd>Enter</kbd>
          </button>
        </div>
      )}
    </>
  );
});

const FULLSCREEN_CLASS = 'is-fullscreen';
const FULLSCREEN_ROOT = 'map-is-fullscreen';
const FULLSCREEN_EVENT = 'uds-fullscreen';

function wrapOf(map: maplibregl.Map | null): HTMLElement | null {
  return map?.getContainer().parentElement ?? null;
}

function mapIsFullscreen(map: maplibregl.Map | null): boolean {
  return wrapOf(map)?.classList.contains(FULLSCREEN_CLASS) ?? false;
}

function toggleMapFullscreen(map: maplibregl.Map | null): void {
  const wrap = wrapOf(map);
  if (!wrap || !map) return;
  const on = !wrap.classList.contains(FULLSCREEN_CLASS);
  wrap.classList.toggle(FULLSCREEN_CLASS, on);
  document.documentElement.classList.toggle(FULLSCREEN_ROOT, on);
  wrap.getBoundingClientRect();
  map.resize();
  requestAnimationFrame(() => map.resize());
  window.dispatchEvent(new Event(FULLSCREEN_EVENT));
}

/** Map button: fullscreen of the map area. F toggles the same mode. */
class FullscreenToggle implements maplibregl.IControl {
  private button: HTMLButtonElement | null = null;
  private map: maplibregl.Map | null = null;
  private onChange = () => this.sync();

  onAdd(map: maplibregl.Map) {
    this.map = map;
    const el = document.createElement('div');
    el.className = 'maplibregl-ctrl maplibregl-ctrl-group';
    const b = document.createElement('button');
    b.type = 'button';
    const icon = document.createElement('span');
    icon.className = 'maplibregl-ctrl-icon';
    icon.setAttribute('aria-hidden', 'true');
    b.appendChild(icon);
    b.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      toggleMapFullscreen(map);
    };
    el.appendChild(b);
    this.button = b;
    this.sync();
    window.addEventListener(FULLSCREEN_EVENT, this.onChange);
    return el;
  }

  onRemove() {
    window.removeEventListener(FULLSCREEN_EVENT, this.onChange);
    this.button = null;
    this.map = null;
  }

  private sync() {
    const on = mapIsFullscreen(this.map);
    if (!this.button) return;
    this.button.className = on ? 'maplibregl-ctrl-shrink' : 'maplibregl-ctrl-fullscreen';
    this.button.title = on ? 'Выйти из полного экрана (F)' : 'Полный экран (F). Выход — снова F, не Esc';
    this.button.setAttribute('aria-label', on ? 'Выйти из полного экрана' : 'Полный экран');
    this.button.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

/** Map button: default zoom, bearing and pitch. */
class ResetViewControl implements maplibregl.IControl {
  private el: HTMLDivElement | null = null;
  constructor(private onReset: () => void) {}
  onAdd() {
    const el = document.createElement('div');
    el.className = 'maplibregl-ctrl maplibregl-ctrl-group';
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'reset-view-btn';
    b.title = 'Масштаб и угол обзора по умолчанию';
    b.setAttribute('aria-label', 'Вернуть масштаб и угол обзора');
    b.innerHTML = '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><circle cx="10" cy="10" r="7.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path fill="currentColor" d="M10 3.1 12.1 9.2 10 8.1 7.9 9.2z"/><path fill="currentColor" opacity="0.35" d="M10 16.9 7.9 10.8 10 11.9 12.1 10.8z"/></svg>';
    b.onclick = (ev) => {
      ev.preventDefault();
      this.onReset();
    };
    el.appendChild(b);
    this.el = el;
    return el;
  }
  onRemove() {
    this.el?.remove();
  }
}

/** Map button: zoom back to the whole loaded area. */
class FitControl implements maplibregl.IControl {
  private el: HTMLDivElement | null = null;
  constructor(private onFit: () => void) {}
  onAdd() {
    const el = document.createElement('div');
    el.className = 'maplibregl-ctrl maplibregl-ctrl-group';
    const b = document.createElement('button');
    b.type = 'button';
    b.title = 'Показать весь участок (0)';
    b.setAttribute('aria-label', 'Показать весь участок');
    b.className = 'fit-btn';
    b.textContent = '⤢';
    b.onclick = () => this.onFit();
    el.appendChild(b);
    this.el = el;
    return el;
  }
  onRemove() {
    this.el?.remove();
  }
}

function findVertex(geo: Geo | null, nodeId: number): number[] | undefined {
  if (!geo) return undefined;
  for (const f of geo.ways.features) {
    const i = f.properties.nodes.indexOf(nodeId);
    if (i >= 0) return f.geometry.coordinates[i];
  }
  return undefined;
}

export default memo(MapView);
