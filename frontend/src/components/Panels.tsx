import { memo, useState, type ReactNode } from 'react';
import type { Fix, HistoryInfo, Issue, LayerVisibility, Tool, ValidationResult } from '../types';
import { TEMPLATES, type RoadTemplate } from '../utils/osm';

// ------------------------------------------------------------------ tools
export const TOOLS: { id: Tool; title: string; key: string; d: string }[] = [
  { id: 'select', title: 'Выбор и перемещение', key: 'V', d: 'M5 3l14 8-6 2-2 6z' },
  { id: 'way', title: 'Новое ребро (Enter — завершить)', key: 'W', d: 'M4 18L10 8l6 6 4-9' },
  { id: 'node', title: 'Вставить узел', key: 'N', d: 'M8 12a4 4 0 1 0 8 0a4 4 0 1 0-8 0' },
  { id: 'split', title: 'Разделить ребро по узлу', key: 'S', d: 'M4 12h6m4 0h6M12 4v4m0 8v4' },
  { id: 'merge', title: 'Соединить узлы', key: 'M', d: 'M6 5l6 7 6-7M12 12v8' },
  { id: 'reverse', title: 'Развернуть ребро', key: 'R', d: 'M4 8h13l-3-3M20 16H7l3 3' },
  { id: 'signal', title: 'Светофор', key: 'T', d: 'M9 2h6v20h-6zM12 6v.01M12 12v.01M12 18v.01' },
  { id: 'crossing', title: 'Пешеходный переход', key: 'P', d: 'M4 6h16M4 18h16M7 9v6M12 9v6M17 9v6' },
];

export const TOOL_HINTS: Record<Tool, string> = {
  select: 'Клик — выбрать · перетащите вершину выбранного ребра',
  way: 'Клик — точка (к узлу/ребру прилипает) · двойной клик или Enter — завершить · Backspace — шаг назад · Esc — отмена',
  node: 'Клик по ребру — вставить узел; по пустому месту — отдельный узел',
  split: 'Клик по внутреннему узлу — разделить ребро',
  merge: 'Клик по первому узлу, затем по второму — склеить',
  reverse: 'Клик по ребру — развернуть направление движения',
  signal: 'Клик по узлу или ребру — поставить светофор',
  crossing: 'Клик по ребру — пешеходный переход',
};

