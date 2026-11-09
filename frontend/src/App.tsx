import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type OpResponse } from './api';
import MapView, { type MapHandle } from './components/MapView';
import Inspector from './components/Inspector';
import { BottomPanel, LayersPanel, TOOLS, TOOL_HINTS, ToolRail } from './components/Panels';
import StartScreen from './components/StartScreen';
import type { DocState, Geo, GeoDiff, Issue, LayerVisibility, NodeProps, Op, OrganizerResult, Selection, SumoResult, Tool, ValidationResult, WayProps } from './types';
import { applyPatch } from './utils/geoPatch';
import { TEMPLATES, templateOps, type RoadTemplate } from './utils/osm';

const DEFAULT_LAYERS: LayerVisibility = {
  roads: true, paths: true, rail: true, signals: true, crossings: true, stops: true, buildings: true, issues: true,
};
const STORAGE_KEY = 'uds-editor-session';
const NO_ISSUES: Issue[] = [];

export default function App() {
  const [doc, setDoc] = useState<DocState | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [tool, setTool] = useState<Tool>('select');
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [layers, setLayers] = useState(DEFAULT_LAYERS);
  const [baseOpacity, setBaseOpacity] = useState(50);
  const [bottomTab, setBottomTab] = useState<'issues' | 'history'>('issues');
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cursor coordinates are written straight into the status bar. Keeping them in
  // React state re-rendered the whole editor on every mouse move.
  const cursorEl = useRef<HTMLSpanElement>(null);
  const onCursor = useCallback((ll: [number, number] | null, zoom: number) => {
    if (cursorEl.current) {
      cursorEl.current.textContent = (ll ? `${ll[1].toFixed(5)}° N · ${ll[0].toFixed(5)}° E   ` : '') + `z ${zoom.toFixed(1)}`;
    }
  }, []);
  const [mapDiff, setMapDiff] = useState<GeoDiff | null>(null);
  // latest map data, updated synchronously so back-to-back patches never merge onto a stale base
  const geoRef = useRef<Geo | null>(null);
  const [team, setTeam] = useState('team1');
  const [sumo, setSumo] = useState<SumoResult | 'running' | null>(null);
  const [organizer, setOrganizer] = useState<OrganizerResult | 'running' | null>(null);
  const [booting, setBooting] = useState(true);
  const mapRef = useRef<MapHandle>(null);

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
    setMapDiff(null);
    setDoc(d);
    setSelection(null);
    setValidation(null);
    scheduleValidate(d.doc_id);
  }, [scheduleValidate]);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) { setBooting(false); return; }
    let saved: { doc_id?: string; team?: string } = {};
    try { saved = JSON.parse(raw); } catch { setBooting(false); return; }
    if (saved.team) setTeam(saved.team);
    if (!saved.doc_id) { setBooting(false); return; }
    api.restore(saved.doc_id)
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

  // keep selection valid after the document changes
  useEffect(() => {
    if (!doc || !selection) return;
    if (selection.kind === 'way' && !wayIndex.has(selection.id)) setSelection(null);
    if (selection.kind === 'node' && !selectedNode) setSelection(null);
  }, [doc, selection, wayIndex, selectedNode]);

  // -------------------------------------------------------------- ops
  const docId = doc?.doc_id;
  const runOps = useCallback(async (ops: Op | Op[], opts?: { select?: 'result' | 'keep'; label?: string }): Promise<boolean> => {
    if (!docId) return false;
    setError(null);
    try {
      let res: OpResponse;
      const list = Array.isArray(ops) ? ops : [ops];
      if (list.length === 1) res = await api.op(docId, list[0], opts?.label);
      else res = await api.batch(docId, list, opts?.label);
      applyState(res);
      if (opts?.select === 'result') {
        const id = res.result?.id ?? res.results?.[res.results.length - 1]?.id;
        const op = list[list.length - 1].op;
        if (id !== undefined) setSelection({ kind: op === 'add_way' ? 'way' : 'node', id });
      }
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }, [docId, applyState]);

  // stable callbacks so memoized panels and the map skip unrelated re-renders
  const onMapOp = useCallback((op: Op, o?: { select?: 'result' | 'keep' }) => runOps(op, { select: o?.select }), [runOps]);
  const onFix = useCallback((i: Issue) => {
    if (i.fix) runOps({ op: i.fix.op, args: i.fix.args }, { label: i.fix.label + ': ' + i.title });
  }, [runOps]);
  const onInspectorOp = useCallback((op: Op | Op[], label?: string) => { runOps(op, { label }); }, [runOps]);
  const mapIssues = layers.issues ? validation?.issues ?? NO_ISSUES : NO_ISSUES;

  const undo = useCallback(async () => {
    if (doc?.history.can_undo) applyState(await api.undo(doc.doc_id));
  }, [doc, applyState]);
  const redo = useCallback(async () => {
    if (doc?.history.can_redo) applyState(await api.redo(doc.doc_id));
  }, [doc, applyState]);

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
      if ((e.ctrlKey || e.metaKey) && (k === 'z' || k === 'я')) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if ((e.ctrlKey || e.metaKey) && (k === 'y' || k === 'н')) { e.preventDefault(); redo(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'Escape' && tool !== 'way') { setTool('select'); setSelection(null); return; }
      if ((e.key === 'Delete' || (e.key === 'Backspace' && tool !== 'way')) && selection) {
        runOps({ op: 'delete', args: { kind: selection.kind, id: selection.id } });
        return;
      }
      const byCode = TOOLS.find((x) => e.code === `Key${x.key}`);
      if (byCode) { setTool(byCode.id); return; }
      const tpl = TEMPLATES.find((x) => x.key === e.key);
      if (tpl) applyTemplate(tpl);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tool, selection, undo, redo, runOps, applyTemplate]);

  useEffect(() => { setHint(null); }, [tool]);

  // ------------------------------------------------------------ export
  const exportOsm = () => {
    if (!doc) return;
    const a = document.createElement('a');
    a.href = api.exportUrl(doc.doc_id, team || 'team');
    a.download = `${team || 'team'}.osm`;
    a.click();
  };

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
      setOrganizer(null);
      setError((e as Error).message);
    }
  };

  const closeDocument = () => {
    const closingId = doc?.doc_id;
    localStorage.removeItem(STORAGE_KEY);
    if (closingId) api.discardDraft(closingId).catch(() => undefined);
    geoRef.current = null;
    setMapDiff(null);
    setDoc(null);
    setValidation(null);
    setSelection(null);
  };

  if (booting) return <div className="start"><p className="muted">Восстановление правок…</p></div>;
  if (!doc) return <StartScreen onLoaded={loadDoc} />;

  const counts = validation?.summary.counts;
  const changes = Object.values(doc.diff).reduce((s, d) => s + d.added + d.modified + d.deleted, 0);

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#3B82F6" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="5" cy="6" r="2" /><circle cx="19" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M7 6h10M6 8l5 8M18 8l-5 8" />
          </svg>
          <b>УДС·Редактор</b>
        </div>
        <div className="file-chip mono">
          {doc.filename}
          {changes > 0 && <><span className="dot" aria-hidden="true" /><span className="muted">изменено объектов: {changes}</span></>}
        </div>
        <button className="btn ghost" onClick={closeDocument}>Открыть другой</button>
        <div className="row gap2">
          <button className="icon-btn" aria-label="Отменить" title={`Отменить${doc.history.undo_label ? ': ' + doc.history.undo_label : ''} · Ctrl+Z`}
            disabled={!doc.history.can_undo} onClick={undo}>↶</button>
          <button className="icon-btn" aria-label="Повторить" title={`Повторить${doc.history.redo_label ? ': ' + doc.history.redo_label : ''} · Ctrl+Shift+Z`}
            disabled={!doc.history.can_redo} onClick={redo}>↷</button>
        </div>
        <span className="grow" />
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
        <button className="btn primary" onClick={exportOsm}>Экспорт .osm</button>
      </header>

      <div className="body">
        <ToolRail tool={tool} onTool={setTool} />
        <LayersPanel layers={layers} onLayers={setLayers} baseOpacity={baseOpacity} onBaseOpacity={setBaseOpacity}
          canApplyTemplate={selectedWay?.layer === 'road'} onTemplate={applyTemplate} />
        <main className="center">
          <div className="map-wrap">
            <MapView ref={mapRef} geo={doc.geo} diff={mapDiff} selection={selection} tool={tool} layers={layers}
              baseOpacity={baseOpacity} issues={mapIssues} onSelect={setSelection}
              onOp={onMapOp} onHint={setHint} onCursor={onCursor} wayById={wayById} />
            <div className="map-hint">{hint ?? TOOL_HINTS[tool]}</div>
            {error && <div className="toast error" role="alert">{error}<button className="icon-btn" aria-label="Закрыть" onClick={() => setError(null)}>×</button></div>}
          </div>
          <BottomPanel validation={validation} history={doc.history} tab={bottomTab} onTab={setBottomTab}
            validating={validating} onShow={showIssue} onFix={onFix} />
        </main>
        <Inspector way={selectedWay} node={selectedNode} onOp={onInspectorOp} />
      </div>

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
            {organizer === 'running' ? <p>Отправка .osm…</p> : (
              <>
                <p className={organizer.ok ? 'good' : 'bad'}>{organizer.ok ? `Принято: ${organizer.filename}` : 'Сервер отклонил файл'}</p>
                {organizer.message && <pre className="log">{organizer.message}</pre>}
              </>
            )}
            {organizer !== 'running' && <button className="btn" onClick={() => setOrganizer(null)}>Закрыть</button>}
          </div>
        </div>
      )}

      {sumo && (
        <div className="modal-back" onClick={() => sumo !== 'running' && setSumo(null)}>
          <div className="modal" role="dialog" aria-label="Результат SUMO netconvert" onClick={(e) => e.stopPropagation()}>
            <h2>Локальная проверка SUMO</h2>
            {sumo === 'running' ? <p>netconvert работает…</p> : !sumo.available ? <p>{sumo.message}</p> : (
              <>
                <p className={sumo.ok ? 'good' : 'bad'}>{sumo.ok ? 'Сеть построена без ошибок' : 'netconvert сообщил об ошибках'}</p>
                {sumo.stats && <p className="mono">рёбер {sumo.stats.edges} · перекрёстков {sumo.stats.junctions} · светофоров {sumo.stats.traffic_lights}</p>}
                {sumo.errors.length > 0 && <pre className="log bad">{sumo.errors.join('\n')}</pre>}
                <p className="muted">Предупреждений: {sumo.warning_count ?? sumo.warnings.length}</p>
                {sumo.warnings.length > 0 && <pre className="log">{sumo.warnings.slice(0, 60).join('\n')}</pre>}
              </>
            )}
            {sumo !== 'running' && <button className="btn" onClick={() => setSumo(null)}>Закрыть</button>}
          </div>
        </div>
      )}
    </div>
  );
}
