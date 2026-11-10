import { useEffect, useRef, useState } from 'react';
import type { FeatureCollection } from 'geojson';
import { api } from '../api';
import type { Fix, Geo, Issue, Selection } from '../types';
import { analysisOverlay, cycleStop, formatLength, formatTime, type AnalysisResult } from '../utils/analysis';

type Point = { lat: number; lon: number; node_id?:number } | { node_id: number } | { way_id: number };
export default function AnalysisTools({
  docId,
  rev,
  geo,
  selection,
  pickPoint,
  onPick,
  onOverlay,
  onFix,
  onShowRoad,
}: {
  docId: string;
  rev: number;
  geo: Geo;
  selection: Selection;
  pickPoint: (cb: (p: Point) => void) => void;
  onPick: () => void;
  onOverlay: (f: FeatureCollection) => void;
  onFix: (issue: Issue, fix: Fix, probe?: Record<string, unknown>) => void;
  onShowRoad: (id: number) => void;
}) {
  const [tab, setTab] = useState<'route' | 'isochrone' | 'catchment' | 'pt-isochrone'>('route');
  const [ptSpeed, setPtSpeed] = useState(20),
    [wait, setWait] = useState(0.5),
    [transfers, setTransfers] = useState(2),
    [transferView, setTransferView] = useState(true);
  const [ptRoutes, setPtRoutes] = useState<
    { relation_id: number; ref: string; headway_min: number; headway_source: string }[]
  >([]);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  useEffect(() => {
    let alive = true;
    fetch(`/api/documents/${docId}/analysis/pt-params`)
      .then((r) => r.json())
      .then((r) => {
        if (alive) setPtRoutes(r.routes);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [docId, rev]);
  const [a, setA] = useState<Point | null>(null),
    [b, setB] = useState<Point | null>(null);
  const [mode, setMode] = useState('car'),
    [metric, setMetric] = useState('time');
  const [compare, setCompare] = useState(false),
    [closures, setClosures] = useState(false);
  const [step, setStep] = useState(1),
    [count, setCount] = useState(5),
    [speed, setSpeed] = useState(4.5);
  const [loadSpeed, setLoadSpeed] = useState(false);
  const [sets, setSets] = useState<[number[], number[]]>([[], []]);
  const [limit, setLimit] = useState(500),
    [units, setUnits] = useState('m');
  const [result, setResult] = useState<AnalysisResult | null>(null),
    [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  useEffect(() => {
    sequence.current++;
    setBusy(false);
    setResult(null);
    onOverlay({ type: 'FeatureCollection', features: [] });
  }, [docId, rev, onOverlay]);
  const body = {
    from: a,
    to: b,
    mode,
    metric,
    use_closures: closures,
    walk_kmh: speed,
    compare: compare ? 'original' : undefined,
    step_min: step,
    count,
    speed_source: loadSpeed ? 'load' : 'free',
    how: tab === 'pt-isochrone' ? 'hull' : 'buffer',
    bands_min: [step, step * 2, step * 3],
    pt_kmh: { bus: ptSpeed, tram: ptSpeed, trolleybus: ptSpeed, minibus: ptSpeed, share_taxi: ptSpeed },
    wait_factor: wait,
    max_transfers: transfers,
    headway_overrides: overrides,
    set_a: sets[0],
    set_b: sets[1],
    buffer_m: tab === 'catchment' ? 40 : 30,
    limit_m: units === 'm' ? limit : 500,
    limit_min: units === 'min' ? limit : undefined,
  };
  const run = async () => {
    const token = ++sequence.current;
    setBusy(true);
    setError('');
    try {
      const r = await api.analysis(docId, tab, body);
      if (token === sequence.current) {
        setResult(r);
        const overlay = analysisOverlay(r);
        if (r.buildings)
          overlay.features.push(
            ...geo.areas.features
              .filter((f) => r.buildings!.none_ids.includes(f.properties.id))
              .map((f) => ({ ...f, properties: { set: 'uncovered-building' } })),
          );
        onOverlay(overlay);
      }
    } catch (e) {
      if (token === sequence.current) setError((e as Error).message);
    } finally {
      if (token === sequence.current) setBusy(false);
    }
  };
  const selectedPoint = selection
    ? selection.kind === 'node'
      ? { node_id: selection.id }
      : { way_id: selection.id }
    : null;
  return (
    <section className="card stack10" aria-label="Анализ доступности">
      <div className="tabs">
        {(
          [
            ['route', 'Маршрут'],
            ['isochrone', 'Изохроны'],
            ['catchment', 'Доступность ОП'],
            ['pt-isochrone', 'Доступность на ОТ'],
          ] as const
        ).map(([v, t]) => (
          <button
            key={v}
            aria-pressed={tab === v}
            onClick={() => {
              setTab(v);
              setStep(v === 'pt-isochrone' ? 5 : 1);
              setResult(null);
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {tab !== 'catchment' ? (
        <>
          <div className="row gap8">
            <button
              className="btn"
              onClick={() => {
                onPick();
                pickPoint(setA);
              }}
            >
              Точка А {a ? '✓' : ''}
            </button>
            {tab === 'route' && (
              <button
                className="btn"
                onClick={() => {
                  onPick();
                  pickPoint(setB);
                }}
              >
                Точка Б {b ? '✓' : ''}
              </button>
            )}
          </div>
          {selectedPoint && (
            <button className="btn small" onClick={() => setA(selectedPoint)}>
              Начать от выбранного объекта
            </button>
          )}
          <label>
            Режим{' '}
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="car">Авто</option>
              <option value="walk">Пешком</option>
            </select>
          </label>
          {tab === 'route' ? (
            <label>
              Критерий{' '}
              <select value={metric} onChange={(e) => setMetric(e.target.value)}>
                <option value="time">Время</option>
                <option value="length">Длина</option>
              </select>
            </label>
          ) : (
            <>
              <label>
                Шаг, мин{' '}
                <select value={step} onChange={(e) => setStep(+e.target.value)}>
                  {[0.5, 1, 2, 5].map((n) => (
                    <option key={n}>{n}</option>
                  ))}
                </select>
              </label>
              <label>
                Количество{' '}
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={count}
                  onChange={(e) => setCount(+e.target.value)}
                />
              </label>
              <label>
                <input type="checkbox" checked={loadSpeed} onChange={(e) => setLoadSpeed(e.target.checked)} />
                С учётом загрузки (BPR — допущение)
              </label>
            </>
          )}
          <label>
            <input type="checkbox" checked={closures} onChange={(e) => setClosures(e.target.checked)} />
            Учитывать перекрытия
          </label>
        </>
      ) : (
        <>
          <p className="small muted">Остановки: клик в списке → выезд A → возврат B → убрать.</p>
          <div style={{ maxHeight: 180, overflow: 'auto' }}>
            {geo.nodes.features
              .filter(
                (f) =>
                  f.properties.category === 'stop' && f.properties.tags.public_transport !== 'stop_position',
              )
              .map((f) => (
                <button
                  className="btn small"
                  key={f.properties.id}
                  onClick={() => setSets(cycleStop(...sets, f.properties.id))}
                >
                  {sets[0].includes(f.properties.id) ? 'A' : sets[1].includes(f.properties.id) ? 'B' : '—'}{' '}
                  {f.properties.tags.name || f.properties.id}
                </button>
              ))}
          </div>
          <label>
            Лимит <input type="number" min={1} value={limit} onChange={(e) => setLimit(+e.target.value)} />
            <select
              value={units}
              onChange={(e) => {
                setUnits(e.target.value);
                setLimit(e.target.value === 'm' ? 500 : 6.67);
              }}
            >
              <option value="m">м</option>
              <option value="min">мин</option>
            </select>
          </label>
          {units === 'm' && (
            <div className="chips">
              {[300, 400, 500, 600].map((n) => (
                <button key={n} onClick={() => setLimit(n)}>
                  {n} м
                </button>
              ))}
            </div>
          )}
          <p className="small muted">
            500 м — СП 42.13330.2016 п.11.24; применимость климатических поправок требует проверки.
          </p>
        </>
      )}
      {tab === 'pt-isochrone' && (
        <div className="card stack10">
          <b>Допущения ОТ</b>
          <label>
            Скорость ОТ, км/ч
            <input
              type="number"
              min={1}
              max={100}
              value={ptSpeed}
              onChange={(e) => setPtSpeed(+e.target.value)}
            />
          </label>
          <label>
            Ожидание, доля интервала
            <input
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={wait}
              onChange={(e) => setWait(+e.target.value)}
            />
          </label>
          <label>
            Пересадок не более
            <input
              type="number"
              min={0}
              max={3}
              value={transfers}
              onChange={(e) => setTransfers(+e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={transferView}
              onChange={(e) => {
                setTransferView(e.target.checked);
                if (result)
                  onOverlay(
                    analysisOverlay({
                      ...result,
                      bands: result.bands?.map((b) => ({
                        ...b,
                        geometry: (e.target.checked ? b.with_transfer : b.no_transfer)?.geometry,
                      })),
                    }),
                  );
              }}
            />
            С пересадками
          </label>
          <p className="small muted">
            Интервалы без тега OSM interval — условные (mock), скорость ОТ и ожидание — допущения.
          </p>
          {ptRoutes.map((r) => (
            <label key={r.relation_id}>
              {r.ref || r.relation_id} · {r.headway_source}
              <input
                type="number"
                min={0.1}
                max={180}
                step={0.1}
                value={overrides[r.relation_id] ?? r.headway_min}
                onChange={(e) => setOverrides({ ...overrides, [r.relation_id]: +e.target.value })}
              />
              мин
            </label>
          ))}
        </div>
      )}
      {(mode === 'walk' || tab === 'catchment' || tab === 'pt-isochrone') && (
        <label>
          Пешком, км/ч (допущение)
          <input
            type="number"
            min={0.5}
            max={15}
            step={0.1}
            value={speed}
            onChange={(e) => setSpeed(+e.target.value)}
          />
        </label>
      )}
      <label>
        <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
        Сравнить с исходным файлом
      </label>
      <button
        className="btn primary"
        disabled={busy || (tab !== 'catchment' && (!a || (tab === 'route' && !b)))}
        onClick={run}
      >
        {busy ? 'Расчёт…' : 'Рассчитать'}
      </button>
      {error && <p className="bad">{error}</p>}
      {result && (
        <>
          <span className="muted small">Только чтение · ревизия {result.revision}</span>
          {result.stats && (
            <>
              <b>
                {formatLength(result.stats.length_m)} · {formatTime(result.stats.time_s)}
              </b>
              <span>
                {result.stats.avg_kmh.toFixed(1)} км/ч · {result.stats.edges} сегментов
              </span>
              {result.stats.ways.map((w) => (
                <button key={w.id} className="link" onClick={() => onShowRoad(w.id)}>
                  {w.name || w.id} · {formatLength(w.length_m)}
                </button>
              ))}
              <span className="small">
                Запретов применено {result.stats.restrictions_applied}; пропущено:{' '}
                {result.stats.restrictions_skipped.join(', ') || 'нет'}
              </span>
            </>
          )}
          {result.bands?.map((b) => (
            <p key={b.minutes}>
              {b.minutes} мин ·{' '}
              {(b.area_km2 ?? (transferView ? b.with_transfer : b.no_transfer)?.area_km2 ?? 0).toFixed(3)} км²
              ·{' '}
              {b.with_transfer
                ? `${b.no_transfer?.stops_reached} / ${b.with_transfer.stops_reached} остановок без/с пересадкой`
                : `${b.road_km.toFixed(2)} км дорог (кольцо ${b.ring_road_km.toFixed(2)})`}
            </p>
          ))}
          {result.boundary_limited && <p className="warn">У границы выгрузки — зона может быть занижена.</p>}
          {result.buildings && (
            <p>
              Здания: обе стороны {result.buildings.both}; одна {result.buildings.one_side}; нет доступа{' '}
              {result.buildings.none}. Население: нет данных (ИД5). Высоты не учтены.
            </p>
          )}
          {result.compare && (
            <p>
              Исходный файл:{' '}
              {result.compare.before.found === undefined
                ? JSON.stringify(
                    result.compare.before.buildings ??
                      result.compare.before.bands?.map((b) => ({ min: b.minutes, km: b.road_km })),
                  )
                : result.compare.before.found
                  ? formatTime(result.compare.before.stats!.time_s)
                  : 'нет пути'}
            </p>
          )}
          {result.issues.map((i) => (
            <div className="card" key={i.id}>
              <b>{i.title}</b>
              <p className="small">{i.description}</p>
              {i.fixes.map((f) => (
                <button
                  className="btn small"
                  key={f.label}
                  onClick={() => onFix(i, f, tab === 'route' ? { type: 'route', ...body } : undefined)}
                >
                  {f.label} · предпросмотр
                </button>
              ))}
            </div>
          ))}
          {result.assumptions.map((a) => (
            <p className="small muted" key={a.key}>
              {a.label}
            </p>
          ))}
          {result.sources.map((s) => (
            <a key={s.label} href={s.url} target="_blank" rel="noreferrer">
              {s.label}
            </a>
          ))}
        </>
      )}
    </section>
  );
}
