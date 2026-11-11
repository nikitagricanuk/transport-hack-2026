import ResizableTable from './ResizableTable';
import { levelOfService } from '../utils/los';
import { memo, useEffect, useState } from 'react';
import { api } from '../api';
import { useEnterKey } from '../utils/useEnterKey';
import type { ChangesResult, Comparison, LoadRow, LoadState, Los, ReportData, ValidationResult } from '../types';
import { fmt } from '../utils/geom';


/** What v and c mean; shown on hover next to every v/c figure. */
export const VC_TIP = "V — интенсивность движения, прив. авт./ч (сколько машин проходит за час); C — пропускная способность, авт./ч (сколько может пройти). v/c — коэффициент загрузки z: до 0,2 — свободно (A), 0,45 — B, 0,7 — C, 0,9 — D, до 1 — E, больше 1 — затор (F). ОДМ 218.2.020-2012, табл. 1.";
export function AnalyticsAssumptions() {
  return <details className="card small">
    <summary>Допущения и источники</summary>
    <p>Геометрия и теги взяты из загруженного OSM. При отсутствии данных учёта модель оценивает спрос по классу дороги.</p>
    <p>Пропускная способность на полосу, доля спроса и запасные скорости заданы для сценария. Задержки рассчитаны по функции BPR с коэффициентами 0,15 и 4.</p>
    <p>Уровень обслуживания определяется по v/c из таблицы 1 ОДМ 218.2.020-2012.</p>
    <a href="https://meganorm.ru/Data2/1/4293787/4293787897.htm" target="_blank" rel="noreferrer">Текст ОДМ 218.2.020-2012</a>
  </details>;
}

export const LOS_COLOR: Record<Los, string> = {
  A: '#16A34A', B: '#65A30D', C: '#CA8A04', D: '#EA580C', E: '#DC2626', F: '#7F1D1D',
};
const LOS_TEXT: Record<Los, string> = {
  A: 'свободно', B: 'почти свободно', C: 'плотно', D: 'насыщенно', E: 'на пределе', F: 'пробка',
};
const TOTALS_RU: Record<string, [string, number, boolean]> = {
  // label, digits, lower is better
  veh_hours: ['Время в пути, авт·ч', 1, true],
  delay_hours: ['Задержка, авт·ч', 1, true],
  avg_vc: ['Средняя загрузка v/c', 3, true],
  congested_km: ['Перегружено, км', 2, true],
  overloaded: ['Рёбер с v/c ≥ 1', 0, true],
  veh_km: ['Пробег, авт·км/ч', 0, false],
};
const SEV_LEVEL: Record<string, string> = { ok: 'готово', risk: 'риск', fail: 'не пройдёт' };

const digitsOf = (n: number) => (Number.isInteger(n) ? 0 : n < 10 ? 2 : 1);

// ------------------------------------------------------------- bar chart
export function Bars({ items, unit, max }: {
  items: { label: string; value: number; color?: string; value2?: number }[];
  unit?: string;
  max?: number;
}) {
  const top = max ?? Math.max(1, ...items.map((i) => Math.max(i.value, i.value2 ?? 0)));
  return (
    <div className="bars-chart" role="img" aria-label="Диаграмма">
      {items.map((i) => (
        <div key={i.label} className="bar-row">
          <span className="bar-label" title={i.label}>{i.label}</span>
          <span className="bar-track">
            {i.value2 !== undefined && (
              <span className="bar-fill ghost" style={{ width: `${(i.value2 / top) * 100}%` }} />
            )}
            <span className="bar-fill" style={{ width: `${(i.value / top) * 100}%`, background: i.color }} />
          </span>
          <span className="bar-value mono">
            {i.value2 !== undefined ? `${fmt(i.value2, digitsOf(i.value2))} → ` : ''}{fmt(i.value, digitsOf(i.value))}{unit ?? ''}
          </span>
        </div>
      ))}
    </div>
  );
}

