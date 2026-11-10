import { createMutationQueue } from './utils/mutationQueue';
import EditorMenus from './components/EditorMenus';
import { PanelToggle } from './components/PanelClose';
import SelectionEditor, { type Segment } from './components/SelectionEditor';
import { toggleSelection } from './utils/selection';
import type { FeatureCollection } from 'geojson';
import type { PickedPoint, RouteQuery } from './utils/networkAnalysis';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { USER_NAME, api, downloadFile, type OpResponse } from './api';
import MapView, { type DrawGroup, type MapHandle, type RouteOverlay } from './components/MapView';
import { routeColour } from './components/Transit';
import Inspector from './components/Inspector';
import { BottomPanel, LayersPanel, TOOLS, TOOL_HINTS, ToolRail, type BottomTab } from './components/Panels';
import { ChangesPanel, ExportCheck, LoadPanel, ReportModal } from './components/Analysis';
import { AnalyticsPanel, BasemapControls, TasksModal, type BaseMap, type ImageOverlay } from './components/Extras';
import StartScreen from './components/StartScreen';
import { FixReview, type FixReviewState } from './components/FixReview';
import { OrganizerReport, SumoReport } from './components/CheckResults';
import { FixAllDialog } from './components/FixAll';
import { MenuBar, Splitter, useLayout } from './components/WindowLayout';
import type {
  ChangesResult, Comparison, DocState, Fix, Geo, GeoDiff, Issue, LayerVisibility, LoadState, NodeProps, Op, OrganizerResult, Peer,
  ReportData, RouteDetail, Selection, SumoResult, Tool, ValidationResult, WayProps,
} from './types';
import { applyPatch } from './utils/geoPatch';
import { sharedHighwayNodes } from './utils/joins';
import { saveName, savedName, useCollab, type CollabMessage } from './utils/collab';
import { isDraftPointKeyClaimed, isFullscreenToggle, shouldDeleteSelection } from './utils/keys';
import {
  DEFAULT_LAYERS, allIssueFiltersOn, allLayersOn, allOff, allOn, applyLayerChange, defaultIssueFilters, issueVisible,
  type IssueFilterKey,
} from './utils/layers';
import { TEMPLATES, templateOps, type RoadTemplate } from './utils/osm';
import { APP_VERSION, isFrontendOutdated } from './utils/version';

const VERSION_CHECK_MS = 60_000;
const STORAGE_KEY = 'uds-editor-session';
const NO_ISSUES: Issue[] = [];

