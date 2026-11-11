import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from 'react';
import { api, downloadFile, type SimulationReportFormat } from '../api';
import type { SimulationRecording, SimulationReport } from '../types';
import { makeSampler } from '../utils/simulation';
import type { MapHandle } from './MapView';

type Engine = 'sumo' | 'matsim' | 'webots';

export function Simulation({ docId, map }: { docId: string; map: RefObject<MapHandle> }) {
  const [engine, setEngine] = useState<Engine>('sumo');
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
    <form key={engine} onSubmit={load}>
      <label>Источник
        <select value={engine} onChange={(e) => { reset(); setEngine(e.target.value as Engine); }}>
          <option value="sumo">SUMO · новый прогон</option>
          <option value="matsim">MATSim · запись</option>
          <option value="webots">Webots · запись</option>
        </select>
      </label>
      {engine === 'sumo' && <label>Машин <input name="vehicles" type="number" min="1" max="200" defaultValue="60" required /></label>}
      {engine === 'matsim' && <>
        <label>Сеть <input name="network" type="file" accept=".xml,.gz" required /></label>
        <label>События <input name="events" type="file" accept=".xml,.gz" required /></label>
        <label>Координаты сети <input name="crs" defaultValue="EPSG:4326" pattern="[Ee][Pp][Ss][Gg]:[0-9]{1,8}" required /></label>
        <label>Начало, с <input name="start" type="number" min="0" max="604800" placeholder="Авто" /></label>
      </>}
      {engine === 'webots' && <label>Траектории <input name="recording" type="file" accept=".csv,.gz" required /></label>}
      <button className="btn primary" type="submit" disabled={busy}>{engine === 'sumo' ? 'Запустить' : 'Загрузить'}</button>
    </form>
    <p className="muted small">
      {engine === 'sumo' ? '90 секунд демонстрационного трафика по текущей дорожной сети.'
        : engine === 'matsim' ? 'Сеть и события одного прогона. На карте отображаются до 120 секунд движения.'
          : 'CSV из контроллера: time,id,lon,lat,angle. Координаты WGS84, угол от севера.'}
    </p>
    {busy && <p role="status">{engine === 'sumo' ? 'SUMO строит сеть и рассчитывает движение…' : 'Читаем траектории…'}</p>}
    {error && <p className="bad" role="alert">{error}</p>}
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
  const save = (kind: SimulationReportFormat) => {
    setError(null);
    downloadFile(api.simulationReportUrl(docId, kind), `simulation_report.${kind}`)
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
    <div className="simulation-export">
      <span className="muted small">Скачать отчёт:</span>
      {FORMATS.map(([kind, label]) => <button key={kind} className="btn" onClick={() => save(kind)}>{label}</button>)}
    </div>
    {error && <p className="bad" role="alert">{error}</p>}
  </div>;
}
