import { useEffect, useState } from 'react';
import { api } from '../api';
import type { RouteDetail, StopInfo } from '../types';
import { fmt } from '../utils/geom';

const MODE_COLOR: Record<string, string> = { bus: '#2563EB', trolleybus: '#0891B2', tram: '#DC2626', minibus: '#CA8A04', share_taxi: '#CA8A04' };

export const routeColour = (r: { colour: string; mode: string }) => r.colour || MODE_COLOR[r.mode] || '#2563EB';

// -------------------------------------------------------------- stops
export function StopPanel({ docId, nodeId, rev, openRoute, onOpenRoute, onChanged }: {
  docId: string;
  nodeId: number;
  /** document revision: refetch after edits */
  rev: number;
  openRoute: RouteDetail | null;
  onOpenRoute: (r: RouteDetail | null) => void;
  onChanged: () => void;
}) {
  const [info, setInfo] = useState<StopInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [routeLoading, setRouteLoading] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    setInfo(null);
    api.stop(docId, nodeId).then((d) => { if (alive) { setInfo(d); setError(null); } }).catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [docId, nodeId, rev]);

  const open = async (routeId: number) => {
    if (openRoute?.id === routeId) { onOpenRoute(null); return; }
    setRouteLoading(routeId);
    try {
      onOpenRoute(await api.route(docId, routeId));
      setError(null);
    } catch (e) {
      setError(`Маршрут не загрузился: ${(e as Error).message}`);
    } finally {
      setRouteLoading(null);
    }
  };

  if (error && !info) return <p className="bad small">Данные остановки не загрузились: {error}</p>;
  if (!info) return <p className="muted small">Загрузка данных остановки и маршрутов…</p>;
  return (
    <div className="card stack10">
      <div className="row between"><b>Остановка</b><span className={`load-pill ${info.load_label === 'высокая' ? 'hi' : info.load_label === 'средняя' ? 'mid' : 'lo'}`}>
        загруженность: {info.load_label}</span></div>
      <div className="kpis">
        <div><b>{info.routes.length}</b><span>маршрутов</span></div>
        <div><b>{fmt(info.vehicles_per_hour)}</b><span>авт./ч на остановке</span></div>
        <div><b>{fmt(info.residents_400m)}</b><span>оценка жителей в радиусе 400 м по площади зданий (не ИД5)</span></div>
      </div>
      {info.routes.length === 0 && <span className="muted small">В файле нет маршрутов (relation route) через эту остановку.</span>}
      <div className="route-chips" role="list" aria-label="Маршруты">
        {info.routes.map((r) => (
          <button key={r.id} role="listitem" className="route-chip" aria-pressed={openRoute?.id === r.id}
            style={{ ['--route' as string]: routeColour(r) }} title={`${r.mode_label} ${r.ref}: ${r.from || '…'} → ${r.to || '…'}`}
            onClick={() => open(r.id)}>
            {r.ref}
          </button>
        ))}
      </div>
      {routeLoading !== null && <span className="muted small">Загрузка маршрута…</span>}
      {error && <span className="bad small">{error}</span>}
      {openRoute && <RouteCard docId={docId} route={openRoute} onRoute={onOpenRoute} onChanged={onChanged} />}
      <span className="muted small">Загруженность — оценка: жители в шаговой доступности на одну машину в час.</span>
    </div>
  );
}

function RouteCard({ docId, route, onRoute, onChanged }: {
  docId: string; route: RouteDetail; onRoute: (r: RouteDetail | null) => void; onChanged: () => void;
}) {
  const [freq, setFreq] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const value = freq ?? route.frequency;
  const commit = async (v: number | null) => {
    setFreq(null);
    try {
      onRoute(await api.setRouteFrequency(docId, route.id, v));
      setSaveError(null);
      onChanged();
    } catch (e) {
      // the panel keeps the previous value and says why
      setSaveError(`Интервал не сохранён: ${(e as Error).message}`);
    }
  };
  return (
    <div className="route-card" style={{ ['--route' as string]: routeColour(route) }}>
      <div className="row between">
        <b>{route.mode_label} {route.ref}</b>
        <button className="icon-btn" aria-label="Скрыть маршрут" onClick={() => onRoute(null)}>×</button>
      </div>
      <span className="small">{route.from || '…'} → {route.to || '…'}</span>
      <span className="muted small">{route.stops} остановок, на карте видно {route.lines.length} участков в пределах файла</span>
      <label className="field"><span>Интервал движения: {fmt(value, 1)} авт./ч (каждые {value > 0 ? fmt(60 / value, 0) : '—'} мин){route.custom_frequency ? ' · задано вручную' : ''}</span>
        <input type="range" min={0} max={30} step={1} value={value} aria-label="Машин в час"
          onChange={(e) => setFreq(Number(e.target.value))}
          onPointerUp={() => freq !== null && commit(freq)} onKeyUp={() => freq !== null && commit(freq)} />
      </label>
      {saveError && <span className="bad small">{saveError}</span>}
      {route.custom_frequency && <button className="link small" onClick={() => commit(null)}>вернуть значение из OSM</button>}
      <span className="muted small">Автобусы маршрута добавляют свой поток (×2,5 легковых) к нагрузке дорог.</span>
    </div>
  );
}

// ------------------------------------------------------------ signals
export { SignalPanel } from './SignalPanel';
