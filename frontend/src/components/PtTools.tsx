import { useEffect, useState } from 'react';
import { api } from '../api';
import type { AnalysisProps } from './AnalysisTools';
import { analysisOverlay, mockHeadways, pickedObject, type AnalysisPoint, type AnalysisResult } from '../utils/networkAnalysis';

export interface PtRoute { relation_id: number; mode: string; ref: string; name: string; headway_min: number; headway_source: string; stops_in_extract: number; pieces: number; stop_ids: number[] }
interface PtReach { geometry: import('geojson').Geometry; area_km2: number; stops_reached: number; routes_used: string[] }
export interface PtResult extends Omit<AnalysisResult, 'bands' | 'compare'> {
  bands: { minutes: number; no_transfer: PtReach; with_transfer: PtReach }[];
  compare?: { before: PtResult } | null;
  stops: { id: number; coord: number[]; reached: boolean }[];
  explain: { sample_points: { coord: number[]; walk_s: number; wait_s: number; ride_s: number; total_s: number }[] };
}
export function PtTools({ docId, rev, picked, onPick, onOverlay, onFix }: AnalysisProps) {
  const [routes, setRoutes] = useState<PtRoute[]>([]);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [sources, setSources] = useState<Record<string,'manual' | 'mock'>>({});
  const [start, setStart] = useState<AnalysisPoint | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [walk, setWalk] = useState(4.5);
  const [speed, setSpeed] = useState(20);
  const [wait, setWait] = useState(.5);
  const [transfers, setTransfers] = useState(2);
  const [bands, setBands] = useState('5,10,15');
  const [how, setHow] = useState('hull');
  const [compare, setCompare] = useState(false);
  const [variant, setVariant] = useState<'no_transfer' | 'with_transfer'>('with_transfer');
  const [result, setResult] = useState<PtResult | null>(null);
  const [sample, setSample] = useState<PtResult['explain']['sample_points'][number] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    api.ptParams(docId).then(r => {if (alive) setRoutes(r.routes);}).catch((e: Error) => {if (alive) setError(e.message);});
    return () => {alive = false;};
  }, [docId,rev]);
  useEffect(() => {
    if (!picked) return;
    if (waiting) {
      setStart(pickedObject(picked)); setWaiting(false); setSample(null);
    } else if (result) {
      const nearest = [...result.explain.sample_points].sort((a,b) => Math.hypot((a.coord[0]-picked.lon)*.612,a.coord[1]-picked.lat)-Math.hypot((b.coord[0]-picked.lon)*.612,b.coord[1]-picked.lat))[0];
      if (nearest && Math.hypot((nearest.coord[0]-picked.lon)*68000,(nearest.coord[1]-picked.lat)*110540)<80) {setSample(nearest);setError('');}
      else {setSample(null);setError('В пределах 80 м нет достигнутой вершины сети. Полигон служит отрисовкой.');}
    }
  // Handle each map click once.
  }, [picked]);
  useEffect(() => () => onPick(false), [onPick]);
  useEffect(() => {
    let alive = true;
    setResult(null);setSample(null);setError('');
    if (!start) return;
    setBusy(true);
    const timer = setTimeout(() => {
      api.analysisPt(docId,{from:start,bands_min:bands.split(',').map(Number),pt_kmh:{bus:speed,trolleybus:speed,tram:speed,minibus:speed,share_taxi:speed},
        walk_kmh:walk,wait_factor:wait,max_transfers:transfers,headway_overrides:overrides,headway_sources:sources,how,compare:compare?'original':null})
        .then(r => {if (alive) {setResult(r);setBusy(false);}}).catch((e: Error) => {if (alive) {setError(e.message);setBusy(false);}});
    },250);
    return () => {alive=false;clearTimeout(timer);};
  }, [docId,rev,start,bands,speed,walk,wait,transfers,overrides,sources,how,compare]);
  useEffect(() => {
    const convert = (r: PtResult): AnalysisResult => ({...r,compare:null,bands:r.bands.map(b => ({minutes:b.minutes,geometry:b[variant].geometry,
      area_km2:b[variant].area_km2,road_km:0,ring_road_km:0}))});
    const visible = result ? convert(result) : null;
    if (visible && result?.compare?.before) visible.compare={before:convert(result.compare.before)};
    const overlay = analysisOverlay(visible,start);
    for (const stop of result?.stops ?? []) overlay.features.push({type:'Feature',geometry:{type:'Point',coordinates:stop.coord},properties:{colour:stop.reached?'#16a34a':'#94a3b8'}});
    for (const f of overlay.features) f.properties = { ...f.properties, mock: Object.values(sources).some(s => s === 'mock') };
    onOverlay(overlay);
  }, [result,variant,start,onOverlay,sources]);
  const mock = Object.values(sources).some(s => s === 'mock');
  return <div className="stack10">
    <b>Доступность на ОТ</b>
    <button className="btn small" onClick={() => {setWaiting(true);onPick(true);}}>Выбрать начало {start ? '✓' : ''}</button>
    <p className="small">{waiting ? 'Выберите начало на карте.' : start ? 'Клик по карте — состав времени в ближайшей достигнутой вершине (до 80 м).' : 'Начало: точка на карте.'}</p>
    <label>Границы, мин (через запятую) <input value={bands} onChange={e => setBands(e.target.value)} /></label>
    <div className="card stack10"><b>Допущения</b>
      <label>Скорость ОТ: {speed} км/ч — допущение <input type="range" min={5} max={60} value={speed} onChange={e => setSpeed(Number(e.target.value))} /></label>
      <label>Пешком: {walk} км/ч — допущение <input type="range" min={1} max={8} step={.1} value={walk} onChange={e => setWalk(Number(e.target.value))} /></label>
      <label>Ожидание: {wait} интервала — допущение <input type="range" min={0} max={1} step={.1} value={wait} onChange={e => setWait(Number(e.target.value))} /></label>
      <label>Пересадок не более <select value={transfers} onChange={e => setTransfers(Number(e.target.value))}>{[0,1,2,3].map(n => <option key={n}>{n}</option>)}</select></label>
    </div>
    <div className="row gap8"><button className="btn small" disabled={!routes.length} onClick={() => {setOverrides(mockHeadways(routes));setSources(Object.fromEntries(routes.map(r => [String(r.relation_id),'mock'])));}}>Заполнить тестовыми данными</button>
      <button className="btn small" onClick={() => {setOverrides({});setSources({});}}>Сбросить интервалы</button></div>
    <p className="small">Только параметры этого анализа. Тестовые интервалы 4–20 мин синтетические. Ручной ввод заменяет тестовое значение.</p>
    {mock && <p className="warning small">ТЕСТОВЫЕ ДАННЫЕ: расчёт использует синтетические интервалы.</p>}
    <details><summary>Интервалы маршрутов ({routes.length})</summary><table className="small"><thead><tr><th>Маршрут</th><th>Интервал, мин</th><th>Источник</th></tr></thead><tbody>{routes.map(r => <tr key={r.relation_id} title={r.name}><td>{r.mode} {r.ref}<br /><span className="muted">{r.stops_in_extract} ост. · {r.pieces} фрагм.</span></td>
      <td><input aria-label={`Интервал маршрута ${r.ref}, отношение ${r.relation_id}`} type="number" min={.1} max={180} step={.5} value={overrides[r.relation_id] ?? r.headway_min}
        onChange={e => {setOverrides({...overrides,[r.relation_id]:Number(e.target.value)});setSources({...sources,[r.relation_id]:'manual'});}} style={{width:64}} /></td>
      <td>{sources[r.relation_id] === 'mock' ? 'тестовые данные' : sources[r.relation_id] === 'manual' ? 'вручную' : r.headway_source}</td></tr>)}</tbody></table></details>
    <label><input type="checkbox" checked={compare} onChange={e => setCompare(e.target.checked)} /> Сравнить с исходным</label>
    <label>Результат <select value={variant} onChange={e => setVariant(e.target.value as typeof variant)}><option value="no_transfer">Без пересадок</option><option value="with_transfer">С пересадками</option></select></label>
    <label>Полигон <select value={how} onChange={e => setHow(e.target.value)}><option value="hull">Concave hull 0,3 — отрисовка</option><option value="buffer">Коридор 40 м — допущение</option></select></label>
    {busy && <p role="status">Расчёт ОТ…</p>}{error && <p className="bad" role="alert">{error}</p>}
    {result?.bands && <table className="small"><thead><tr><th>Мин</th><th>км² без пересадок</th><th>км² с пересадками</th><th>Остановок</th></tr></thead><tbody>{result.bands.map(b => <tr key={b.minutes}><td>{b.minutes}</td><td>{b.no_transfer.area_km2.toFixed(3)}</td><td>{b.with_transfer.area_km2.toFixed(3)}</td><td>{b[variant].stops_reached}</td></tr>)}</tbody></table>}
    {sample && <div className="card small">Вершина сети {sample.coord.map(n => n.toFixed(5)).join(', ')}: пешком {sample.walk_s.toFixed(0)} с + ожидание {sample.wait_s.toFixed(0)} с + поездка {sample.ride_s.toFixed(0)} с = {sample.total_s.toFixed(0)} с.</div>}
    {result?.boundary_limited && <p className="warning small">У границы выгрузки — зона может быть занижена. Поездки вне фрагмента не учитываются.</p>}
    {result?.issues.map(i => <div className="issue" key={i.id}><b>{i.title}</b><p className="small">{i.description}</p>{i.fixes.map((f,j) => <button className="btn small" key={j} onClick={() => onFix(i,f)}>{f.label}</button>)}</div>)}
    {result && <details open className="small"><summary>Допущения и источники</summary>{result.assumptions.map(a => <p key={a.key}>{a.label}</p>)}{result.sources.map(s => <p key={s.label}>{s.label}</p>)}</details>}
  </div>;
}