function Delta({ before, after, digits, lowerBetter }: { before: number; after: number; digits: number; lowerBetter: boolean }) {
  const d = after - before;
  if (Math.abs(d) < 10 ** -digits / 2) return <span className="muted">—</span>;
  const good = lowerBetter ? d < 0 : d > 0;
  return <span className={good ? 'good' : 'bad'}>{d > 0 ? '+' : '−'}{fmt(Math.abs(d), digits)}</span>;
}

// ------------------------------------------------------------- load panel
export const LoadPanel = memo(function LoadPanel({
  load, comparison, onMultiplier, onSnapshot, onDeleteSnapshot, onCompare, onReset, onShowRoad, colorOn, onColor,
}: {
  load: LoadState | null;
  comparison: Comparison | null;
  onMultiplier: (m: number) => void;
  onSnapshot: (name: string) => void;
  onDeleteSnapshot: (id: string) => void;
  onCompare: (a: string, b: string) => void;
  onReset: () => void;
  onShowRoad: (id: number) => void;
  colorOn: boolean;
  onColor: (on: boolean) => void;
}) {
  const [name, setName] = useState('');
  const [mult, setMult] = useState<number | null>(null);
  const [a, setA] = useState('');
  const [b, setB] = useState('current');
  if (!load) return <p className="muted pad">Расчёт нагрузки…</p>;
  const s = load.summary;
  const m = mult ?? load.multiplier;
  const worst = [...load.rows].sort((x, y) => y.vc - x.vc).slice(0, 8);
  const snapA = a || load.snapshots[load.snapshots.length - 1]?.id || '';

  return (
    <div className="load-panel">
      <section className="load-col">
        <div className="row between">
          <b>Нагрузка сети</b>
          <label className="row gap8 small"><input type="checkbox" checked={colorOn} onChange={(e) => onColor(e.target.checked)} />окрасить дороги</label>
        </div>
        <label className="field"><span>Спрос: ×{fmt(m, 2)} к базовой интенсивности</span>
          <input type="range" min={0.3} max={2} step={0.05} value={m}
            onChange={(e) => setMult(Number(e.target.value))}
            onPointerUp={() => { if (mult !== null) { onMultiplier(mult); setMult(null); } }}
            onKeyUp={() => { if (mult !== null) { onMultiplier(mult); setMult(null); } }} />
        </label>
        <div className="kpis">
          <div title={VC_TIP} className="has-tip"><b>{fmt(s.avg_vc, 2)}</b><span>средняя v/c, загрузка z ⓘ</span></div>
          <div title={VC_TIP} className="has-tip"><b>{fmt(s.congested_km, 2)}</b><span>км с v/c ≥ 0,9 ⓘ</span></div>
          <div><b>{fmt(s.delay_hours, 1)}</b><span>задержка, авт·ч</span></div>
        </div>
        <div className="los-strip" aria-label="Распределение по уровню обслуживания">
          {(Object.keys(s.by_los) as Los[]).map((l) => (
            <span key={l} style={{ flex: Math.max(s.by_los[l], 0.001), background: LOS_COLOR[l] }}
              title={`${l} — ${LOS_TEXT[l]}: ${s.by_los[l]} рёбер`}>{s.by_los[l] ? l : ''}</span>
          ))}
        </div>
        <AnalyticsAssumptions />
        <button className="btn small" onClick={onReset}>Сбросить нагрузку</button>
      </section>

      <section className="load-col">
        <b>Самые загруженные</b>
        {worst.map((r) => (
          <button key={r.id} className="link worst" onClick={() => onShowRoad(r.id)}>
            <span className="los-dot" style={{ background: LOS_COLOR[r.los] }}>{r.los}</span>
            <span className="grow ellipsis">{r.name || `${r.highway} #${r.id}`}</span>
            <span className="mono small">{fmt(r.volume)} / {fmt(r.capacity)} · {fmt(r.vc, 2)}</span>
          </button>
        ))}
      </section>

      <section className="load-col">
        <b>Зафиксировать состояние</b>
        <div className="row gap8">
          <input placeholder="например, «До расширения»" value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { onSnapshot(name); setName(''); } }} aria-label="Название снимка" />
          <button className="btn small" onClick={() => { onSnapshot(name); setName(''); }}>Снимок</button>
        </div>
        <div className="snap-list">
          {load.snapshots.length === 0 && <span className="muted small">Снимков пока нет: зафиксируйте состояние, измените дороги или нагрузку и сравните.</span>}
          {load.snapshots.map((sn) => (
            <div key={sn.id} className="snap">
              <span className="grow ellipsis" title={sn.created}><b>{sn.name}</b> <span className="muted small">{sn.created.slice(11, 16)} · v/c {fmt(sn.summary.avg_vc, 2)}</span></span>
              <button className="icon-btn" aria-label={`Удалить снимок ${sn.name}`} onClick={() => onDeleteSnapshot(sn.id)}>×</button>
            </div>
          ))}
        </div>
        {load.snapshots.length > 0 && (
          <div className="row gap8">
            <select value={snapA} onChange={(e) => setA(e.target.value)} aria-label="Было">
              {load.snapshots.map((sn) => <option key={sn.id} value={sn.id}>{sn.name}</option>)}
            </select>
            <span>→</span>
            <select value={b} onChange={(e) => setB(e.target.value)} aria-label="Стало">
              <option value="current">Текущее</option>
              {load.snapshots.map((sn) => <option key={sn.id} value={sn.id}>{sn.name}</option>)}
            </select>
            <button className="btn small primary" onClick={() => onCompare(snapA, b)}>Сравнить</button>
          </div>
        )}
      </section>

      {comparison && <ComparisonView c={comparison} onShowRoad={onShowRoad} />}
    </div>
  );
});