export function ToolRail({ tool, onTool }: { tool: Tool; onTool: (t: Tool) => void }) {
  return (
    <div className="rail" role="toolbar" aria-label="Инструменты" aria-orientation="vertical">
      {TOOLS.map((t) => (
        <button key={t.id} className="tool" title={`${t.title} · ${t.key}`} aria-label={t.title}
          aria-pressed={tool === t.id} onClick={() => onTool(t.id)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={t.d} /></svg>
          <span className="tool-key">{t.key}</span>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- layers
const LAYERS: { key: keyof LayerVisibility; name: string; sw: string }[] = [
  { key: 'roads', name: 'Дороги', sw: '#E8C36A' },
  { key: 'paths', name: 'Пешеходные', sw: '#9CA3AF' },
  { key: 'rail', name: 'Трамвай / ж/д', sw: '#7C3AED' },
  { key: 'signals', name: 'Светофоры', sw: '#16A34A' },
  { key: 'crossings', name: 'Переходы', sw: '#F8FAFC' },
  { key: 'stops', name: 'Остановки', sw: '#0F766E' },
  { key: 'buildings', name: 'Здания и зоны', sw: '#C9C5BC' },
  { key: 'issues', name: 'Ошибки графа', sw: '#EA580C' },
];

export function LayersPanel({ layers, onLayers, baseOpacity, onBaseOpacity, canApplyTemplate, onTemplate }: {
  layers: LayerVisibility; onLayers: (l: LayerVisibility) => void;
  baseOpacity: number; onBaseOpacity: (v: number) => void;
  canApplyTemplate: boolean; onTemplate: (t: RoadTemplate) => void;
}) {
  return (
    <aside className="left" aria-label="Слои и шаблоны">
      <h3 className="section">Слои</h3>
      <div className="layer-list">
        {(() => {
          const all = LAYERS.every((l) => layers[l.key]);
          const some = !all && LAYERS.some((l) => layers[l.key]);
          return (
            <label className="layer layer-all">
              <input type="checkbox" checked={all}
                ref={(el) => { if (el) el.indeterminate = some; }}
                onChange={() => onLayers(Object.fromEntries(LAYERS.map((l) => [l.key, !all])) as unknown as LayerVisibility)} />
              <span className="sw" aria-hidden="true" />
              <span><b>Выбрать все</b></span>
            </label>
          );
        })()}
        {LAYERS.map((l) => (
          <label key={l.key} className="layer">
            <input type="checkbox" checked={layers[l.key]} onChange={() => onLayers({ ...layers, [l.key]: !layers[l.key] })} />
            <span className="sw" style={{ background: l.sw }} aria-hidden="true" />
            <span>{l.name}</span>
          </label>
        ))}
      </div>
      <div className="card stack10 m14">
        <div className="row between"><b>Подложка OSM</b><span className="mono muted small">{baseOpacity}%</span></div>
        <label className="small muted stack4">Прозрачность
          <input type="range" min={0} max={100} value={baseOpacity} onChange={(e) => onBaseOpacity(Number(e.target.value))} />
        </label>
      </div>
      <h3 className="section">Шаблоны дорог <span className="muted">{canApplyTemplate ? 'к выбранному' : 'выберите ребро'}</span></h3>
      <div className="stack6 px14">
        {TEMPLATES.map((t) => (
          <button key={t.key} className="template" disabled={!canApplyTemplate} onClick={() => onTemplate(t)}>
            <span className="bars" aria-hidden="true">
              {Array.from({ length: t.backward }, (_, i) => <i key={`b${i}`} className="bar back" />)}
              {Array.from({ length: t.forward }, (_, i) => <i key={`f${i}`} className="bar" />)}
            </span>
            <span className="grow"><b>{t.name}</b><br /><small className="muted">{t.sub}</small></span>
            <kbd>{t.key}</kbd>
          </button>
        ))}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------- issues
const SEV_LABEL = { critical: 'критично', warning: 'предупреждение', info: 'замечание' } as const;

const ISSUES_PAGE = 200;

export type BottomTab = 'issues' | 'history' | 'load' | 'changes';

export const BottomPanel = memo(function BottomPanel({ validation, history, tab, onTab, onShow, onFix, validating, children }: {
  validation: ValidationResult | null; history: HistoryInfo | null;
  tab: BottomTab; onTab: (t: BottomTab) => void;
  onShow: (i: Issue) => void; onFix: (i: Issue, fix: Fix) => void; validating: boolean;
  /** content of the "load" and "changes" tabs */
  children?: ReactNode;
}) {
  const issues = validation?.issues ?? [];
  // thousands of DOM rows make every re-render slow; show a page, load more on demand
  const [limit, setLimit] = useState(ISSUES_PAGE);
  return (
    <section className="bottom" aria-label="Проблемы и история">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'issues'} onClick={() => onTab('issues')}>Проблемы графа · {issues.length}</button>
        <button role="tab" aria-selected={tab === 'history'} onClick={() => onTab('history')}>История правок · {history?.entries.length ?? 0}</button>
        <button role="tab" aria-selected={tab === 'changes'} onClick={() => onTab('changes')}>До / после</button>
        <button role="tab" aria-selected={tab === 'load'} onClick={() => onTab('load')}>Нагрузка</button>
        <span className="grow" />
        {validation && (
          <span className="levels">
            {(['matsim', 'sumo', 'webots'] as const).map((l) => (
              <span key={l} className={`level ${validation.summary.levels[l]}`} title={validation.summary.level_notes?.[l]}>
                {l === 'matsim' ? 'MatSim' : l === 'sumo' ? 'SUMO' : 'Webots'}
              </span>
            ))}
          </span>
        )}
        <span className="muted small">{validating ? 'проверка…' : 'проверка после каждой правки'}</span>
      </div>
      <div className="bottom-body">
        {tab === 'issues' && (issues.length === 0
          ? <p className="ok-msg">Проблем не найдено</p>
          : issues.slice(0, limit).map((i) => (
            <div key={i.id} className="issue">
              <span className={`sev ${i.severity}`} title={SEV_LABEL[i.severity]}>{i.severity === 'info' ? 'i' : '!'}</span>
              <button className="link issue-title" onClick={() => onShow(i)}>{i.title}</button>
              <span className="issue-desc">{i.description}</span>
              <span className="mono muted small">{i.refs.slice(0, 2).map((r) => `${r.kind}/${r.id}`).join(', ')}</span>
              <span className="issue-fixes">
                {(i.fixes?.length ? i.fixes : i.fix ? [i.fix] : []).map((f) => (
                  <button key={`${f.op}:${f.label}`} className="btn small" onClick={() => onFix(i, f)}>{f.label}</button>
                ))}
              </span>
            </div>
          )))}
        {tab === 'issues' && issues.length > limit && (
          <button className="btn small more" onClick={() => setLimit(limit + ISSUES_PAGE)}>
            Показать ещё {Math.min(ISSUES_PAGE, issues.length - limit)} из {issues.length - limit}
          </button>
        )}
        {(tab === 'load' || tab === 'changes') && children}
        {tab === 'history' && (history?.entries.length
          ? history.entries.map((e, idx) => <div key={idx} className="hist">{idx === 0 ? '● ' : ''}{e}</div>)
          : <p className="muted pad">Правок пока нет</p>)}
      </div>
    </section>
  );
});
