import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from 'react';
import { api, downloadFile, type SimulationReportFormat } from '../api';
import type { SimulationRecording, SimulationReport } from '../types';
import { makeSampler } from '../utils/simulation';
import type { MapHandle } from './MapView';

type Engine = 'sumo' | 'matsim' | 'webots';

/** Vehicle kinds of a SUMO run; each drives only where its class may (a tram on tram tracks). */
const KINDS = [
  { kind: 'car', label: 'Легковые', icon: '🚗', def: 40 },
  { kind: 'bus', label: 'Автобусы', icon: '🚌', def: 8 },
  { kind: 'tram', label: 'Трамваи', icon: '🚋', def: 4 },
  { kind: 'truck', label: 'Грузовые', icon: '🚚', def: 6 },
  { kind: 'motorcycle', label: 'Мотоциклы', icon: '🏍', def: 4 },
  { kind: 'bicycle', label: 'Велосипеды', icon: '🚲', def: 6 },
] as const;
const MAX_TOTAL = 300;

export function Simulation({ docId, map }: { docId: string; map: RefObject<MapHandle> }) {
  const [engine, setEngine] = useState<Engine>('sumo');
  // chosen kinds and their counts; legkovye are on by default
  const [fleet, setFleet] = useState<Record<string, number>>({ car: 60 });
  const total = Object.values(fleet).reduce((a, b) => a + b, 0);
  const toggle = (kind: string, def: number) => setFleet((f) => {
    const next = { ...f };
    if (kind in next) delete next[kind]; else next[kind] = def;
    return next;
  });
  const allOn = KINDS.every((k) => k.kind in fleet);
  const [recording, setRecording] = useState<SimulationRecording | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [time, setTime] = useState(0);
  const clock = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const duration = recording?.frames[recording.frames.length - 1]?.time ?? 0;
  const sample = useMemo(() => recording && makeSampler(recording), [recording]);

  useEffect(() => () => {
    pending.current?.abort();
    map.current?.updateVehicles([]);
  }, [map]);

  useEffect(() => {
    if (!playing || !sample) return;
    let prev: number | undefined;
    let raf = 0;
    const tick = (now: number) => {
      if (prev !== undefined) {
        const dt = now - prev;
        if (dt < 30) {
          raf = requestAnimationFrame(tick);
          return;
        }
        clock.current = Math.min(duration, clock.current + Math.min(dt, 100) * speed / 1000);
        map.current?.updateVehicles(sample(clock.current));
        setTime(clock.current);
      }
      prev = now;
      if (clock.current >= duration) setPlaying(false);
      else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, sample, duration, speed, map]);

  const seek = (next: number) => {
    clock.current = next;
    setTime(next);
    if (sample) map.current?.updateVehicles(sample(next));
  };

  const reset = () => {
    pending.current?.abort();
    setBusy(false);
    setError(null);
    setPlaying(false);
    setRecording(null);
    clock.current = 0;
    setTime(0);
    map.current?.updateVehicles([]);
  };

  const load = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = new FormData(event.currentTarget);
    const params = new URLSearchParams();
    for (const [name, value] of body) {
      if (typeof value === 'string' && value.trim()) params.set(name, value.trim());
    }
    if (engine === 'sumo') {
      if (!total) { setError('Выберите хотя бы один вид транспорта'); return; }
      params.set('fleet', Object.entries(fleet).map(([k, n]) => `${k}:${n}`).join(','));
      params.set('vehicles', String(Math.max(1, Math.min(200, total))));
    }
    reset();
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    try {
      const result = await api.simulation(docId, engine, params, body, controller.signal);
      if (controller.signal.aborted) return;
      setRecording(result);
      map.current?.updateVehicles(result.frames[0].vehicles);
      setPlaying(result.frames.length > 1);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return <section className="simulation-panel" aria-label="Управление симуляцией">
   <div className="sim-layout">
    {engine === 'sumo' && <div className="sim-fleet" role="group" aria-label="Виды транспорта">
      <div className="row between"><b className="small">Транспорт</b>
        <button type="button" className="link small" onClick={() => setFleet(allOn ? {} : Object.fromEntries(KINDS.map((k) => [k.kind, fleet[k.kind] ?? k.def])))}>
          {allOn ? 'снять все' : 'выбрать все'}</button></div>
      {KINDS.map((k) => {
        const on = k.kind in fleet;
        return <div key={k.kind} className={`sim-kind${on ? ' on' : ''}`}>
          <button type="button" className="sim-kind-btn" aria-pressed={on} onClick={() => toggle(k.kind, k.def)}>
            <span aria-hidden="true">{k.icon}</span>{k.label}</button>
          {on && <input type="number" min={1} max={200} value={fleet[k.kind]} aria-label={`Количество: ${k.label}`}
            onChange={(e) => setFleet((f) => ({ ...f, [k.kind]: Math.max(1, Math.min(200, Number(e.target.value) || 1)) }))} />}
        </div>;
      })}
      <span className={`small ${total > MAX_TOTAL ? 'bad' : 'muted'}`}>Всего {total}{total > MAX_TOTAL ? ` — не больше ${MAX_TOTAL}` : ''}</span>
    </div>}
    <div className="sim-main">
    <form key={engine} onSubmit={load}>
      <label>Источник
        <select value={engine} onChange={(e) => { reset(); setEngine(e.target.value as Engine); }}>
          <option value="sumo">SUMO · новый прогон</option>
          <option value="matsim">MATSim · запись</option>
          <option value="webots">Webots · запись</option>
        </select>
      </label>
      {engine === 'matsim' && <>
        <label>Сеть <input name="network" type="file" accept=".xml,.gz" required /></label>
        <label>События <input name="events" type="file" accept=".xml,.gz" required /></label>
        <label>Координаты сети <input name="crs" defaultValue="EPSG:4326" pattern="[Ee][Pp][Ss][Gg]:[0-9]{1,8}" required /></label>
        <label>Начало, с <input name="start" type="number" min="0" max="604800" placeholder="Авто" /></label>
      </>}
      {engine === 'webots' && <label>Траектории <input name="recording" type="file" accept=".csv,.gz" required /></label>}
      <button className="btn primary" type="submit" disabled={busy || (engine === 'sumo' && (!total || total > MAX_TOTAL))}>{engine === 'sumo' ? 'Запустить' : 'Загрузить'}</button>
    </form>
    <p className="muted small">
      {engine === 'sumo' ? '90 секунд движения выбранного транспорта по текущей сети: каждый вид едет по всем дорогам, где ему разрешено (трамвай — по путям, автобус — по проезжей части).'
        : engine === 'matsim' ? 'Сеть и события одного прогона. На карте отображаются до 120 секунд движения.'
          : 'CSV из контроллера: time,id,lon,lat,angle. Координаты WGS84, угол от севера.'}
    </p>
    {busy && <p role="status">{engine === 'sumo' ? 'SUMO строит сеть и рассчитывает движение…' : 'Читаем траектории…'}</p>}
    {error && <p className="bad" role="alert">{error}</p>}
    {recording?.fleet && <p className="small">{recording.fleet.map((f) => `${f.label}: ${f.placed}${f.placed < f.requested ? ` из ${f.requested}` : ''}`).join(' · ')}</p>}
    {recording?.warnings?.map((w) => <p key={w} className="bad small">{w}</p>)}
    {recording?.rules && <details className="small sim-rules">
      <summary>Правила движения (ПДД РФ) · светофоров по программам редактора: {recording.signals?.filter((s) => s.applied).length ?? 0} из {recording.signals?.length ?? 0}</summary>
      <ul>{recording.rules.map((r) => <li key={r}>{r}</li>)}</ul>
    </details>}
    {recording && <div className="simulation-playback">
      <button className="btn" disabled={!duration} onClick={() => {
        if (!playing && clock.current >= duration) seek(0);
        setPlaying(!playing);
      }}>{playing ? 'Пауза' : 'Играть'}</button>
      <button className="btn" onClick={() => { setPlaying(false); seek(0); }}>С начала</button>
      <label>Скорость <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
        {[0.5, 1, 2, 5, 10].map((n) => <option key={n} value={n}>{n}×</option>)}
      </select></label>
      <input type="range" aria-label="Время симуляции" min="0" max={duration} step="any" value={time}
        disabled={!duration} onChange={(e) => seek(Number(e.target.value))} />
      <span className="mono">{Math.floor(time)} / {Math.ceil(duration)} с</span>
      <button className="btn" onClick={() => {
        const car = sample?.(clock.current)[0] ?? recording.frames.find((frame) => frame.vehicles.length)?.vehicles[0];
        if (car) map.current?.flyTo(car[1], car[2], 17);
      }}>К машинам</button>
    </div>}
    {recording?.report && <SimulationSummary docId={docId} report={recording.report} />}
    </div>
   </div>
  </section>;
}

const FORMATS: [SimulationReportFormat, string][] = [
  ['xlsx', 'Excel'], ['pdf', 'PDF'], ['docx', 'Word'], ['csv', 'CSV'], ['json', 'JSON'],
];

const num = (v: number | null, digits = 0) => v === null ? '—'
  : v.toLocaleString('ru-RU', { maximumFractionDigits: digits });
const pct = (v: number) => `${Math.round(v * 100)} %`;

/** Key metrics of the last run and the report export in every format. */
export function SimulationSummary({ docId, report }: { docId: string; report: SimulationReport }) {
  const [error, setError] = useState<string | null>(null);
  const s = report.summary;
  const tiles: [string, string][] = [
    ['Интенсивность ΣN', `${num(s.intensity_total)} авт/ч`],
    ['Пропущено ΣQ', `${num(s.throughput_total)} авт/ч`],
    ['Загрузка Z, средн. / макс.', `${num(s.avg_load, 2)} / ${num(s.max_load, 2)}`],
    ['Средняя скорость', `${num(s.avg_speed_kmh, 1)} км/ч`],
    ['Машин в сети', `${num(s.avg_on_network, 1)} (макс. ${s.max_on_network})`],
    ['Завершённых поездок', `${s.completed} из ${s.vehicles} (${pct(s.completed_share)})`],
    ['Время поездки', s.avg_trip_s === null ? '—' : `${num(s.avg_trip_s, 1)} с`],
    ['Время ожидания', `${num(s.avg_delay_s, 1)} с на машину`],
    ['Время простоя', `${num(s.avg_stopped_s, 1)} с (${pct(s.stopped_share)})`],
    ['Макс. очередь', `${s.max_queue} авт`],
    ['Выбросы CO₂', `${num(s.co2_kg, 2)} кг`],
  ];
  const kinds = report.kinds ?? [];
  // the report can be exported for some vehicle kinds only; all are chosen at first
  const [only, setOnly] = useState<string[]>([]);
  const chosen = only.length ? only : kinds.map((k) => k.kind);
  const save = (kind: SimulationReportFormat) => {
    setError(null);
    const subset = chosen.length && chosen.length < kinds.length ? chosen : [];
    downloadFile(api.simulationReportUrl(docId, kind, subset), `simulation_report${subset.length ? `_${subset.join('_')}` : ''}.${kind}`)
      .catch((e: Error) => setError(e.message));
  };
  return <div className="simulation-report" aria-label="Отчёт о симуляции">
    <h3>Отчёт о симуляции · {s.engine}, {num(s.duration_s)} с</h3>
    <dl>{tiles.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    {report.roads.length > 0 && <table>
      <thead><tr><th>Дорога</th><th title="Интенсивность">N, авт/ч</th><th title="Пропускная способность">P, авт/ч</th>
        <th title="Коэффициент загрузки N / P">Z</th><th>LOS</th></tr></thead>
      <tbody>{report.roads.slice(0, 5).map((r) => <tr key={r.id} className={r.load >= 0.9 ? 'bad' : undefined}>
        <td>{r.name || `#${r.id}`}</td><td>{num(r.intensity)}</td><td>{num(r.capacity)}</td>
        <td>{num(r.load, 2)}</td><td>{r.los}</td></tr>)}</tbody>
    </table>}
    {kinds.length > 0 && <table className="sim-kinds">
      <thead><tr><th>Вид</th><th>Машин</th><th title="Средняя скорость">V, км/ч</th><th title="Время ожидания на машину">Ожид., с</th>
        <th title="Ожидание у светофоров на машину">У светоф., с</th><th>CO₂, кг</th></tr></thead>
      <tbody>{kinds.map((k) => <tr key={k.kind}><td>{k.label}</td><td>{k.vehicles}</td><td>{num(k.speed_kmh, 1)}</td>
        <td>{num(k.delay_s, 1)}</td><td>{num(k.signal_wait_s, 1)}</td><td>{num(k.co2_kg, 2)}</td></tr>)}</tbody>
    </table>}
    {(report.signals?.length ?? 0) > 0 && <details className="sim-signals">
      <summary>Светофоры по фазам ({report.signals!.length})</summary>
      {report.signals!.map((sg) => <table key={sg.id}>
        <caption>{sg.name} · цикл {sg.cycle_s} с{sg.custom ? ' · программа задана' : ''}</caption>
        <thead><tr><th>Фаза</th><th>Зел., с</th>{kinds.map((k) => <th key={k.kind} title={`${k.label}: проехало / ожидание, с`}>{k.label}</th>)}</tr></thead>
        <tbody>{sg.phases.map((p) => <tr key={p.name}><td title={p.moves}>{p.name}</td><td>{p.green_s}</td>
          {kinds.map((k) => <td key={k.kind}>{p.passed[k.kind] ?? 0} / {num(p.waited_s[k.kind] ?? 0)}</td>)}</tr>)}</tbody>
      </table>)}
      <p className="muted small">В ячейке: проехало машин в фазу / суммарное ожидание у светофора, с. Фаза — зелёный, жёлтый 3 с и все красные 2 с.</p>
    </details>}
    {kinds.length > 1 && <div className="sim-report-kinds" role="group" aria-label="Виды транспорта в отчёте">
      <span className="muted small">В отчёт:</span>
      {kinds.map((k) => <label key={k.kind} className="small"><input type="checkbox" checked={chosen.includes(k.kind)}
        onChange={() => setOnly(chosen.includes(k.kind) ? chosen.filter((x) => x !== k.kind) : [...chosen, k.kind])} />{k.label}</label>)}
    </div>}
    <div className="simulation-export">
      <span className="muted small">Скачать отчёт{chosen.length && chosen.length < kinds.length ? ` (${kinds.filter((k) => chosen.includes(k.kind)).map((k) => k.label).join(', ')})` : ''}:</span>
      {FORMATS.map(([kind, label]) => <button key={kind} className="btn" onClick={() => save(kind)}>{label}</button>)}
    </div>
    {error && <p className="bad" role="alert">{error}</p>}
  </div>;
}