function ComparisonView({ c, onShowRoad }: { c: Comparison; onShowRoad: (id: number) => void }) {
  return (
    <section className="load-col wide">
      <b>«{c.a.name}» → «{c.b.name}»</b>
      <ResizableTable id="analysis-1" className="cmp">
        <thead><tr><th>Показатель</th><th>Было</th><th>Стало</th><th>Δ</th></tr></thead>
        <tbody>
          {Object.entries(TOTALS_RU).map(([k, [label, digits, lower]]) => c.totals[k] && (
            <tr key={k}>
              <td>{label}</td>
              <td className="mono">{fmt(c.totals[k].before, digits)}</td>
              <td className="mono">{fmt(c.totals[k].after, digits)}</td>
              <td className="mono"><Delta before={c.totals[k].before} after={c.totals[k].after} digits={digits} lowerBetter={lower} /></td>
            </tr>
          ))}
        </tbody>
      </ResizableTable>
      <span className="small muted">Изменённые рёбра: {c.changed.length}. Загрузка v/c было → стало:</span>
      <Bars max={Math.max(1.2, ...c.changed.slice(0, 8).map((x) => Math.max(x.vc_before ?? 0, x.vc_after ?? 0)))}
        items={c.changed.slice(0, 8).map((x) => ({
          label: x.name || `#${x.id}`, value: x.vc_after ?? 0, value2: x.vc_before ?? 0,
          color: x.los_after ? LOS_COLOR[x.los_after] : undefined,
        }))} />
      <div className="chips-row">
        {c.changed.slice(0, 8).map((x) => (
          <button key={x.id} className="btn small" onClick={() => onShowRoad(x.id)}>
            {x.name || `#${x.id}`}: полос {x.lanes_before ?? '—'}→{x.lanes_after ?? '—'}, LOS {x.los_before ?? '—'}→{x.los_after ?? '—'}
          </button>
        ))}
      </div>
    </section>
  );
}

