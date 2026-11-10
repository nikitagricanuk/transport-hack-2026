import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from 'react';
import { api } from '../api';
import type { SimulationRecording } from '../types';
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
  </section>;
}
