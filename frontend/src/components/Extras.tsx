import { AnalysisTools, type AnalysisProps } from './AnalysisTools';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { HistoryEntry, Kind, LoadState, OdMatrix, Selection, Task, TaskCriterion, TaskResult } from '../types';
import { fmt } from '../utils/geom';
import { AnalyticsAssumptions, Bars, LOS_COLOR } from './Analysis';

// --------------------------------------------------------- intensity presets
export const INTENSITY_PRESETS: { name: string; multiplier: number; hint: string }[] = [
  { name: 'Утренний пик', multiplier: 1.0, hint: '07–09, расчётный час' },
  { name: 'Вечерний пик', multiplier: 1.1, hint: '17–19' },
  { name: 'Межпиковый', multiplier: 0.65, hint: 'день' },
  { name: 'Выходной', multiplier: 0.5, hint: 'суббота' },
  { name: 'Ночь', multiplier: 0.15, hint: '00–05' },
  { name: 'Перспектива +30%', multiplier: 1.3, hint: 'рост спроса' },
];

export function IntensityPresets({ current, onPick }: { current: number; onPick: (m: number) => void }) {
  return (
    <div className="preset-grid" role="group" aria-label="Шаблоны интенсивности">
      {INTENSITY_PRESETS.map((p) => (
        <button key={p.name} className="btn small" aria-pressed={Math.abs(current - p.multiplier) < 0.01}
          title={`${p.hint}: ×${p.multiplier}`} onClick={() => onPick(p.multiplier)}>
          {p.name} <span className="muted">×{p.multiplier}</span>
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------ analytics mode
const CLASS_RU: Record<string, string> = {
  motorway: 'Автомагистрали', trunk: 'Скоростные', primary: 'Магистральные', secondary: 'Районные', tertiary: 'Местные',
  residential: 'Жилые', service: 'Проезды', unclassified: 'Прочие', living_street: 'Жилые зоны',
};

export function AnalyticsPanel({ docId, rev, load, onMultiplier, onShowRoad, onReopen, onOpenTasks, analysis, active }: {
  analysis: AnalysisProps;
  active: boolean;
  docId: string;
  rev: number;
  load: LoadState | null;
  onMultiplier: (m: number) => void;
  onShowRoad: (id: number) => void;
  onReopen: (wayId: number) => void;
  onOpenTasks: () => void;
}) {
  const [opened, setOpened] = useState(active);
  useEffect(() => { if (active) setOpened(true); }, [active]);
  const [od, setOd] = useState<OdMatrix | null>(null);
  const [grid, setGrid] = useState(3);
  const [view, setView] = useState<'trips' | 'shares' | 'minutes'>('trips');
  useEffect(() => {
    if (!active) return;
    let alive = true;
    api.od(docId, grid).then((m) => alive && setOd(m)).catch(() => undefined);
    return () => { alive = false; };
  }, [docId, grid, rev, active]);

  const flows = useMemo(() => {
    if (!load) return null;
    const byClass = new Map<string, number>();
    for (const r of load.rows) {
      const k = r.highway.replace('_link', '');
      byClass.set(k, (byClass.get(k) ?? 0) + (r.volume * r.length_m) / 1000);
    }
    const classes = [...byClass.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7);
    const top = [...load.rows].filter((r) => !r.closed).sort((a, b) => b.volume - a.volume).slice(0, 8);
    const transit = load.rows.reduce((s, r) => s + (r.transit_pcu ?? 0) * r.length_m / 1000, 0);
    const missing = load.rows.filter((r) => r.needs_intensity);
    return { classes, top, transit, missing };
  }, [load]);

  if (!load || !flows) return <aside className="inspector empty" style={{ display: active ? undefined : 'none' }}><p>Расчёт аналитики…</p></aside>;
  const s = load.summary;
  const matrix = od ? (view === 'trips' ? od.trips : view === 'shares' ? od.shares : od.minutes) : [];
  const maxCell = Math.max(1, ...matrix.flat().map((v) => (typeof v === 'number' ? v : 0)));
  return (
    <aside className="inspector analytics" aria-label="Аналитика сети" style={{ display: active ? undefined : 'none' }}>
      <div className="insp-head"><span className="badge">АНАЛИТИКА</span><h2>Потоки и сценарии</h2></div>
      <div className="insp-body stack16">
        {opened && <AnalysisTools {...analysis} />}
        <section className="stack10">
          <b>Сценарий спроса</b>
          <IntensityPresets current={load.multiplier} onPick={onMultiplier} />
          <AnalyticsAssumptions />
        </section>

        <section className="stack10">
          <b>Статистика потоков</b>
          <div className="kpis">
            <div><b>{fmt(s.veh_km)}</b><span>авт·км за час</span></div>
            <div><b>{fmt(s.veh_hours, 0)}</b><span>авт·ч в пути</span></div>
            <div><b>{fmt(s.delay_hours, 1)}</b><span>авт·ч задержки</span></div>
            <div><b>{fmt(flows.transit)}</b><span>лег. экв.·км ОТ</span></div>
          </div>
          <span className="small muted">Пробег по классам дорог, авт·км/ч</span>
          <Bars items={flows.classes.map(([k, v]) => ({ label: CLASS_RU[k] ?? k, value: Math.round(v), color: '#2563EB' }))} />
          <span className="small muted">Распределение рёбер по уровню обслуживания</span>
          <div className="los-strip">
            {(Object.keys(s.by_los) as (keyof typeof s.by_los)[]).map((l) => (
              <span key={l} style={{ flex: Math.max(s.by_los[l], 0.001), background: LOS_COLOR[l] }} title={`${l}: ${s.by_los[l]}`}>
                {s.by_los[l] ? l : ''}</span>
            ))}
          </div>
          <span className="small muted">Самые нагруженные участки</span>
          {flows.top.map((r) => (
            <button key={r.id} className="link worst" onClick={() => onShowRoad(r.id)}>
              <span className="los-dot" style={{ background: LOS_COLOR[r.los] }}>{r.los}</span>
              <span className="grow ellipsis">{r.name || `${r.highway} #${r.id}`}</span>
              <span className="mono small">{fmt(r.volume)} авт/ч</span>
            </button>
          ))}
          {flows.missing.length > 0 && (
            <div className="card warn-card stack6">
              <b>Новые дороги без интенсивности: {flows.missing.length}</b>
              <span className="small">Для дороги, добавленной в редакторе, интенсивность нужно задать вручную.</span>
              {flows.missing.slice(0, 5).map((r) => (
                <button key={r.id} className="link small" onClick={() => onShowRoad(r.id)}>{r.name || `#${r.id}`} — задать</button>
              ))}
            </div>
          )}
        </section>

        <section className="stack10">
          <div className="row between"><b>ДТП и перекрытия</b><span className="small muted">перекрыть — в свойствах дороги</span></div>
          {(load.closures ?? []).length === 0 && <span className="small muted">Нет перекрытых дорог.</span>}
          {(load.closures ?? []).map((c) => (
            <div key={c.id} className="card stack6">
              <div className="row between">
                <button className="link" onClick={() => onShowRoad(c.id)}><b>{c.name || `#${c.id}`}</b></button>
                <button className="btn small" onClick={() => onReopen(c.id)}>Открыть</button>
              </div>
              <span className="small">{c.found
                ? `Объезд по ${c.ways.length} рёбрам, +${fmt(Math.max(0, c.detour_s), 0)} с, перешло ${fmt(c.moved ?? c.volume)} авт/ч`
                  + (c.partial ? '; в обратном направлении объезда нет' : '')
                : 'Объезда нет в пределах карты — поток не может проехать'}</span>
            </div>
          ))}
        </section>

        <section className="stack10">
          <div className="row between">
            <b>Матрица корреспонденций</b>
            <select value={grid} onChange={(e) => setGrid(Number(e.target.value))} aria-label="Число зон">
              {[2, 3, 4].map((g) => <option key={g} value={g}>{g}×{g} зон</option>)}
            </select>
          </div>
          <div className="seg" role="group" aria-label="Что показать">
            <button aria-pressed={view === 'trips'} onClick={() => setView('trips')}>Поездки/ч</button>
            <button aria-pressed={view === 'shares'} onClick={() => setView('shares')}>Доли, %</button>
            <button aria-pressed={view === 'minutes'} onClick={() => setView('minutes')}>Время, мин</button>
          </div>
          {!od ? <span className="small muted">Расчёт…</span> : od.zones.length === 0 ? <span className="small muted">Нет зданий для зон.</span> : (
            <div className="od-wrap">
              <table className="od">
                <thead><tr><th>Из \ в</th>{od.zones.map((z) => <th key={z.id} title={`${z.residents} жит., ${z.jobs} раб. мест`}>{z.name}</th>)}</tr></thead>
                <tbody>
                  {od.zones.map((z, i) => (
                    <tr key={z.id}>
                      <th title={`${z.residents} жителей`}>{z.name}</th>
                      {matrix[i].map((v, j) => (
                        <td key={j} style={{ background: typeof v === 'number' && view !== 'minutes'
                          ? `rgba(37,99,235,${Math.min(0.85, v / maxCell)})` : undefined }}>
                          {v === null ? '—' : fmt(v as number, view === 'trips' ? 0 : 1)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {od && <span className="small muted">Гравитационная модель: {fmt(od.total)} поездок в час пик, жители — по этажности и квартирам,
            рабочие места — по площади нежилых зданий, время — кратчайший путь по дорогам.</span>}
        </section>

        <button className="btn" onClick={onOpenTasks}>Задания и проверка</button>
      </div>
    </aside>
  );
}

// ------------------------------------------------------------------ history
const STATUS_RU = { added: 'добавлен', modified: 'изменён', deleted: 'удалён' } as const;

export function HistoryList({ details, labels, onShow }: {
  details?: HistoryEntry[];
  labels: string[];
  onShow: (refs: { kind: Kind; id: number }[]) => void;
}) {
  if (!details?.length) {
    return labels.length
      ? <>{labels.map((e, i) => <div key={i} className="hist">{e}</div>)}</>
      : <p className="muted pad">Правок пока нет</p>;
  }
  return (
    <>
      {details.map((h, i) => (
        <button key={i} className="hist hist-entry" onClick={() => onShow(h.refs)} title="Показать изменённые объекты">
          <span className="mono small muted">{h.at ? new Date(h.at * 1000).toLocaleTimeString('ru-RU') : ''}</span>
          <span className="hist-author" style={{ background: authorColor(h.author) }}>{h.author ?? 'без имени'}</span>
          <b>{h.label}</b>
          <span className="small muted ellipsis">
            {h.refs.slice(0, 3).map((r) => `${r.kind === 'way' ? 'ребро' : r.kind === 'node' ? 'узел' : 'отношение'} ${r.name || r.id} ${STATUS_RU[r.status]}`).join('; ')}
            {h.count > 3 ? ` и ещё ${h.count - 3}` : ''}
          </span>
        </button>
      ))}
    </>
  );
}

export function authorColor(name: string | null): string {
  if (!name) return '#475569';
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 60% 40%)`;
}

// -------------------------------------------------------------------- tasks
const CRITERIA_TYPES: { type: TaskCriterion['type']; label: string; needs: 'value' | 'way' | 'node' | 'category' | 'tag' }[] = [
  { type: 'no_issues', label: 'Нет проблем графа категории', needs: 'category' },
  { type: 'components_max', label: 'Компонент связности не больше', needs: 'value' },
  { type: 'max_vc', label: 'Максимальная v/c не больше', needs: 'value' },
  { type: 'avg_vc', label: 'Средняя v/c не больше', needs: 'value' },
  { type: 'lanes_min', label: 'Полос на выбранном ребре не меньше', needs: 'way' },
  { type: 'signal_at', label: 'Светофор на выбранном узле', needs: 'node' },
  { type: 'tag', label: 'Тег выбранного объекта равен', needs: 'tag' },
];
const CATEGORIES = [['', 'любой'], ['connectivity', 'связность'], ['junction', 'перекрёстки'], ['direction', 'направления'],
  ['lanes', 'полосы'], ['geometry', 'геометрия'], ['data', 'данные']];

export function TasksModal({ docId, selection, onClose }: { docId: string; selection: Selection; onClose: () => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ title: string; description: string; criteria: TaskCriterion[] } | null>(null);
  const [ctype, setCtype] = useState<TaskCriterion['type']>('no_issues');
  const [cval, setCval] = useState('');
  const [ckey, setCkey] = useState('maxspeed');

  useEffect(() => {
    api.tasks(docId).then((r) => { setTasks(r.tasks); setActive((a) => a ?? r.tasks[0]?.id ?? null); }).catch((e: Error) => setError(e.message));
  }, [docId]);
  const task = tasks.find((t) => t.id === active);

  const check = async () => {
    if (!task) return;
    setError(null);
    try { setResult(await api.checkTask(docId, task.id)); } catch (e) { setError((e as Error).message); }
  };
  const addCriterion = () => {
    if (!draft) return;
    const spec = CRITERIA_TYPES.find((c) => c.type === ctype)!;
    const c: TaskCriterion = { type: ctype, label: '' };
    if (spec.needs === 'value') c.value = Number(cval.replace(',', '.')) || 0;
    if (spec.needs === 'category') { if (cval) c.category = cval; }
    if (spec.needs === 'way') {
      if (selection?.kind !== 'way') { setError('Сначала выберите ребро на карте'); return; }
      c.way_id = selection.id; c.value = Number(cval) || 1;
    }
    if (spec.needs === 'node') {
      if (selection?.kind !== 'node') { setError('Сначала выберите узел на карте'); return; }
      c.node_id = selection.id;
    }
    if (spec.needs === 'tag') {
      if (!selection) { setError('Сначала выберите объект на карте'); return; }
      Object.assign(c, { kind: selection.kind, id: selection.id, key: ckey, value: cval });
    }
    c.label = `${spec.label}${spec.needs === 'category' ? ` «${CATEGORIES.find((x) => x[0] === cval)?.[1]}»` : ''}`
      + `${spec.needs === 'value' || spec.needs === 'way' ? ` ${cval || 1}` : ''}`
      + `${spec.needs === 'tag' ? ` ${ckey}=${cval}` : ''}${selection && spec.needs !== 'value' && spec.needs !== 'category' ? ` (${selection.kind}/${selection.id})` : ''}`;
    setError(null);
    setDraft({ ...draft, criteria: [...draft.criteria, c] });
  };
  const save = async () => {
    if (!draft) return;
    try {
      const r = await api.createTask(docId, draft);
      setTasks(r.tasks);
      setActive(r.tasks[r.tasks.length - 1].id);
      setDraft(null);
      setResult(null);
    } catch (e) { setError((e as Error).message); }
  };
  const remove = async (id: string) => {
    const r = await api.deleteTask(docId, id);
    setTasks(r.tasks);
    setActive(r.tasks[0]?.id ?? null);
    setResult(null);
  };

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal tasks" role="dialog" aria-label="Задания" onClick={(e) => e.stopPropagation()}>
        <div className="row between"><h2>Задания</h2><button className="icon-btn" aria-label="Закрыть" onClick={onClose}>×</button></div>
        <div className="tasks-grid">
          <nav className="stack6" aria-label="Список заданий">
            {tasks.map((t) => (
              <button key={t.id} className="btn" aria-pressed={t.id === active && !draft}
                onClick={() => { setActive(t.id); setResult(null); setDraft(null); }}>
                {t.title}{t.builtin ? '' : ' ✎'}
              </button>
            ))}
            <button className="btn small" onClick={() => { setDraft({ title: '', description: '', criteria: [] }); setResult(null); }}>+ Создать задание</button>
          </nav>
          {!draft && task && (
            <section className="stack10">
              <h3>{task.title}</h3>
              <p className="muted">{task.description}</p>
              <ol className="criteria">
                {task.criteria.map((c, i) => {
                  const r = result?.results[i];
                  return (
                    <li key={i} className={r ? (r.ok ? 'ok' : 'fail') : ''}>
                      <span className="crit-mark">{r ? (r.ok ? '✓' : '✗') : '•'}</span>
                      <span className="grow">{r?.label ?? c.label}</span>
                      {r && <span className="small muted">{r.detail}</span>}
                    </li>
                  );
                })}
              </ol>
              <div className="row gap8">
                <button className="btn primary" onClick={check}>Проверить</button>
                {result && <b className={result.done ? 'good' : 'bad'}>Выполнено {result.passed} из {result.total}{result.done ? ' — задание решено' : ''}</b>}
                <span className="grow" />
                {!task.builtin && <button className="btn danger small" onClick={() => remove(task.id)}>Удалить</button>}
              </div>
            </section>
          )}
          {draft && (
            <section className="stack10">
              <label className="field"><span>Название</span>
                <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></label>
              <label className="field"><span>Условие для студента</span>
                <textarea rows={2} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
              <ol className="criteria">{draft.criteria.map((c, i) => <li key={i}><span className="crit-mark">•</span>{c.label}</li>)}</ol>
              <div className="row gap8 wrap">
                <select value={ctype} onChange={(e) => { setCtype(e.target.value as TaskCriterion['type']); setCval(''); }} aria-label="Тип требования">
                  {CRITERIA_TYPES.map((c) => <option key={c.type} value={c.type}>{c.label}</option>)}
                </select>
                {CRITERIA_TYPES.find((c) => c.type === ctype)?.needs === 'category' ? (
                  <select value={cval} onChange={(e) => setCval(e.target.value)} aria-label="Категория">
                    {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                ) : CRITERIA_TYPES.find((c) => c.type === ctype)?.needs === 'node' ? null : (
                  <>
                    {ctype === 'tag' && <input value={ckey} onChange={(e) => setCkey(e.target.value)} aria-label="Ключ" style={{ width: 110 }} />}
                    <input value={cval} onChange={(e) => setCval(e.target.value)} placeholder="значение" aria-label="Значение" style={{ width: 90 }} />
                  </>
                )}
                <button className="btn small" onClick={addCriterion}>Добавить требование</button>
              </div>
              <span className="small muted">Требования к ребру, узлу или тегу берут объект, выбранный на карте.</span>
              <div className="row gap8">
                <button className="btn primary" disabled={!draft.criteria.length} onClick={save}>Сохранить задание</button>
                <button className="btn" onClick={() => setDraft(null)}>Отмена</button>
              </div>
            </section>
          )}
        </div>
        {error && <p className="bad small">{error}</p>}
      </div>
    </div>
  );
}

// ----------------------------------------------------------- base map/overlay
export type BaseMap = 'osm' | 'sat' | 'none';

export interface ImageOverlay {
  url: string;
  name: string;
  opacity: number;
  /** top-left, top-right, bottom-right, bottom-left as [lon, lat] */
  corners: [number, number][];
}

export function BasemapControls({ base, onBase, overlay, onOverlayFile, onOverlay }: {
  base: BaseMap;
  onBase: (b: BaseMap) => void;
  overlay: ImageOverlay | null;
  onOverlayFile: (file: File) => void;
  onOverlay: (o: ImageOverlay | null) => void;
}) {
  return (
    <div className="stack6">
      <div className="seg" role="group" aria-label="Подложка">
        {([['osm', 'Схема OSM'], ['sat', 'Спутник'], ['none', 'Нет']] as const).map(([v, l]) => (
          <button key={v} aria-pressed={base === v} onClick={() => onBase(v)}>{l}</button>
        ))}
      </div>
      {!overlay ? (
        <label className="btn small file-btn">Наложить снимок карты…
          <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { const f = e.target.files?.[0]; if (f) onOverlayFile(f); e.target.value = ''; }} />
        </label>
      ) : (
        <div className="card stack6">
          <div className="row between"><b className="ellipsis">{overlay.name}</b>
            <button className="icon-btn" aria-label="Убрать снимок" onClick={() => onOverlay(null)}>×</button></div>
          <label className="small muted stack4">Прозрачность снимка {Math.round(overlay.opacity * 100)}%
            <input type="range" min={0.1} max={1} step={0.05} value={overlay.opacity}
              onChange={(e) => onOverlay({ ...overlay, opacity: Number(e.target.value) })} />
          </label>
          <span className="small muted">Перетащите четыре угловые точки на карте, чтобы совместить снимок с дорогами (привязка и масштаб).</span>
        </div>
      )}
    </div>
  );
}