export default function App() {
  const [doc, setDoc] = useState<DocState | null>(null);
  const [analysisOverlay, setAnalysisOverlay] = useState<FeatureCollection>({type:'FeatureCollection',features:[]});
  const [analysisPick,setAnalysisPick] = useState(false);
  const [analysisPicked,setAnalysisPicked] = useState<PickedPoint|null>(null);
  const [multi, setMulti] = useState<NonNullable<Selection>[]>([]);
  const [segment, setSegment] = useState<Segment | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [tool, setTool] = useState<Tool>('select');
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [layers, setLayers] = useState(DEFAULT_LAYERS);
  const [issueFilters, setIssueFilters] = useState(defaultIssueFilters);
  const [baseOpacity, setBaseOpacity] = useState(50);
  const [bottomTab, setBottomTab] = useState<BottomTab>('issues');
  const [load, setLoad] = useState<LoadState | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [loadColor, setLoadColor] = useState(false);
  const [changesData, setChangesData] = useState<ChangesResult | null>(null);
  const [changesOverlay, setChangesOverlay] = useState(true);
  const [exportCheck, setExportCheck] = useState(false);
  const [report, setReport] = useState<ReportData | null | 'error' | undefined>(undefined);
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cursor coordinates are written straight into the status bar. Keeping them in
  // React state re-rendered the whole editor on every mouse move.
  const cursorEl = useRef<HTMLSpanElement>(null);
  const onCursor = useCallback((ll: [number, number] | null, zoom: number) => {
    sendRef.current({ cursor: ll });
    if (cursorEl.current) {
      cursorEl.current.textContent = (ll ? `${ll[1].toLocaleString('ru-RU', { minimumFractionDigits: 5, maximumFractionDigits: 5 })}° с. ш. · ${ll[0].toLocaleString('ru-RU', { minimumFractionDigits: 5, maximumFractionDigits: 5 })}° в. д.   ` : '') + `масштаб ${zoom.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}`;
    }
  }, []);
  const [mapDiff, setMapDiff] = useState<GeoDiff | null>(null);
  // houses above lawns by default; the layers panel reorders the groups
  const [drawOrder, setDrawOrder] = useState<DrawGroup[]>(['zones', 'buildings', 'paths', 'roads', 'rail']);
  // two ways of working: editing the graph, or analysing flows and scenarios
  const [mode, setMode] = useState<'edit' | 'analytics'>('edit');
  const [base, setBase] = useState<BaseMap>('osm');
  const [overlay, setOverlay] = useState<ImageOverlay | null>(null);
  const [threeD, setThreeD] = useState(false);
  const [highlight, setHighlight] = useState<{ kind: string; id: number }[]>([]);
  // a suggested fix under review: drawn on the map, applied only on the user's click
  const mapHighlight = useMemo(() => [...highlight,...multi], [highlight,multi]);
  const [fixReview, setFixReview] = useState<FixReviewState | null>(null);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [fixAllOpen, setFixAllOpen] = useState(false);
  const [followId, setFollowId] = useState<string | null>(null);
  const followRef = useRef<string | null>(null);
  const modeRef = useRef<'edit' | 'analytics'>('edit');
  modeRef.current = mode;
  followRef.current = followId;
  const highlightTimer = useRef<number>(0);
  const flash = useCallback((refs: { kind: string; id: number }[], ms = 4000) => {
    setHighlight(refs);
    window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlight([]), ms);
  }, []);
  const [openRoute, setOpenRoute] = useState<RouteDetail | null>(null);
  const runOpsRef = useRef<((ops: Op | Op[], opts?: { label?: string }) => Promise<boolean>) | null>(null);
  const routeOverlay = useMemo<RouteOverlay | null>(() => (openRoute
    ? { colour: routeColour(openRoute), lines: openRoute.lines, stops: openRoute.stop_list }
    : null), [openRoute]);
  // ---- collaboration
  const [myName, setMyName] = useState(savedName);
  const [peerList, setPeerList] = useState<Peer[]>([]);
  const [nameEdit, setNameEdit] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const peersRef = useRef(new Map<string, Peer>());
  const sendRef = useRef<(p: Record<string, unknown>) => void>(() => undefined);
  const revRef = useRef(0);
  const pendingRef = useRef(new Map<number, OpResponse>());
  const resyncTimer = useRef<number>(0);
  // latest map data, updated synchronously so back-to-back patches never merge onto a stale base
  const geoRef = useRef<Geo | null>(null);
  const [team, setTeam] = useState('team1');
  const [sumo, setSumo] = useState<SumoResult | 'running' | null>(null);
  const [organizer, setOrganizer] = useState<OrganizerResult | 'running' | null>(null);
  const [booting, setBooting] = useState(true);
  // panels shown/hidden from the «Окно» menu and their dragged sizes
  const win = useLayout();
  const [stale, setStale] = useState(false);
  const mapRef = useRef<MapHandle>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      if (document.visibilityState === 'hidden') return;
      api.health()
        .then((health) => {
          if (!cancelled) setStale(isFrontendOutdated(APP_VERSION, health.version));
        })
        .catch(() => undefined);
    };
    check();
    const timer = window.setInterval(check, VERSION_CHECK_MS);
    const onFocus = () => check();
    const onVis = () => { if (document.visibilityState === 'visible') check(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVis);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, []);

  // ---------------------------------------------------------- indexes
  const wayIndex = useMemo(() => {
    const m = new Map<number, WayProps>();
    doc?.geo.ways.features.forEach((f) => m.set(f.properties.id, f.properties));
    doc?.geo.areas.features.forEach((f) => m.set(f.properties.id, f.properties));
    return m;
  }, [doc]);
  const wayById = useCallback((id: number) => wayIndex.get(id), [wayIndex]);

  const selectedNode = useMemo((): (NodeProps & { lon: number; lat: number }) | undefined => {
    if (!doc || selection?.kind !== 'node') return undefined;
    const f = doc.geo.nodes.features.find((n) => n.properties.id === selection.id);
    if (f) return { ...f.properties, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] };
    for (const w of doc.geo.ways.features) {
      const i = w.properties.nodes.indexOf(selection.id);
      if (i >= 0) {
        const [lon, lat] = w.geometry.coordinates[i];
        return { id: selection.id, kind: 'node', junction: false, category: 'junction', tags: {}, lon, lat };
      }
    }
    return undefined;
  }, [doc, selection]);
  const selectedWay = selection?.kind === 'way' ? wayIndex.get(selection.id) : undefined;
  const sharedNodes = useMemo(() => sharedHighwayNodes(doc?.geo ?? null), [doc]);
  const canSeparate = selection?.kind === 'node' && sharedNodes.has(selection.id);
  const sharedJunctions = useMemo(() => {
    if (!selectedWay || (selectedWay.layer !== 'road' && selectedWay.layer !== 'path')) return 0;
    return new Set(selectedWay.nodes.filter((id) => sharedNodes.has(id))).size;
  }, [selectedWay, sharedNodes]);
  const selectedGeometry = useMemo(() => {
    if (!doc || selection?.kind !== 'way') return undefined;
    const line = doc.geo.ways.features.find((f) => f.properties.id === selection.id);
    if (line) return line.geometry.coordinates;
    return doc.geo.areas.features.find((f) => f.properties.id === selection.id)?.geometry.coordinates[0];
  }, [doc, selection]);
  const nodeOnRoad = useMemo(() => {
    if (!doc || selection?.kind !== 'node') return true;
    const tagged = selectedNode?.tags.highway === 'traffic_signals' || selectedNode?.tags.highway === 'crossing';
    if (!tagged) return true;  // only signals and crossings need to sit on the carriageway
    return doc.geo.ways.features.some((f) => f.properties.layer === 'road' && f.properties.nodes.includes(selection.id));
  }, [doc, selection, selectedNode]);
  const loadRow = useMemo(
    () => (selection?.kind === 'way' ? load?.rows.find((r) => r.id === selection.id) : undefined),
    [load, selection],
  );
  const loadVc = useMemo(
    () => (loadColor && load ? new Map(load.rows.map((r) => [r.id, r.vc])) : null),
    [loadColor, load],
  );

  // --------------------------------------------------------- validation
  const validateTimer = useRef<number>();
  const scheduleValidate = useCallback((docId: string) => {
    window.clearTimeout(validateTimer.current);
    validateTimer.current = window.setTimeout(async () => {
      setValidating(true);
      try {
        setValidation(await api.validate(docId));
      } catch (e) {
        setError(String((e as Error).message));
      } finally {
        setValidating(false);
      }
    }, 250);
  }, []);

  const loadDoc = useCallback((d: DocState) => {
    geoRef.current = d.geo;
    revRef.current = d.rev ?? 0;
    pendingRef.current.clear();
    try {
      window.history.replaceState(null, '', `${location.pathname}?doc=${encodeURIComponent(d.doc_id)}`);
    } catch {
      /* sandboxed iframe */
    }
    setMapDiff(null);
    setDoc(d);
    setSelection(null);
    setValidation(null);
    scheduleValidate(d.doc_id);
  }, [scheduleValidate]);

  useEffect(() => {
    // a shared link (?doc=...) wins over the last document of this browser
    const shared = new URLSearchParams(location.search).get('doc');
    const raw = localStorage.getItem(STORAGE_KEY);
    let saved: { doc_id?: string; team?: string } = {};
    try { saved = raw ? JSON.parse(raw) : {}; } catch { saved = {}; }
    if (saved.team) setTeam(saved.team);
    const target = shared || saved.doc_id;
    if (!target) { setBooting(false); return; }
    api.restore(target)
      .then((d) => { loadDoc(d); setError(null); })
      .catch((e: Error) => {
        const msg = e?.message ?? '';
        if (msg.startsWith('404') || msg.includes('не найден')) localStorage.removeItem(STORAGE_KEY);
        else setError(msg);
      })
      .finally(() => setBooting(false));
  }, [loadDoc]);

  useEffect(() => {
    if (!doc) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ doc_id: doc.doc_id, team, filename: doc.filename }));
  }, [doc, team]);

  const docId = doc?.doc_id;
  const analysisTimer = useRef<number>();
  const wantChanges = bottomTab === 'changes';
  useEffect(() => {
    if (!doc) return;
    const id = doc.doc_id;
    window.clearTimeout(analysisTimer.current);
    analysisTimer.current = window.setTimeout(() => {
      api.load(id).then(setLoad).catch(() => undefined);
      if (wantChanges) api.changes(id).then(setChangesData).catch(() => undefined);
    }, 300);
  }, [doc, wantChanges]);

  const loadMutations=useRef(createMutationQueue<LoadState>());
  const loadRequest=useRef(0);
  const loadAction = useCallback(async (request:()=>Promise<LoadState>) => {
    const id=docIdRef.current, sequence=++loadRequest.current;
    try {
      const state=await loadMutations.current.run(request);
      if(docIdRef.current===id && sequence===loadRequest.current)setLoad(state);
    } catch(e) {
      if(docIdRef.current!==id)return;
      setError((e as Error).message);
      // Restore authoritative state when an optimistic edit is rejected.
      if(id && sequence===loadRequest.current) {
        try {const state=await api.load(id);if(docIdRef.current===id && sequence===loadRequest.current)setLoad(state);}catch{/* Keep the original error visible. */}
      }
    }
  }, []);
  const onClosure = useCallback(async (wayId: number, closed: boolean) => {
    const id = docIdRef.current;
    if (!id) return;
    try {
      const state = await loadMutations.current.run(()=>api.setClosure(id, wayId, closed));
      if(docIdRef.current!==id)return;
      setLoad(state);
      const c = state.closures?.find((x) => x.id === wayId);
      setNotice(!closed ? 'Перекрытие снято'
        : c?.found ? `Дорога перекрыта: ${c.moved ?? 0} авт./ч ушли на объезд по ${c.ways.length} рёбрам`
          + (c.partial ? ' (в обратную сторону объезда нет)' : '')
        : 'Дорога перекрыта, но объезда в пределах карты нет — поток теряется');
      if (c?.found) flash(c.ways.map((w) => ({ kind: 'way', id: w })), 8000);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [flash]);
  const objectReport = useCallback(async (kind: string, eid: number, fmt: 'xlsx' | 'pdf') => {
    const id = docIdRef.current;
    if (!id) return;
    try {
      await downloadFile(api.objectReportUrl(id, kind, eid, fmt), `object_${kind}_${eid}.${fmt}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  // apply a building template to every building of the same type that has no storeys yet
  const autofillBuildings = useCallback((buildingType: string, tags: Record<string, string>) => {
    const geo = geoRef.current;
    if (!geo) return;
    const targets = geo.areas.features.filter((f) => f.properties.layer === 'building'
      && (f.properties.tags.building ?? 'yes') === buildingType && !f.properties.tags['building:levels']);
    if (!targets.length) { setNotice('Нет зданий этого типа без этажности'); return; }
    runOpsRef.current?.(targets.map((f) => ({ op: 'set_tags', args: { kind: 'way', id: f.properties.id, tags } })),
      { label: `Шаблон здания для ${targets.length} зданий` });
  }, []);
  const refreshLoad = useCallback(() => {
    const id = docIdRef.current;
    if (id) api.load(id).then(setLoad).catch(() => undefined);
  }, []);
  const loadTimer=useRef<ReturnType<typeof setTimeout>>();
  useEffect(()=>()=>clearTimeout(loadTimer.current),[docId]);
  const onMultiplier = useCallback((m: number) => { clearTimeout(loadTimer.current);loadTimer.current=setTimeout(()=>{if(docId)loadAction(()=>api.setLoad(docId,{multiplier:m}));},150); }, [docId, loadAction]);
  const onSnapshot = useCallback((name: string) => { if (docId) loadAction(()=>api.addSnapshot(docId, name)); }, [docId, loadAction]);
  const onDeleteSnapshot = useCallback((sid: string) => { if (docId) loadAction(()=>api.deleteSnapshot(docId, sid)); }, [docId, loadAction]);
  const onResetLoad = useCallback(() => {
    if (docId) loadAction(()=>api.setLoad(docId, { reset_all: true }));
    setComparison(null);
  }, [docId, loadAction]);
  const volumeTimers=useRef(new Map<number,ReturnType<typeof setTimeout>>());
  useEffect(()=>()=>{for(const timer of volumeTimers.current.values())clearTimeout(timer);volumeTimers.current.clear();},[docId]);
  const onSetLoad = useCallback((wayId: number, volume: number | null) => {
    if(!docId)return;
    if(volume!==null)setLoad(prev=>{
      if(!prev)return prev;
      const cfg=prev.settings??{};
      const demand=({morning:1,day:.65,evening:1.1,night:.15} as Record<string,number>)[cfg.time_of_day??'morning'];
      const pcu=1+(cfg.truck_share??0)+1.5*(cfg.bus_share??0);
      return {...prev,rows:prev.rows.map(row=>{
        if(row.id!==wayId || row.closed)return row;
        const effective=Math.round(volume*prev.multiplier+(row.transit_pcu??0))*demand*pcu;
        const vc=row.capacity?effective/row.capacity:0;
        const los = vc<.2?'A':vc<.45?'B':vc<.7?'C':vc<.9?'D':vc<=1?'E':'F';
        return {...row,volume:effective,base_volume:volume,custom:true,vc,los,t_s:row.t0_s*(1+.15*vc**4)};
      })};
    });
    clearTimeout(volumeTimers.current.get(wayId));
    volumeTimers.current.set(wayId,setTimeout(()=>{volumeTimers.current.delete(wayId);loadAction(()=>api.setLoad(docId,{way_id:wayId,volume}));},150));
  }, [docId, loadAction]);
  const onCompare = useCallback(async (a: string, b: string) => {
    if (!docId) return;
    try {
      setComparison(await api.compare(docId, a, b));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [docId]);
  const showElement = useCallback((kind: 'way' | 'node', id: number) => {
    const geo = geoRef.current;
    setSelection({ kind, id });
    if (!geo) return;
    if (kind === 'way') {
      const f = geo.ways.features.find((w) => w.properties.id === id) ?? geo.areas.features.find((w) => w.properties.id === id);
      const coords = f ? ('nodes' in f.properties && f.geometry.type === 'Polygon' ? f.geometry.coordinates[0] : f.geometry.coordinates) : null;
      const mid = coords ? (coords as number[][])[Math.floor((coords as number[][]).length / 2)] : null;
      if (mid) mapRef.current?.flyTo(mid[0], mid[1], 18);
    } else {
      const f = geo.nodes.features.find((n) => n.properties.id === id);
      if (f) mapRef.current?.flyTo(f.geometry.coordinates[0], f.geometry.coordinates[1], 19);
    }
  }, []);
  const onShowRoad = useCallback((id: number) => showElement('way', id), [showElement]);

  const openReport = async () => {
    if (!doc) return;
    setReport(null);
    try {
      setReport(await api.report(doc.doc_id));
    } catch {
      setReport('error');
    }
  };

  const applyState = useCallback((res: OpResponse) => {
    const { patch, geo: fullGeo, ...state } = res;
    let geo = fullGeo ?? geoRef.current;
    let diff: GeoDiff | null = null;
    if (!fullGeo && patch && geo) {
      diff = applyPatch(geo, patch);
      geo = diff.target;
    }
    if (!geo) return;
    geoRef.current = geo;
    setMapDiff(diff);
    setDoc({ ...state, geo } as DocState);
    scheduleValidate(res.doc_id);
  }, [scheduleValidate]);

  // Own responses and broadcast patches carry the server revision. Apply them strictly
  // in order; if one goes missing (socket hiccup), reload the full document.
  const resync = useCallback(async () => {
    const id = geoRef.current ? docIdRef.current : undefined;
    if (!id) return;
    try {
      const d = await api.restore(id);
      geoRef.current = d.geo;
      revRef.current = d.rev ?? 0;
      pendingRef.current.clear();
      setMapDiff(null);
      setDoc(d);
      scheduleValidate(d.doc_id);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [scheduleValidate]);
  const ingest = useCallback((res: OpResponse) => {
    if (res.geo) {
      revRef.current = res.rev ?? revRef.current;
      pendingRef.current.clear();
      applyState(res);
      return;
    }
    if (!res.patch) {
      applyState(res);  // nothing changed on the map (e.g. nothing to undo)
      return;
    }
    if (res.rev <= revRef.current) return;  // already applied
    const pending = pendingRef.current;
    pending.set(res.rev, res);
    while (pending.has(revRef.current + 1)) {
      const next = pending.get(revRef.current + 1)!;
      pending.delete(revRef.current + 1);
      revRef.current += 1;
      applyState(next);
    }
    window.clearTimeout(resyncTimer.current);
    if (pending.size) resyncTimer.current = window.setTimeout(resync, 1500);
  }, [applyState, resync]);

  const publishPeers = useCallback((membership: boolean) => {
    const list = [...peersRef.current.values()];
    mapRef.current?.updatePeers(list);
    if (membership) setPeerList(list);
  }, []);
  const onCollab = useCallback((m: CollabMessage) => {
    const peers = peersRef.current;
    if (m.type === 'hello') {
      peers.clear();
      m.peers.forEach((p) => peers.set(p.id, p));
      publishPeers(true);
      if (m.rev !== revRef.current) resync();
    } else if (m.type === 'join' || m.type === 'presence') {
      const before = peers.get(m.peer.id);
      peers.set(m.peer.id, m.peer);
      const view = (m.peer as { view?: { center: [number, number]; zoom: number } }).view;
      if (followRef.current === m.peer.id && view) mapRef.current?.jumpTo(view.center, view.zoom);
      publishPeers(!before || before.name !== m.peer.name || before.selection?.id !== m.peer.selection?.id);
    } else if (m.type === 'leave') {
      peers.delete(m.id);
      if (followRef.current === m.id) setFollowId(null);
      publishPeers(true);
    } else if (m.type === 'patch') {
      ingest(m as unknown as OpResponse);
      // say who changed what, and flash it on the map
      const last = (m as unknown as OpResponse).history?.details?.[0];
      if (last) {
        setNotice(`${last.author ?? 'Участник'}: ${last.label}${last.refs[0]?.name ? ` — ${last.refs[0].name}` : ''}`);
        flash(last.refs);
      }
    } else if (m.type === 'load') {
      const id = docIdRef.current;
      if (id) api.load(id).then(setLoad).catch(() => undefined);
    }
  }, [ingest, publishPeers, resync, flash]);
  const docIdRef = useRef<string | undefined>(undefined);
  docIdRef.current = doc?.doc_id;
  const collab = useCollab(doc?.doc_id, myName, onCollab);
  sendRef.current = collab.send;
  USER_NAME.current = myName;
  const onView = useCallback((center: [number, number], zoom: number) => { sendRef.current({ view: { center, zoom } }); }, []);
  const onOverlayFile = useCallback((file: File) => {
    // start from the visible part of the map; the corners are then dragged into place
    const b = mapRef.current?.getBounds?.();
    const url = URL.createObjectURL(file);
    const [w, s0, e, n] = b ?? [104.26, 52.25, 104.28, 52.27];
    const dx = (e - w) * 0.15;
    const dy = (n - s0) * 0.15;
    setOverlay({ url, name: file.name, opacity: 0.6,
      corners: [[w + dx, n - dy], [e - dx, n - dy], [e - dx, s0 + dy], [w + dx, s0 + dy]] });
  }, []);
  const onShowRefs = useCallback((refs: { kind: string; id: number }[]) => {
    flash(refs, 6000);
    const first = refs.find((r) => r.kind === 'way' || r.kind === 'node');
    if (first) showElement(first.kind as 'way' | 'node', first.id);
  }, [flash, showElement]);
  const switchMode = (m: 'edit' | 'analytics') => {
    setMode(m);
    if (m === 'analytics') {
      setTool('select');
      setBottomTab('load');
      setLoadColor(true);
      // load colouring is the default view of analytics: make sure the roads are visible
      setLayers((prev) => (prev.roads ? prev : { ...prev, roads: true }));
    } else {
      setLoadColor(false);
    }
  };
  useEffect(() => { collab.send({ selection }); }, [collab, selection]);
  useEffect(() => { collab.send({ tool }); }, [collab, tool]);
  useEffect(() => { if (!doc) { peersRef.current.clear(); setPeerList([]); } }, [doc]);

  const shareLink = async () => {
    if (!doc) return;
    const link = `${location.origin}${location.pathname}?doc=${encodeURIComponent(doc.doc_id)}`;
    try {
      await navigator.clipboard.writeText(link);
      setNotice('Ссылка скопирована — отправьте её коллеге');
    } catch {
      setNotice(link);
    }
  };
  const commitName = () => {
    const n = (nameEdit ?? '').trim().slice(0, 32);
    if (n) {
      setMyName(n);
      saveName(n);
      collab.send({ name: n });
    }
    setNameEdit(null);
  };

  // keep selection valid after the document changes
  useEffect(() => {
    if (!doc || !selection) return;
    if (selection.kind === 'way' && !wayIndex.has(selection.id)) setSelection(null);
    if (selection.kind === 'node' && !selectedNode) setSelection(null);
  }, [doc, selection, wayIndex, selectedNode]);

  // -------------------------------------------------------------- ops
  const runOps = useCallback(async (ops: Op | Op[], opts?: { select?: 'result' | 'keep'; label?: string; reason?: string }): Promise<boolean> => {
    if (!docId) return false;
    if (modeRef.current === 'analytics') {
      setNotice('Режим аналитики: граф не редактируется. Переключитесь на «Редактирование».');
      return false;
    }
    setError(null);
    try {
      let res: OpResponse;
      const list = Array.isArray(ops) ? ops : [ops];
      if (list.length === 1) res = await api.op(docId, list[0], opts?.label, opts?.reason);
      else res = await api.batch(docId, list, opts?.label, opts?.reason);
      ingest(res);
      const warnings = [
        ...(res.result?.warnings ?? []),
        ...(res.results ?? []).flatMap((r) => r.warnings ?? []),
      ];
      if (warnings.length) setHint(warnings.join(' · '));
      if (opts?.select === 'result') {
        const id = res.result?.id ?? res.results?.[res.results.length - 1]?.id;
        const op = list[list.length - 1].op;
        if (id !== undefined) setSelection({ kind: op === 'add_way' || op === 'edit_segment' ? 'way' : 'node', id });
        else if (list[0].op === 'set_tags' && list[0].args.kind === 'node') setSelection({ kind: 'node', id: Number(list[0].args.id) });
      }
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }, [docId, ingest]);

  runOpsRef.current = runOps;

  // stable callbacks so memoized panels and the map skip unrelated re-renders
  const onMapOp = useCallback((op: Op | Op[], o?: { select?: 'result' | 'keep'; label?: string }) =>
    runOps(op, { select: o?.select, label: o?.label }), [runOps]);
  const onFix = useCallback((i: Issue, fix: Fix, probe?: RouteQuery) => {
    if (!docId) return;
    setMode('edit');modeRef.current='edit';setAnalysisPick(false);
    setFixReview({ issue: i, fix, preview: null, error: null });
    api.fixPreview(docId, { op: fix.op, args: fix.args, probe })
      .then((preview) => {
        // frame what is added or removed (a long road that is only split would zoom too far out),
        // with at least ~80 m around a tiny change
        const core = preview.features.filter((f) => f.properties.status !== 'modified');
        const pts = (core.length ? core : preview.features).flatMap((f) => (f.geometry.type === 'Point'
          ? [f.geometry.coordinates] : f.geometry.coordinates));
        if (pts.length) {
          const lons = pts.map((p) => p[0]); const lats = pts.map((p) => p[1]);
          const pad = 0.0008;
          mapRef.current?.fitBounds([Math.min(...lats) - pad, Math.min(...lons) - pad * 1.6,
            Math.max(...lats) + pad, Math.max(...lons) + pad * 1.6]);
        } else if (i.location) mapRef.current?.flyTo(i.location[0], i.location[1], 18);
        setFixReview((r) => (r && r.fix === fix ? { ...r, preview } : r));
      })
      .catch((e: Error) => {
        if (i.location) mapRef.current?.flyTo(i.location[0], i.location[1], 18);
        setFixReview((r) => (r && r.fix === fix ? { ...r, error: e.message } : r));
      });
  }, [docId]);
  const applyReviewedFix = useCallback(() => {
    const r = fixReview;
    if (!r) return;
    setFixReview(null);
    setMode('edit');
    runOps({ op: r.fix.op, args: r.fix.args }, {
      label: r.fix.label + ': ' + r.issue.title,
      reason: r.issue.description,
      select:r.fix.op==='edit_segment'?'result':'keep',
    }).then(ok=>{if(ok && r.fix.op==='edit_segment'){setSegment(null);setMulti([]);}});
  }, [fixReview, runOps]);
  const cancelFixReview = useCallback(() => setFixReview(null), []);
  // a review belongs to the document it was computed on
  useEffect(() => { setFixReview(null); }, [docId]);
  const onInspectorOp = useCallback((op: Op | Op[], label?: string) => { runOps(op, { label }); }, [runOps]);
  const onLayers = useCallback((next: LayerVisibility) => {
    const applied = applyLayerChange(layers, issueFilters, next);
    setLayers(applied.layers);
    setIssueFilters(applied.filters);
  }, [layers, issueFilters]);
  // «Выбрать всё» toggles: a second press, with everything already on, clears every checkbox
  const onSelectAllLayers = useCallback(() => {
    if (allOn(layers)) {
      setLayers(allOff(layers));
      setIssueFilters(allOff(issueFilters));
    } else onLayers(allLayersOn());
  }, [onLayers, layers, issueFilters]);
  const onIssueFilter = useCallback((key: IssueFilterKey) => {
    setIssueFilters((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);
  const onSelectAllIssues = useCallback(() => {
    if (allOn(issueFilters)) {
      setIssueFilters(allOff(issueFilters));
      setLayers((prev) => ({ ...prev, issues: false }));
      return;
    }
    setIssueFilters(allIssueFiltersOn());
    setLayers((prev) => ({ ...prev, issues: true }));
  }, [issueFilters]);
  const mapIssues = useMemo(() => {
    if (!layers.issues || !validation) return NO_ISSUES;
    const shown = validation.issues.filter((issue) => issueVisible(issueFilters, issue.category));
    return shown.length ? shown : NO_ISSUES;
  }, [layers.issues, validation, issueFilters]);

  const undo = useCallback(async () => {
    if (doc?.history.can_undo) ingest(await api.undo(doc.doc_id));
  }, [doc, ingest]);
  const redo = useCallback(async () => {
    if (doc?.history.can_redo) ingest(await api.redo(doc.doc_id));
  }, [doc, ingest]);

  const applyTemplate = useCallback((t: RoadTemplate) => {
    if (selection?.kind === 'way') runOps(templateOps(selection.id, t), { label: `Шаблон «${t.name}»` });
  }, [selection, runOps]);

  const showIssue = useCallback((i: Issue) => {
    if (i.location) mapRef.current?.flyTo(i.location[0], i.location[1], 19);
    const ref = i.refs.find((r) => r.kind === 'way' || r.kind === 'node');
    if (ref && ref.kind !== 'relation') setSelection({ kind: ref.kind, id: ref.id });
  }, []);

  // ---------------------------------------------------------- hotkeys
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      const k = e.key.toLowerCase();
      // Esc and Ctrl+Z work the same way: first they cancel what is still in progress
      // (a road being drawn, an open dialog, a fix under review, vertex or merge picking),
      // and when nothing is pending they undo the last edit from the history.
      const cancelUnsaved = (): boolean => {
        if ((mapRef.current?.draftPointCount() ?? 0) > 0) { mapRef.current?.cancelDraft(); return true; }
        if (organizer && organizer !== 'running') { setOrganizer(null); return true; }
        if (sumo && sumo !== 'running') { setSumo(null); return true; }
        if (exportCheck) { setExportCheck(false); return true; }
        if (report !== undefined) { setReport(undefined); return true; }
        if (tasksOpen) { setTasksOpen(false); return true; }
        if (fixAllOpen) { setFixAllOpen(false); return true; }
        if (fixReview) { setFixReview(null); return true; }
        return mapRef.current?.cancelPending() ?? false;
      };
      const undoWithNotice = () => {
        if (!doc?.history.can_undo) return;
        undo();
        if (doc.history.undo_label) setNotice(`Отменено: ${doc.history.undo_label}`);
      };
      if ((e.ctrlKey || e.metaKey) && (k === 'z' || k === 'я')) {
        e.preventDefault();
        if (e.shiftKey) redo();
        else if (!cancelUnsaved()) undoWithNotice();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (k === 'y' || k === 'н')) { e.preventDefault(); redo(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (isFullscreenToggle(e)) { e.preventDefault(); mapRef.current?.toggleFullscreen(); return; }
      if (e.key === 'Escape') {
        e.preventDefault();
        if (cancelUnsaved()) return;
        if (selection || multi.length || segment || analysisPick) { setSelection(null);setMulti([]);setSegment(null);setAnalysisPick(false);return; }
        if (tool !== 'select') { setTool('select'); return; }
        undoWithNotice();
        return;
      }
      // Delete/Backspace remove the selection, unless a way draft owns the key
      // (then it only drops the latest draft point). That listener is capture
      // on this same window, and stopPropagation() does not suppress another
      // capture listener, so this handler stays on the bubble path and also
      // refuses the delete when the draft still owns the key.
      if (selection && shouldDeleteSelection(
        e, tool, mapRef.current?.draftPointCount() ?? 0, isDraftPointKeyClaimed(),
      )) {
        runOps({ op: 'delete', args: { kind: selection.kind, id: selection.id } });
        return;
      }
      if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') { mapRef.current?.zoomBy(1); return; }
      if (e.key === '-' || e.key === '_' || e.code === 'NumpadSubtract') { mapRef.current?.zoomBy(-1); return; }
      if (e.key === '0') { mapRef.current?.fitData(); return; }
      const byCode = TOOLS.find((x) => e.code === `Key${x.key}`);
      if (byCode) { setTool(byCode.id); return; }
      const tpl = TEMPLATES.find((x) => x.key === e.key);
      if (tpl) applyTemplate(tpl);
    };
    window.addEventListener('keydown', onKey, false);
    return () => window.removeEventListener('keydown', onKey, false);
  }, [tool, selection, undo, redo, runOps, applyTemplate, fixReview, organizer, sumo, exportCheck, report, tasksOpen, fixAllOpen, doc, multi, segment, analysisPick]);

  useEffect(() => { setHint(null); }, [tool]);

  // ------------------------------------------------------------ export
  const exportOsm = async () => {
    if (!doc) return;
    const name = `${team || 'team'}.osm`;
    try {
      const size = await downloadFile(api.exportUrl(doc.doc_id, team || 'team'), name, true);
      setNotice(`Сохранён ${name} — ${(size / 1024 / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} МБ`);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const downloadReport = useCallback(async (kind: 'xlsx' | 'docx' | 'pdf') => {
    const id = docIdRef.current;
    if (!id) return;
    try {
      await downloadFile(api.reportUrl(id, kind), `report.${kind}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  const downloadEditLog = useCallback(async () => {
    const id = docIdRef.current;
    if (!id) return;
    try {
      await downloadFile(api.editLogUrl(id), 'fix-log.md');
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const checkSumo = async () => {
    if (!doc) return;
    setSumo('running');
    try {
      setSumo(await api.sumo(doc.doc_id));
    } catch (e) {
      setSumo(null);
      setError((e as Error).message);
    }
  };

  const uploadOrganizer = async () => {
    if (!doc) return;
    setOrganizer('running');
    setError(null);
    try {
      setOrganizer(await api.organizer(doc.doc_id));
    } catch (e) {
      // the server already explains the rejection in Russian; show it in the dialog, not a toast
      setOrganizer({ ok: false, status: 0, filename: '', message: (e as Error).message });
    }
  };

  const closeDocument = () => {
    const closingId = doc?.doc_id;
    localStorage.removeItem(STORAGE_KEY);
    // others may still be editing this document: then leave it, do not delete it
    if (closingId && peerList.length === 0) api.discardDraft(closingId).catch(() => undefined);
    try {
      window.history.replaceState(null, '', location.pathname);
    } catch {
      /* sandboxed iframe */
    }
    geoRef.current = null;
    setMapDiff(null);
    setDoc(null);
    setValidation(null);
    setSelection(null);
    setLoad(null);
    setComparison(null);
    setChangesData(null);
    setOpenRoute(null);
  };

  const versionNotice = stale ? <VersionNotice /> : null;
  if (booting) {
    return (
      <>
        {versionNotice}
        <div className="start"><p className="muted">Восстановление правок…</p></div>
      </>
    );
  }
  if (!doc) {
    return (
      <>
        {versionNotice}
        <StartScreen onLoaded={loadDoc} />
      </>
    );
  }

  const counts = validation?.summary.counts;
  const changes = Object.values(doc.diff).reduce((s, d) => s + d.added + d.modified + d.deleted, 0);

  return (
    <div className="app" style={win.style}>
      {versionNotice}
      <PanelToggle.Provider value={win.toggle}>
      <MenuBar extra={<EditorMenus onReport={openReport} onDownload={(kind) => { downloadFile(api.reportUrl(doc.doc_id, kind), `report.${kind}`).catch((e:Error) => setError(e.message)); }} />} layout={win.layout} onToggle={win.toggle} onReset={win.reset} />
      <header className="top">
        <div className="brand">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#3B82F6" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="5" cy="6" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M7 6h10M6 8l5 8M18 8l-5 8" />
          </svg>
          <b>Редактор УДС</b>
        </div>
        <div className="file-chip mono">
          {doc.filename}
          {changes > 0 && <><span className="dot" aria-hidden="true" /><span className="muted">изменено объектов: {changes}</span></>}
        </div>
        <button className="btn ghost" onClick={closeDocument}
          title={peerList.length ? 'Выйти из документа (он останется у коллег)' : 'Закрыть документ и удалить черновик'}>
          Открыть другой
        </button>
        <div className="row gap2">
          <button className="icon-btn" aria-label="Отменить" title={`Отменить${doc.history.undo_label ? ': ' + doc.history.undo_label : ''} · Ctrl+Z`}
            disabled={!doc.history.can_undo} onClick={undo}>↶</button>
          <button className="icon-btn" aria-label="Повторить" title={`Повторить${doc.history.redo_label ? ': ' + doc.history.redo_label : ''} · Ctrl+Shift+Z`}
            disabled={!doc.history.can_redo} onClick={redo}>↷</button>
        </div>
        <div className="seg mode-seg" role="group" aria-label="Режим работы">
          <button aria-pressed={mode === 'edit'} onClick={() => switchMode('edit')}>Редактирование</button>
          <button aria-pressed={mode === 'analytics'} onClick={() => switchMode('analytics')}>Аналитика</button>
        </div>
        <button className="btn" aria-pressed={threeD} onClick={() => setThreeD(!threeD)} title="Объёмный вид зданий">3D</button>
        <span className="grow" />
        <div className="peers" aria-label={`В документе: ${[myName, ...peerList.map((p) => p.name)].join(', ')}`}>
          <button className="peer-avatar me" style={{ background: '#334155' }} title={`${myName} (вы) — изменить имя`}
            onClick={() => setNameEdit(myName)}>{initials(myName)}</button>
          {peerList.map((p) => (
            <button key={p.id} className={`peer-avatar ${followId === p.id ? 'following' : ''}`} style={{ background: p.color }}
              title={followId === p.id ? `Перестать следить за ${p.name}` : `Следить за ${p.name}: карта повторяет его вид`}
              aria-pressed={followId === p.id}
              onClick={() => {
                const next = followId === p.id ? null : p.id;
                setFollowId(next);
                const view = (p as { view?: { center: [number, number]; zoom: number } }).view;
                if (next && view) mapRef.current?.jumpTo(view.center, view.zoom);
                if (next) setNotice(`Вы следите за ${p.name}. Нажмите на аватар ещё раз, чтобы перестать.`);
              }}>{initials(p.name)}</button>
          ))}
        </div>
        <button className="btn" onClick={shareLink} title="Скопировать ссылку для совместной работы">Поделиться</button>
        {nameEdit !== null && (
          <div className="name-pop">
            <input autoFocus value={nameEdit} onChange={(e) => setNameEdit(e.target.value)} aria-label="Ваше имя"
              onKeyDown={(e) => { if (e.key === 'Enter') commitName(); if (e.key === 'Escape') setNameEdit(null); }} />
            <button className="btn small primary" onClick={commitName}>OK</button>
          </div>
        )}
        <button className="btn" onClick={() => { setBottomTab('issues'); if (doc) scheduleValidate(doc.doc_id); }}>
          Проверить
          {counts && <span className={`count ${counts.critical ? 'crit' : counts.warning ? 'warn' : 'ok'}`}>
            {counts.critical + counts.warning}</span>}
        </button>
        <button className="btn" onClick={checkSumo} title="Локальный прогон SUMO netconvert">SUMO-проверка</button>
        <button className="btn" onClick={uploadOrganizer} title="POST на сервер проверки. Нужны ORGANIZER_TOKEN и TEAM_NAME">На сервер проверки</button>
        <label className="team">
          <span className="sr-only">Имя файла (название команды)</span>
          <input value={team} onChange={(e) => setTeam(e.target.value.replace(/[^\w\-]/g, ''))} aria-label="Название команды" />
          <span className="muted mono">.osm</span>
        </label>

        <button className="btn primary" onClick={() => { setExportCheck(true); scheduleValidate(doc.doc_id); }}>Экспорт .osm</button>
      </header>

      <div className="body">
        {mode === 'edit' && win.layout.visible.tools && <ToolRail tool={tool} onTool={setTool} />}
        {win.layout.visible.left && (
          <>
            <LayersPanel layers={layers} onLayers={onLayers} onSelectAll={onSelectAllLayers}
              baseOpacity={baseOpacity} onBaseOpacity={setBaseOpacity}
              canApplyTemplate={selectedWay?.layer === 'road'} onTemplate={applyTemplate}
              drawOrder={drawOrder} onDrawOrder={setDrawOrder}
              basemap={<BasemapControls base={base} onBase={setBase} overlay={overlay} onOverlay={setOverlay} onOverlayFile={onOverlayFile} />} />
            <Splitter panel="left" value={win.layout.size.left} onResize={(v) => win.resize('left', v)} label="Ширина панели слоёв" />
          </>
        )}
        <main className="center">
          <div className="map-wrap">
            <MapView ref={mapRef} geo={doc.geo} diff={mapDiff} selection={selection} tool={tool} layers={layers}
              drawOrder={drawOrder} route={routeOverlay} base={base} overlay={overlay} onOverlay={setOverlay}
              analysis={analysisOverlay} pickPoint={analysisPick && mode === 'analytics'} onPickPoint={setAnalysisPicked}
              segment={segment} multiSelection={multi} onToggleSelection={(s) => setMulti((m) => toggleSelection(m.length ? m : selection ? [selection] : [], s))}
              onBoxSelection={setMulti} onSegment={setSegment}
              threeD={threeD} highlight={mapHighlight} onView={onView} fixPreview={fixReview?.preview ?? null}
              baseOpacity={baseOpacity} issues={mapIssues} onSelect={(s) => { setSelection(s); setMulti([]); setSegment(null); }} loadVc={loadVc} analytics={mode === 'analytics'}
              changes={bottomTab === 'changes' && changesOverlay ? changesData : null}
              onOp={onMapOp} onHint={setHint} onCursor={onCursor} wayById={wayById} />
            {analysisOverlay.features.some(f=>f.properties?.mock) && <div className="analysis-legend">Интервалы без OSM interval — условные (mock); скорость ОТ и ожидание — допущения</div>}
            <div className="map-hint">{hint ?? TOOL_HINTS[tool]}</div>
            {fixReview && (
              <FixReview key={`${fixReview.issue.id}:${fixReview.fix.op}:${fixReview.fix.label}`} review={fixReview}
                onApply={applyReviewedFix} onCancel={cancelFixReview} />
            )}
            {notice && !error && (
              <div className="toast notice" role="status">{notice}
                <button className="icon-btn" aria-label="Закрыть" onClick={() => setNotice(null)}>×</button></div>
            )}
            {error && <div className="toast error" role="alert">{error}<button className="icon-btn" aria-label="Закрыть" onClick={() => setError(null)}>×</button></div>}
          </div>
          {win.layout.visible.bottom && (
            <Splitter panel="bottom" value={win.layout.size.bottom} onResize={(v) => win.resize('bottom', v)}
              label="Высота нижней панели" />
          )}
          {win.layout.visible.bottom && (
          <BottomPanel validation={validation} history={doc.history} tab={bottomTab} onTab={setBottomTab}
            validating={validating} onShow={showIssue} onFix={onFix}
            issueFilters={issueFilters} onIssueFilter={onIssueFilter} onSelectAllIssues={onSelectAllIssues}
            onShowRefs={onShowRefs} onDownloadLog={downloadEditLog} onFixAll={() => setFixAllOpen(true)}>
            {bottomTab === 'load' && (
              <LoadPanel load={load} comparison={comparison} onMultiplier={onMultiplier} onSnapshot={onSnapshot}
                onDeleteSnapshot={onDeleteSnapshot} onCompare={onCompare} onReset={onResetLoad} onShowRoad={onShowRoad}
                colorOn={loadColor} onColor={setLoadColor} />
            )}
            {bottomTab === 'changes' && (
              <ChangesPanel data={changesData} onShow={showElement} overlay={changesOverlay} onOverlay={setChangesOverlay} />
            )}
          </BottomPanel>
          )}
        </main>
        {win.layout.visible.right && (
          <Splitter panel="right" value={win.layout.size.right} onResize={(v) => win.resize('right', v)}
            label="Ширина панели свойств" />
        )}
        <AnalyticsPanel key={doc.doc_id} active={win.layout.visible.right && mode === 'analytics' && !selection}
          analysis={{docId:doc.doc_id,rev:doc.rev,picked:analysisPicked,onPick:setAnalysisPick,onOverlay:setAnalysisOverlay,onFix,onShowRoad}}
          docId={doc.doc_id} rev={doc.rev} load={load} onMultiplier={onMultiplier} onShowRoad={onShowRoad}
          onFix={onFix} onScenarioChanged={refreshLoad} onDetour={async (id,index) => {
              try {const next=await loadMutations.current.run(()=>api.setClosure(doc.doc_id,id,true,index));if(docIdRef.current!==doc.doc_id)return;setLoad(next);const options=next.closures?.find(c=>c.id===id)?.alternatives ?? [];setAnalysisOverlay({type:'FeatureCollection',features:options.flatMap((a,i)=>a.lines.map(coordinates=>({type:'Feature' as const,geometry:{type:'LineString' as const,coordinates},properties:{before:i!==index}})))});} catch(e){setError((e as Error).message);}
            }} onReopen={(id) => onClosure(id,false)} onOpenTasks={() => setTasksOpen(true)} />
        {win.layout.visible.right && (mode !== 'analytics' || selection) && (mode === 'edit' && (multi.length > 1 || segment) ?
          <SelectionEditor multi={multi} segment={segment} geo={doc.geo} onOp={onInspectorOp} onFix={onFix} onClear={() => {setMulti([]);setSegment(null);}} /> : (
        <Inspector way={selectedWay} node={selectedNode} geometry={selectedGeometry} loadRow={loadRow} nodeOnRoad={nodeOnRoad}
          canSeparate={canSeparate} sharedJunctions={sharedJunctions}
          onSetLoad={onSetLoad} onOp={onInspectorOp} docId={doc.doc_id} rev={doc.rev} openRoute={openRoute}
          onOpenRoute={setOpenRoute} onScenarioChanged={refreshLoad}
          closed={selection?.kind === 'way' && (load?.closures ?? []).some((c) => c.id === selection.id)}
          onClosure={onClosure} onObjectReport={objectReport} onAutofill={autofillBuildings}
          readOnly={mode === 'analytics'} />
        ))}
      </div>
      {fixAllOpen && (
        <FixAllDialog docId={doc.doc_id} onClose={() => setFixAllOpen(false)}
          onApply={(ops, label) => { runOps(ops, { label }); }}
          onShow={(loc, ref) => {
            if (loc) mapRef.current?.flyTo(loc[0], loc[1], 18);
            if (ref && (ref.kind === 'way' || ref.kind === 'node')) setSelection({ kind: ref.kind, id: ref.id });
          }} />
      )}
      {tasksOpen && <TasksModal docId={doc.doc_id} selection={selection} onClose={() => setTasksOpen(false)} />}

      <footer className="status mono">
        <span>узлов {doc.stats.nodes}</span><span>рёбер {doc.stats.ways}</span><span>светофоров {doc.stats.signals}</span>
        <span>правок {doc.history.entries.length}</span>
        {validation && <span className={validation.summary.components <= 1 ? 'good' : 'bad'}>
          ● компонент связности: {validation.summary.components}</span>}
        {validation && validation.summary.outside_strong > 0 && (
          <span className="bad">● вне сильной связности: {validation.summary.outside_strong}</span>
        )}
        <span className="grow" />
        <span ref={cursorEl} style={{ whiteSpace: 'pre' }} />
      </footer>

      {organizer && (
        <div className="modal-back" onClick={() => organizer !== 'running' && setOrganizer(null)}>
          <div className="modal" role="dialog" aria-label="Отправка на сервер проверки" onClick={(e) => e.stopPropagation()}>
            <h2>Сервер проверки</h2>
            {organizer === 'running' ? <p>Отправка .osm…</p> : <OrganizerReport result={organizer} />}
            {organizer !== 'running' && <button className="btn" onClick={() => setOrganizer(null)}>Закрыть</button>}
          </div>
        </div>
      )}

      {exportCheck && (
        <ExportCheck validation={validating ? null : validation} filename={`${team || 'team'}.osm`}
          onClose={() => setExportCheck(false)}
          onFixList={() => { setExportCheck(false); setBottomTab('issues'); }}
          onExport={() => { setExportCheck(false); exportOsm(); }} />
      )}
      {report !== undefined && (
        <ReportModal data={report} onClose={() => setReport(undefined)}
          onDownload={downloadReport} />
      )}

      {sumo && (
        <div className="modal-back" onClick={() => sumo !== 'running' && setSumo(null)}>
          <div className="modal" role="dialog" aria-label="Результат SUMO netconvert" onClick={(e) => e.stopPropagation()}>
            <h2>Локальная проверка SUMO</h2>
            {sumo === 'running' ? <p>netconvert строит сеть SUMO…</p> : !sumo.available ? <p>{sumo.message}</p> : (
              <SumoReport sumo={sumo} onShow={(kind, id) => { setSumo(null); showElement(kind, id); }} />
            )}
            {sumo !== 'running' && <button className="btn" onClick={() => setSumo(null)}>Закрыть</button>}
          </div>
        </div>
      )}
      </PanelToggle.Provider>
    </div>
  );
}

function VersionNotice() {
  return (
    <div className="version-banner" role="status">
      <p>Версия страницы устарела. Обновите её, чтобы продолжить в актуальном редакторе.</p>
      <button type="button" className="btn small primary" onClick={() => window.location.reload()}>Обновить страницу</button>
    </div>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const text = parts.length > 1 ? parts[0][0] + parts[1][0] : (parts[0] ?? '?').slice(0, 2);
  return text.toUpperCase();
}
