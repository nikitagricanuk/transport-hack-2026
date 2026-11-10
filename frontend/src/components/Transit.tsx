import { levelOfService } from '../utils/los';
import ResizableTable from './ResizableTable';
import { useEffect, useState } from 'react';
import { api } from '../api';
import type { RouteDetail, SignalPlan, StopInfo } from '../types';
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
export function SignalPanel({ docId, nodeId, rev, onChanged }: {
  docId: string; nodeId: number; rev: number; onChanged: () => void;
}) {
  const [plan, setPlan] = useState<SignalPlan | null>(null);
  const [draft, setDraft] = useState<{ cycle?: number; main_share?: number }>({});
  const [error, setError] = useState<string | null>(null);
  // the cycle runs: a clock that moves the marker over the phase bar
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 250); return () => window.clearInterval(t); }, []);
  useEffect(() => {
    let alive = true;
    api.signal(docId, nodeId).then((p) => { if (alive) { setPlan(p); setError(null); } }).catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [docId, nodeId, rev]);

  const send = async (body: { green_main_s?: number; green_cross_s?: number; cycle?: number; main_share?: number; optimize?: boolean; reset?: boolean }) => {
    setDraft({});
    try {
      setPlan(await api.setSignal(docId, nodeId, body));
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error) return <p className="bad small">{error}</p>;
  if (!plan) return <p className="muted small">План светофора…</p>;
  const cycle = draft.cycle ?? plan.cycle;
  const share = draft.main_share ?? plan.main_share;
  // per phase: green + yellow 3 s (ГОСТ Р 52289-2019) + all-red 2 s; both phases add up to the cycle
  const yellow = plan.phases[0]?.yellow_s ?? 3;
  const allRed = plan.phases[0]?.all_red_s ?? 2;
  const green = cycle - 2 * (yellow + allRed);
  const gMain = Math.round(green * share);
  const gCross = green - gMain;
  const byGroup = (g: 'main' | 'cross') => plan.approaches.filter((a) => a.group === g);
  // where we are in the cycle: main green → yellow → all-red → cross green → yellow → all-red
  const tc = (now / 1000) % cycle;
  const bounds = [gMain, gMain + yellow, gMain + yellow + allRed, gMain + yellow + allRed + gCross,
    gMain + 2 * yellow + allRed + gCross, cycle];
  const phaseIdx = bounds.findIndex((b) => tc < b);
  const left = Math.max(0, Math.round(bounds[phaseIdx] - tc));
  const LIGHT = ['зелёный', 'жёлтый', 'красный', 'красный', 'красный', 'красный'];
  const LIGHT_X = ['красный', 'красный', 'красный', 'зелёный', 'жёлтый', 'красный'];
  const lightClass = (t: string) => (t === 'зелёный' ? 'good' : t === 'жёлтый' ? 'warn-text' : 'bad');
  return (
    <div className="card stack10">
      <div className="row between"><b>Фазы светофора</b><span className="muted small">{plan.custom ? 'задано вручную' : 'по умолчанию'}</span></div>
      <div className="phase-bar live" aria-label={`Цикл ${cycle} с`}>
        <span className="phase-now" style={{ left: `${(tc / cycle) * 100}%` }} aria-hidden="true" />
        <span className="ph main" style={{ flex: gMain }}>{gMain} с</span>
        <span className="ph yellow" style={{ flex: yellow }} title={`Жёлтый ${yellow} с`} />
        <span className="ph allred" style={{ flex: allRed }} title={`Все красные ${allRed} с`} />
        <span className="ph cross" style={{ flex: gCross }}>{gCross} с</span>
        <span className="ph yellow" style={{ flex: yellow }} title={`Жёлтый ${yellow} с`} />
        <span className="ph allred" style={{ flex: allRed }} title={`Все красные ${allRed} с`} />
      </div>
      <div className="row between small" aria-live="off">
        <span>Главное: <b className={lightClass(LIGHT[phaseIdx])}>{LIGHT[phaseIdx]}</b></span>
        <span>Пересекающее: <b className={lightClass(LIGHT_X[phaseIdx])}>{LIGHT_X[phaseIdx]}</b></span>
        <span className="mono muted">ещё {left} с</span>
      </div>
      <p className="small">
        Пропускная способность подходов зависит от доли зелёного: c = n · s · g/C. Главное g/C = {gMain}/{cycle} ={' '}
        {fmt(gMain / cycle, 2)}, пересекающее {gCross}/{cycle} = {fmt(gCross / cycle, 2)}.
      </p>
      <p className="small muted">
        {gMain} + {yellow} + {allRed} + {gCross} + {yellow} + {allRed} = {cycle} с. Это планировочная оценка:
        SUMO строит программы светофоров сам, тайминг сохраняется в тегах uds:signal:*.
      </p>
      <label className="field">Зелёный главного направления, с
        <input type="number" min={1} max={170} value={gMain} onChange={(e) => setDraft({ cycle: +e.target.value + gCross + 10, main_share: +e.target.value / (+e.target.value + gCross) })} onBlur={() => send({green_main_s:gMain,green_cross_s:gCross})} /></label>
      <label className="field">Зелёный пересекающего направления, с
        <input type="number" min={1} max={170} value={gCross} onChange={(e) => setDraft({ cycle: +e.target.value + gMain + 10, main_share: gMain / (+e.target.value + gMain) })} onBlur={() => send({green_main_s:gMain,green_cross_s:gCross})} /></label>
      <p className="small muted">Жёлтый 3 с; все красные 2 с на фазу. z = Nфакт / P; LOS — ОДМ 218.2.020-2012, таблица 1.</p>
      <div className="row between small">
        <span className="muted">Вебстер: цикл {plan.webster.cycle} с, главному {Math.round(plan.webster.main_share * 100)} %{plan.webster.saturated ? ' (перегрузка)' : ''}</span>
        <button className="btn small primary" onClick={() => send({ optimize: true })}>Подобрать</button>
      </div>
      <ResizableTable id="transit-1" className="cmp">
        <thead><tr><th>Подход регулируемой улицы</th><th>z = v/c</th></tr></thead>
        <tbody>
          {(['main', 'cross'] as const).flatMap((g) => byGroup(g).map((a, i) => (
            <tr key={`${g}${a.way_id}${i}`}>
              <td className="ellipsis">{a.name || a.highway} <span className="muted small">{a.bearing}°</span></td>
              <td className="mono">{a.vc !== null ? `${fmt(a.vc, 2)} (${levelOfService(a.vc)})` : '—'}</td>
            </tr>
          )))}
        </tbody>
      </ResizableTable>
      {plan.custom && <button className="link small" onClick={() => send({ reset: true })}>сбросить к равному делению</button>}
      <span className="muted small">Фазы влияют на расчёт нагрузки (доля зелёного × пропускная способность подходов). План сохраняется в uds:signal:*; SUMO строит фазы сам.</span>
    </div>
  );
}
