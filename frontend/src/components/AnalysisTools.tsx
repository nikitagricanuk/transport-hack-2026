import type { PtRoute } from './PtTools';
import { PtTools } from './PtTools';
import { StopsTools } from './StopsTools';
import { useEffect, useMemo, useState } from 'react';
import type { FeatureCollection } from 'geojson';
import { api } from '../api';
import type { Fix, Issue } from '../types';
import { analysisOverlay, cycleStop, formatRoute, pickedObject, type AnalysisPoint, type AnalysisResult, type PickedPoint, type RouteQuery } from '../utils/networkAnalysis';

export interface AnalysisProps {
  docId: string;
  rev: number;
  picked: PickedPoint | null;
  onPick: (active: boolean) => void;
  onOverlay: (data: FeatureCollection) => void;
  onFix: (issue: Issue, fix: Fix, probe?: RouteQuery) => void;
  onShowRoad: (id: number) => void;
  onShowNode?: (id: number) => void;
  onFlyTo?: (lon: number, lat: number) => void;
}
export function AnalysisTools({ docId, rev, picked, onPick, onOverlay, onFix, onShowRoad, onShowNode, onFlyTo }: AnalysisProps) {
  const [tab, setTab] = useState<'route' | 'catchment' | 'isochrone' | 'pt' | 'stops'>('route');
  const [mode, setMode] = useState<'car' | 'walk'>('car');
  const [metric, setMetric] = useState<'time' | 'length'>('time');
  const [waiting, setWaiting] = useState<'a' | 'b' | 'stops' | null>(null);
  const [a, setPointA] = useState<AnalysisPoint | null>(null);
  const [b, setPointB] = useState<AnalysisPoint | null>(null);
  const [stopRoutes, setStopRoutes] = useState<PtRoute[]>([]);
  useEffect(() => { let alive = true; api.ptParams(docId).then(r => { if (alive) setStopRoutes(r.routes); }).catch(() => undefined); return () => { alive = false; }; }, [docId,rev]);
  const [setA, setSetA] = useState<number[]>([]);
  const [setB, setSetB] = useState<number[]>([]);
  const [walk, setWalk] = useState(4.5);
  const [compare, setCompare] = useState(false);
  const [closures, setClosures] = useState(false);
  const [step, setStep] = useState(1);
  const [count, setCount] = useState(5);
  const [limit, setLimit] = useState(500);
  const [unit, setUnit] = useState<'m' | 'min'>('m');
  const [buffer, setBuffer] = useState(40);
  const [loaded, setLoaded] = useState(false);
  const [how, setHow] = useState('buffer');
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const arm = (which: typeof waiting) => { setWaiting(which); onPick(which !== null); };
  useEffect(() => {
    if (!picked || !waiting) return;
    if (waiting === 'stops') {
      if (picked.nodeId === undefined) { setError('Выберите узел остановки на карте.'); return; }
      const [aa, bb] = cycleStop(setA, setB, picked.nodeId);
      setSetA(aa); setSetB(bb);
    } else {
      const p: AnalysisPoint = pickedObject(picked);
      // A building is an object start; roads retain the clicked position.
      if (waiting === 'a') setPointA(p); else setPointB(p);
      setWaiting(null); onPick(false);
    }
  // set changes must not replay a click.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picked]);
  useEffect(() => () => onPick(false), [onPick]);
  const query = useMemo(() => ({ from: a, to: b, mode, metric, walk_kmh: walk, use_closures: closures,
    compare: compare ? 'original' as const : null, step_min: step, count, buffer_m: tab === 'catchment' ? buffer : 30,
    limit_m: unit === 'm' ? limit : 500, limit_min: unit === 'min' ? limit : null, set_a: setA, set_b: setB,
    speed_source: loaded ? 'loaded' : 'free', how }), [a,b,mode,metric,walk,closures,compare,step,count,buffer,unit,limit,setA,setB,loaded,how,tab]);
  useEffect(() => {
    let alive = true;
    setResult(null); setError('');
    if (tab === 'pt' || tab === 'stops') { setBusy(false); return; }
    if (tab === 'route' ? !a || !b : tab === 'isochrone' ? !a : !setA.length && !setB.length) { setBusy(false); return; }
    setBusy(true);
    const timer = setTimeout(() => {
      const run = tab === 'route' ? api.analysisRoute : tab === 'catchment' ? api.analysisCatchment : api.analysisIsochrone;
      run(docId, tab === 'catchment' ? { ...query, mode: 'walk' } : query)
        .then(r => { if (alive) { setResult(r); setBusy(false); } })
        .catch((e: Error) => { if (alive) { setError(e.message); setBusy(false); } });
    }, 200);
    return () => { alive = false; clearTimeout(timer); };
  }, [docId,rev,query,tab]);
  useEffect(() => { if (tab !== 'pt' && tab !== 'stops') onOverlay(analysisOverlay(result,a,b)); }, [result,a,b,onOverlay,tab]);
  const probe = tab === 'route' && a && b ? { from:a, to:b, mode, metric, walk_kmh:walk, use_closures:closures, compare:null } : undefined;
  return <section className="stack10 analysis-tools" aria-label="Анализ сети">
    <div className="row gap8" role="tablist" aria-label="Инструменты анализа">
      {([['route','Маршрут'],['catchment','Доступность ОП'],['isochrone','Изохроны'],['pt','Доступность на ОТ'],['stops','Остановки']] as const).map(([id,label]) =>
        <button className="btn small" role="tab" aria-selected={tab === id} key={id} onClick={() => { setTab(id); arm(null); }}>{label}</button>)}
    </div>
    {tab === 'stops' ? <StopsTools docId={docId} rev={rev} onOverlay={onOverlay} onShowRoad={onShowRoad} onShowNode={onShowNode} onFlyTo={onFlyTo} />
    : tab === 'pt' ? <PtTools docId={docId} rev={rev} picked={picked} onPick={onPick} onOverlay={onOverlay} onFix={onFix} onShowRoad={onShowRoad} /> : <>
    <p className="muted small">Анализ не изменяет граф. Исправления открываются для проверки в режиме редактирования.</p>
    {tab !== 'catchment' && <>
      <label>Способ движения <select value={mode} onChange={e => setMode(e.target.value as typeof mode)}><option value="car">Авто</option><option value="walk">Пешком</option></select></label>
      <div className="row gap8"><button className="btn small" aria-pressed={waiting === 'a'} onClick={() => arm('a')}>Точка А {a ? '✓' : ''}</button>
        {tab === 'route' && <button className="btn small" aria-pressed={waiting === 'b'} onClick={() => arm('b')}>Точка Б {b ? '✓' : ''}</button>}</div>
      {tab === 'route' && <label>Оптимизировать <select value={metric} onChange={e => setMetric(e.target.value as typeof metric)}><option value="time">Время</option><option value="length">Длина</option></select></label>}
      <label><input type="checkbox" checked={closures} onChange={e => setClosures(e.target.checked)} /> Учитывать закрытые дороги</label>
    </>}
    {tab === 'isochrone' && <>
      <label>Шаг, мин <select value={step} onChange={e => setStep(Number(e.target.value))}>{[.5,1,2,5].map(n => <option key={n}>{n}</option>)}</select></label>
      <label>Количество <input type="number" min={1} max={10} value={count} onChange={e => setCount(Number(e.target.value))} /></label>
      <label><input type="checkbox" checked={loaded} onChange={e => setLoaded(e.target.checked)} /> С учётом загрузки (сценарий BPR)</label>
      <label>Полигон <select value={how} onChange={e => setHow(e.target.value)}><option value="buffer">Коридор 30 м — допущение</option><option value="hull">Concave hull 0,3 — отрисовка</option></select></label>
    </>}
    {tab === 'catchment' && <>
      <label>По маршруту… <select defaultValue="" onChange={e => {
        const route = stopRoutes.find(r => String(r.relation_id) === e.target.value);
        if (!route) return;
        const directions = stopRoutes.filter(r => r.mode === route.mode && r.ref === route.ref);
        setSetA([...new Set(directions[0]?.stop_ids ?? [])]); setSetB([...new Set(directions[1]?.stop_ids ?? [])]);
      }}><option value="">Выберите маршрут (первые два направления)</option>{stopRoutes.filter((r,i,all) => all.findIndex(s => s.mode === r.mode && s.ref === r.ref) === i).map(r => <option key={r.relation_id} value={r.relation_id}>{r.mode} {r.ref}</option>)}</select></label>
      <button className="btn small" aria-pressed={waiting === 'stops'} onClick={() => arm(waiting === 'stops' ? null : 'stops')}>Выбирать остановки: А → Б → убрать</button>
      <p className="small">А (выезд): {setA.join(', ') || 'не выбраны'}<br />Б (возврат): {setB.join(', ') || 'не выбраны'}</p>
      <label>Предел <input type="number" min={1} max={unit === 'm' ? 5000 : 60} value={limit} onChange={e => setLimit(Number(e.target.value))} />
        <select value={unit} onChange={e => { const next = e.target.value as typeof unit; setLimit(next === 'm' ? Math.round(limit*walk/60*1000) : Number((limit/walk*60/1000).toFixed(3))); setUnit(next); }}><option value="m">м</option><option value="min">мин</option></select></label>
      <div className="row gap8">{[300,400,500,600].map(n => <button className="btn small" key={n} onClick={() => {setLimit(n);setUnit('m');}}>{n} м</button>)}</div>
      <label>Коридор, м — допущение <input type="number" min={1} max={100} value={buffer} onChange={e => setBuffer(Number(e.target.value))} /></label>
      <p className="small">Синий — А · оранжевый — Б · фиолетовый — обе стороны · серый — нет доступа.<br />Население: нет данных (ИД5 не передан). Поправки на рельеф нет: отсутствуют высоты.</p>
    </>}
    {(mode === 'walk' || tab === 'catchment') && <label>Пешком, км/ч — допущение <input type="number" min={1} max={12} step={.1} value={walk} onChange={e => setWalk(Number(e.target.value))} /></label>}
    <label><input type="checkbox" checked={compare} onChange={e => setCompare(e.target.checked)} /> Сравнить с исходным файлом</label>
    {waiting && <p role="status">Выберите {waiting === 'stops' ? 'остановку' : `точку ${waiting === 'a' ? 'А' : 'Б'}`} на карте. <button className="btn small" onClick={() => arm(null)}>Отмена</button></p>}
    {busy && <p role="status">Расчёт…</p>}{error && <p role="alert" className="bad">{error}</p>}
    {result?.stats && <div className="card"><b>{formatRoute(result.stats)}</b><p>{result.stats.avg_kmh.toFixed(1)} км/ч · {result.stats.edges} рёбер</p>
      <p className="small">Запретов учтено: {result.stats.restrictions_applied}. Пропущено: {result.stats.restrictions_skipped.join(', ') || 'нет'}.</p>
      {result.stats.ways.map(w => <button key={w.id} className="btn small" onClick={() => onShowRoad(w.id)}>{w.name}</button>)}</div>}
    {result && result.found === false && <b>Путь не найден</b>}
    {result?.compare && <p className="small">Было: {result.compare.before.stats ? formatRoute(result.compare.before.stats) : result.compare.before.found === false ? 'нет пути' : 'пунктир на карте'} → стало: {result.stats ? formatRoute(result.stats) : result.found === false ? 'нет пути' : 'цветная заливка'}</p>}
    {result?.bands && <table className="small"><thead><tr><th>Мин</th><th>км²</th><th>км сети</th><th>кольцо, км</th><th>Δ км</th></tr></thead><tbody>{result.bands.map((band,i) => <tr key={band.minutes}><td>{band.minutes}</td><td>{band.area_km2.toFixed(3)}</td><td>{band.road_km.toFixed(2)}</td><td>{band.ring_road_km.toFixed(2)}</td><td>{result.compare?.before.bands?.[i] ? (band.road_km-result.compare.before.bands[i].road_km).toFixed(2) : '—'}</td></tr>)}</tbody></table>}
    {result?.boundary_limited && <p className="warning small">У границы выгрузки — зона может быть занижена.</p>}
    {result?.buildings && <div className="card small">Зданий: {result.buildings.total}. Обе стороны: {result.buildings.both}; одна: {result.buildings.one_side}; нет доступа: {result.buildings.none}.
      {result.compare?.before.buildings && <p>Было без доступа: {result.compare.before.buildings.none}.</p>}
      <p>Пешком: А {result.network?.walk_km_reached_a.toFixed(2)} км · Б {result.network?.walk_km_reached_b.toFixed(2)} км. Компонент с остановками: {result.network?.components_with_stops}.</p></div>}
    {result?.issues.map(i => <div className="issue" key={i.id}><b>{i.title}</b><p className="small">{i.description}</p><div className="issue-fixes">{i.fixes.map((fix,j) => <button key={j} className="btn small" onClick={() => onFix(i,fix,probe)}>{fix.label}</button>)}</div></div>)}
    {result && <details open className="small"><summary>Допущения и источники</summary>{result.assumptions.map(a => <p key={a.key}>{a.label}</p>)}{result.sources.map(s => <p key={s.label}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.label}</a> : s.label}</p>)}</details>}
  </>}
  </section>;
}