// ------------------------------------------------------- road load editor
export function RoadLoad({ row, onSet, docId, rev }: {
  row: LoadRow; onSet: (volume: number | null) => void;
  /** for «Как посчитано» */
  docId?: string; rev?: number;
}) {
  const [v, setV] = useState<number | null>(null);
  const val = v ?? row.volume;
  const max = Math.max(row.capacity * 1.6, 100);
  const vc = row.capacity ? val / row.capacity : 0;
  const los = v === null ? row.los : levelOfService(vc);
  const commit = () => { if (v !== null) { onSet(Math.round(v)); setV(null); } };
  // exact count typed by hand (a traffic survey), not only the slider
  const [typed, setTyped] = useState<string | null>(null);
  const typedValue = typed === null ? null : Number(typed.replace(',', '.').replace(/\s/g, ''));
  const typedOk = typedValue !== null && Number.isFinite(typedValue) && typedValue >= 0 && typedValue <= 20000;
  const commitTyped = () => {
    if (typed === null) return;
    if (typedOk) onSet(Math.round(typedValue!));
    setTyped(null);
  };
  return (
    <div className={`card stack10 ${row.needs_intensity ? 'warn-card' : ''}`}>
      {row.needs_intensity && <b className="small">Новая дорога: задайте интенсивность — без неё модель берёт оценку по классу</b>}
      {row.closed && <b className="small">Дорога перекрыта: поток ушёл на объезд</b>}
      <div className="row between">
        <b>Интенсивность</b>
        <span className="los-dot" style={{ background: LOS_COLOR[los] }} title={LOS_TEXT[los]}>{los}</span>
      </div>
      <input type="range" min={0} max={max} step={10} value={val} aria-label="Интенсивность, авт./ч"
        onChange={(e) => setV(Number(e.target.value))} onPointerUp={commit} onKeyUp={commit} />
      <label className="row gap8 small">
        <span>Данные учёта, авт./ч</span>
        <input type="number" inputMode="numeric" min={0} max={20000} step={1} className="num-input"
          aria-label="Интенсивность по данным учёта, авт./ч" placeholder={String(Math.round(val))}
          value={typed ?? ''} onChange={(e) => setTyped(e.target.value)} onBlur={commitTyped}
          onKeyDown={(e) => { if (e.key === 'Enter') commitTyped(); if (e.key === 'Escape') setTyped(null); }} />
        {typed !== null && !typedOk && <span className="bad">от 0 до 20 000</span>}
      </label>
      <div className="row between small">
        <span className="mono has-tip" title={VC_TIP}>V — {fmt(val)} авт./ч {row.custom ? '(введено вручную)' : '(нет данных учёта)'}</span>
        <span className="mono muted has-tip" title={VC_TIP}>C — пропускная способность {fmt(row.capacity)} авт./ч · v/c {fmt(vc, 2)} ⓘ</span>
      </div>
      <div className="row between small muted">
        <span>t: {fmt(row.t0_s, 0)} с свободно → {fmt(row.t_s, 0)} с</span>
        {row.custom && <button className="link small" onClick={() => onSet(null)}>по умолчанию</button>}
      </div>
      {docId && <LoadExplain docId={docId} wayId={row.id} rev={rev ?? 0} />}
    </div>
  );
}

