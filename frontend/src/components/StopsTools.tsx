import { useEffect, useState } from 'react';
import type { FeatureCollection } from 'geojson';
import { api } from '../api';
import type { StopAdvice, StopsAnalysis } from '../types';
import { fmt } from '../utils/geom';

const LEVEL_CLASS: Record<string, string> = { 'низкая': 'lo', 'средняя': 'mid', 'высокая': 'hi', 'нет маршрутов': '' };

/** Stops: which housing has no stop nearby, which stops are crowded, and what to change. */
export function StopsTools({ docId, rev, onOverlay, onShowRoad, onShowNode, onFlyTo }: {
  docId: string;
  rev: number;
  onOverlay: (data: FeatureCollection) => void;
  onShowRoad: (id: number) => void;
  onShowNode?: (id: number) => void;
  onFlyTo?: (lon: number, lat: number) => void;
}) {
  const [radius, setRadius] = useState(500);
  const [trips, setTrips] = useState(1);
  const [peak, setPeak] = useState(0.1);
  const [data, setData] = useState<StopsAnalysis | null>(null);
  const [error, setError] = useState('');
  const [limit, setLimit] = useState(10);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      api.stopsAnalysis(docId, { radius_m: radius, trips_per_resident: trips, peak_share: peak })
        .then((d) => { if (alive) { setData(d); setError(''); } })
        .catch((e: Error) => alive && setError(e.message));
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [docId, rev, radius, trips, peak]);
  useEffect(() => { onOverlay(data?.features ?? { type: 'FeatureCollection', features: [] }); }, [data, onOverlay]);
  useEffect(() => () => onOverlay({ type: 'FeatureCollection', features: [] }), [onOverlay]);

  const show = (a: StopAdvice) => {
    if (a.node_id !== undefined && onShowNode) onShowNode(a.node_id);
    else if (a.coord && onFlyTo) onFlyTo(a.coord[0], a.coord[1]);
    else if (a.way_id) onShowRoad(a.way_id);
  };
  const s = data?.summary;
  return (
    <div className="stack10 stops-tools">
      <p className="muted small">Покрытие жилья остановками, нехватка остановок по домам и районам, загруженность остановок и советы. Анализ не меняет граф.</p>
      <label>Радиус доступности, м <input type="number" min={100} max={1500} step={50} value={radius} onChange={(e) => setRadius(Number(e.target.value) || 500)} /></label>
      <div className="row gap8">{[300, 400, 500].map((n) => <button key={n} className="btn small" aria-pressed={radius === n} onClick={() => setRadius(n)}>{n} м</button>)}</div>
      <label>Поездок на ОТ на жителя в сутки — допущение <input type="number" min={0} max={5} step={0.1} value={trips} onChange={(e) => setTrips(Number(e.target.value))} /></label>
      <label>Доля часа пик — допущение <input type="number" min={0.01} max={0.5} step={0.01} value={peak} onChange={(e) => setPeak(Number(e.target.value) || 0.1)} /></label>
      {error && <p role="alert" className="bad">{error}</p>}
      {!data && !error && <p role="status">Расчёт…</p>}
      {data && s && <>
        <div className="kpis">
          <div><b>{s.stops}</b><span>остановок ({s.stop_nodes} точек OSM)</span></div>
          <div><b>{s.coverage === null ? '—' : `${fmt(s.coverage * 100, 1)} %`}</b><span>жителей у остановки</span></div>
          <div><b>{fmt(s.uncovered)}</b><span>жителей дальше {fmt(radius)} м</span></div>
          <div><b>{s.uncovered_buildings}</b><span>жилых зданий без остановки</span></div>
        </div>
        {s.buildings === 0 && <p className="warning small">В файле нет жилых зданий (building=apartments/residential/house…) — покрытие и спрос оценить нельзя.</p>}
        {s.routes === 0 && s.stops > 0 && <p className="warning small">В файле нет маршрутов (relation route) — загруженность остановок не рассчитать.</p>}
        <div className="row gap8 small legend-row">
          <span className="load-pill lo">низкая {s.by_level['низкая'] ?? 0}</span>
          <span className="load-pill mid">средняя {s.by_level['средняя'] ?? 0}</span>
          <span className="load-pill hi">высокая {s.by_level['высокая'] ?? 0}</span>
          <span className="load-pill">без маршрутов {s.by_level['нет маршрутов'] ?? 0}</span>
        </div>
        <p className="small muted">На карте: точки — остановки по загруженности; штриховка — жилые здания без остановки в радиусе; фиолетовые метки — зоны нехватки.</p>

        <section className="stack6">
          <b>Советы ({data.advice.length})</b>
          {data.advice.length === 0 && <span className="small muted">Замечаний нет.</span>}
          {data.advice.map((a, i) => (
            <div key={i} className={`card stop-advice ${a.severity}`}>
              <b>{a.title}</b>
              <p className="small">{a.text}</p>
              {(a.node_id !== undefined || a.coord || a.way_id) && <div><button className="btn small" onClick={() => show(a)}>Показать</button></div>}
            </div>
          ))}
        </section>

        <section className="stack6">
          <b>Загруженность остановок</b>
          <table className="cmp small">
            <thead><tr><th>Остановка</th><th title="Оценка жителей, для которых остановка ближайшая">Жит.</th><th title="Спрос в час пик, пасс./ч">Спрос</th><th title="Провозная способность, мест/ч">Мест/ч</th><th>Загр.</th></tr></thead>
            <tbody>{data.stops.slice(0, limit).map((st) => (
              <tr key={st.id}>
                <td><button className="link" onClick={() => onShowNode?.(st.id)} title={st.routes.length ? `Маршруты: ${st.routes.join(', ')}` : 'Нет маршрутов в файле'}>{st.name || `#${st.id}`}</button></td>
                <td>{fmt(st.residents)}</td><td>{fmt(st.demand_per_hour)}</td><td>{fmt(st.capacity_per_hour)}</td>
                <td><span className={`load-pill ${LEVEL_CLASS[st.level]}`}>{st.level === 'нет маршрутов' ? '—' : `${fmt(st.load * 100, 0)} %`}</span></td>
              </tr>))}
            </tbody>
          </table>
          {data.stops.length > limit && <button className="link small" onClick={() => setLimit((n) => n + 20)}>Показать ещё</button>}
        </section>

        {data.gaps.length > 0 && <section className="stack6">
          <b>Где не хватает остановок</b>
          {data.gaps.slice(0, 8).map((g, i) => (
            <button key={i} className="link worst" onClick={() => onFlyTo?.(g.coord[0], g.coord[1])}>
              <span className="grow ellipsis">{g.road_name || 'без названия'} · {g.buildings} зд.</span>
              <span className="mono small">≈{fmt(g.residents)} жит.{g.nearest_stop_m !== null ? ` · ${fmt(g.nearest_stop_m)} м` : ''}</span>
            </button>))}
        </section>}

        {data.districts.length > 0 && <section className="stack6">
          <b>Районы и жилые территории</b>
          <table className="cmp small">
            <thead><tr><th>Территория</th><th>Жит.</th><th>Покрыто</th></tr></thead>
            <tbody>{data.districts.slice(0, 10).map((d) => (
              <tr key={d.id}><td><button className="link" onClick={() => onShowRoad(d.id)}>{d.name}</button></td><td>{fmt(d.residents)}</td>
                <td className={d.coverage < 0.9 ? 'bad' : undefined}>{fmt(d.coverage * 100, 0)} %</td></tr>))}
            </tbody>
          </table>
        </section>}

        <details className="small"><summary>Допущения и источники</summary>
          {data.assumptions.map((a) => <p key={a}>{a}</p>)}
          {data.sources.map((src) => <p key={src.label}>{src.url ? <a href={src.url} target="_blank" rel="noreferrer">{src.label}</a> : src.label}</p>)}
        </details>
      </>}
    </div>
  );
}