// ---------------------------------------------------------- before/after
export const ChangesPanel = memo(function ChangesPanel({ data, onShow, overlay, onOverlay }: {
  data: ChangesResult | null;
  onShow: (kind: 'way' | 'node', id: number) => void;
  overlay: boolean;
  onOverlay: (on: boolean) => void;
}) {
  if (!data) return <p className="muted pad">Сравнение с исходным файлом…</p>;
  const by = { added: 0, modified: 0, deleted: 0 };
  data.changes.forEach((c) => { by[c.status] += 1; });
  const rows: [string, number, number][] = [
    ['Компонент связности', data.before.components, data.after.components],
    ['Критичных проблем', data.before.counts.critical, data.after.counts.critical],
    ['Предупреждений', data.before.counts.warning, data.after.counts.warning],
  ];
  return (
    <div className="load-panel">
      <section className="load-col">
        <div className="row between">
          <b>Исходный файл → сейчас</b>
          <label className="row gap8 small"><input type="checkbox" checked={overlay} onChange={(e) => onOverlay(e.target.checked)} />показать на карте</label>
        </div>
        <ResizableTable id="analysis-2" className="cmp">
          <thead><tr><th /><th>Было</th><th>Стало</th><th>Δ</th></tr></thead>
          <tbody>
            {rows.map(([label, b, a]) => (
              <tr key={label}><td>{label}</td><td className="mono">{b}</td><td className="mono">{a}</td>
                <td className="mono"><Delta before={b} after={a} digits={0} lowerBetter /></td></tr>
            ))}
          </tbody>
        </ResizableTable>
        <div className="row gap8 small">
          {(['matsim', 'sumo', 'webots'] as const).map((l) => (
            <span key={l} className={`level ${data.after.levels[l]}`}>
              {l === 'matsim' ? 'MatSim' : l === 'sumo' ? 'SUMO' : 'Webots'}: {SEV_LEVEL[data.before.levels[l]]} → {SEV_LEVEL[data.after.levels[l]]}
            </span>
          ))}
        </div>
        <span className="small muted">
          <span className="sw-line added" /> добавлено {by.added} · <span className="sw-line modified" /> изменено {by.modified} ·{' '}
          <span className="sw-line deleted" /> удалено {by.deleted} · пунктир — как было
        </span>
      </section>
      <section className="load-col wide">
        <b>Что изменилось</b>
        {data.changes.length === 0 && <span className="muted">Правок нет — файл совпадает с исходным.</span>}
        <div className="change-list">
          {data.changes.slice(0, 200).map((c) => (
            <div key={`${c.kind}${c.id}`} className={`change ${c.status}`}>
              <button className="link" disabled={c.status === 'deleted' || c.kind === 'relation'}
                onClick={() => c.kind !== 'relation' && onShow(c.kind, c.id)}>
                {c.kind === 'way' ? 'ребро' : c.kind === 'node' ? 'узел' : 'отношение'} {c.name || c.highway || c.id}
              </button>
              <span className="muted small">
                {c.status === 'added' ? 'добавлен' : c.status === 'deleted' ? 'удалён' : 'изменён'}
                {c.geometry ? ' · геометрия' : ''}
              </span>
              {c.tags.slice(0, 4).map((t) => (
                <span key={t.key} className="mono small tagdiff">
                  {t.key}: <s>{t.before ?? '—'}</s> → <b>{t.after ?? '—'}</b>
                </span>
              ))}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
});

// ------------------------------------------------------- export pre-check
export function ExportCheck({ validation, onFixList, onExport, onClose, filename }: {
  validation: ValidationResult | null;
  onFixList: () => void;
  onExport: () => void;
  onClose: () => void;
  filename: string;
}) {
  const s = validation?.summary;
  const blocked = !s || s.counts.critical > 0 || s.components > 1;
  useEnterKey(!blocked, onExport);  // Enter saves a file that passed the check
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Проверка перед сохранением" onClick={(e) => e.stopPropagation()}>
        <h2>Проверка перед сохранением</h2>
        {!s ? <p>Проверка ещё идёт…</p> : (
          <>
            <p className={s.components <= 1 ? 'good' : 'bad'}>
              Связность: {s.components <= 1 ? 'граф связен' : `${s.components} несвязанных компоненты — MatSim не построит часть маршрутов`}
              {s.outside_strong > 0 && `; вне сильной связности: ${s.outside_strong} узлов`}
            </p>
            <p>Критичных: <b className={s.counts.critical ? 'bad' : 'good'}>{s.counts.critical}</b>, предупреждений: {s.counts.warning}, замечаний: {s.counts.info}</p>
            <div className="row gap8">
              {(['matsim', 'sumo', 'webots'] as const).map((l) => (
                <span key={l} className={`level ${s.levels[l]}`} title={s.level_notes?.[l]}>
                  {l === 'matsim' ? 'MatSim' : l === 'sumo' ? 'SUMO' : 'Webots'}: {SEV_LEVEL[s.levels[l]]}
                </span>
              ))}
            </div>
          </>
        )}
        <div className="row gap8">
          {blocked && <button className="btn" onClick={onFixList}>К списку проблем</button>}
          <span className="grow" />
          <button className="btn" onClick={onClose}>Отмена</button>
          <button className={`btn ${blocked ? 'danger' : 'primary'}`} onClick={onExport} disabled={!s}>
            {blocked ? `Всё равно сохранить ${filename}` : `Сохранить ${filename}`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ report
export function ReportModal({ data, onClose, onDownload }: {
  data: ReportData | null | 'error';
  onClose: () => void;
  onDownload: (kind: 'xlsx' | 'docx' | 'pdf') => void;
}) {
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal report" role="dialog" aria-label="Отчёт" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h2>Отчёт по схеме</h2>
          <div className="row gap8">
            <button className="btn" onClick={() => onDownload('pdf')}>PDF</button>
            <button className="btn" onClick={() => onDownload('docx')}>Word (.docx)</button>
            <button className="btn primary" onClick={() => onDownload('xlsx')}>Excel (.xlsx)</button>
          </div>
        </div>
        {data === null && <p>Собираю данные…</p>}
        {data === 'error' && <p className="bad">Не удалось собрать отчёт.</p>}
        {data && data !== 'error' && (
          <>
            <p className="muted small">{data.filename} · {data.generated}. В Excel — те же цифры и диаграммы Excel, построенные по ним.</p>
            <div className="kpis">
              <div><b>{fmt(data.stats.road_km, 1)}</b><span>км дорог</span></div>
              <div><b>{fmt(data.stats.lane_km, 1)}</b><span>полосо-км</span></div>
              <div><b>{data.stats.buildings.count}</b><span>зданий</span></div>
              <div><b>{data.stats.signals}</b><span>светофоров</span></div>
              <div><b>{data.validation.summary.components}</b><span>компонент связности</span></div>
              <div><b>{data.changes.added + data.changes.modified + data.changes.deleted}</b><span>изменено объектов</span></div>
            </div>
            <div className="report-grid">
              <section>
                <h3>Протяжённость по классам, км</h3>
                <Bars items={data.stats.classes.map((c) => ({ label: c.label, value: c.length_km, color: '#2563EB' }))} />
              </section>
              <section>
                <h3>Проблемы графа</h3>
                <Bars items={data.validation.matrix.map((m) => ({
                  label: m.category, value: m.critical + m.warning + m.info,
                  color: m.critical ? '#EA580C' : m.warning ? '#D97706' : '#64748B',
                }))} />
              </section>
              <section>
                <h3>Уровень обслуживания дорог</h3>
                <AnalyticsAssumptions />
                <Bars items={(Object.keys(data.load.summary.by_los) as Los[]).map((l) => ({
                  label: `${l} — ${LOS_TEXT[l]}`, value: data.load.summary.by_los[l], color: LOS_COLOR[l],
                }))} />
              </section>
              {data.comparison && (
                <section>
                  <h3>Уровень обслуживания: «{data.comparison.a.name}» → «{data.comparison.b.name}», рёбер</h3>
                  <Bars items={(Object.keys(data.comparison.los_after) as Los[]).map((l) => ({
                    label: l, value: data.comparison!.los_after[l], value2: data.comparison!.los_before[l], color: LOS_COLOR[l],
                  }))} />
                </section>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

interface ExplainStep { name: string; formula: string; substitution: string; value: number | string; unit: string; source?: string }

/**
 * «Как посчитано»: collapsed, only the main results; expanded, every step of the load
 * model — the formula, the numbers put into it and the result (fetched from the server,
 * so it is exactly what the model computed, signal cycle and green included).
 */
export function LoadExplain({ docId, wayId, rev }: { docId: string; wayId: number; rev: number }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<ExplainStep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setSteps(null);
    api.explainLoad(docId, wayId)
      .then((r) => { if (alive) { setSteps((r as { steps: ExplainStep[] }).steps); setError(null); } })
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [open, docId, wayId, rev]);
  return (
    <div className="explain">
      <button className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? '▾ Скрыть расчёт' : '▸ Как посчитано'}
      </button>
      {open && (error ? <p className="bad small">Расчёт не загрузился: {error}</p>
        : !steps ? <p className="muted small">Загрузка расчёта…</p> : (
          <table className="cmp explain-table">
            <thead><tr><th>Величина</th><th>Формула и подстановка</th><th>Результат</th></tr></thead>
            <tbody>
              {steps.map((s, i) => (
                <tr key={i} title={s.source}>
                  <td>{s.name}</td>
                  <td><span className="mono">{s.formula}</span><br /><span className="muted small">{s.substitution}</span></td>
                  <td className="mono">{typeof s.value === 'number' ? fmt(s.value, s.value % 1 ? 2 : 0) : s.value} {s.unit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
    </div>
  );
}
