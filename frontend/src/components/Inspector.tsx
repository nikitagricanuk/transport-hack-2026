import { memo, useState } from 'react';
import type { LoadRow, NodeProps, Op, Tags, WayProps } from '../types';
import TagEditor from './TagEditor';
import { RoadLoad } from './Analysis';
import { areaM2, fmt, lengthM } from '../utils/geom';
import { HIGHWAY_CLASSES, SPEEDS, SURFACES, laneCounts, onewayMode, turnGlyphs, type OnewayMode } from '../utils/osm';

interface Props {
  way?: WayProps;
  node?: NodeProps & { lon: number; lat: number };
  /** line coordinates of the way, or the outer ring of an area */
  geometry?: number[][];
  loadRow?: LoadRow;
  onSetLoad?: (wayId: number, volume: number | null) => void;
  /** the selected node is a vertex of a drivable way */
  nodeOnRoad?: boolean;
  onOp: (op: Op | Op[], label?: string) => void;
}

const BUILDING_TYPES: [string, string][] = [
  ['yes', 'здание'], ['apartments', 'многоквартирный'], ['house', 'частный дом'], ['residential', 'жилое'],
  ['commercial', 'коммерческое'], ['retail', 'магазин'], ['office', 'офис'], ['school', 'школа'],
  ['university', 'университет'], ['hospital', 'больница'], ['industrial', 'промышленное'], ['garage', 'гараж'],
];

export default memo(function Inspector({ way, node, geometry, loadRow, onSetLoad, nodeOnRoad, onOp }: Props) {
  const [tab, setTab] = useState<'attr' | 'tags'>('attr');
  if (!way && !node) {
    return (
      <aside className="inspector empty" aria-label="Свойства объекта">
        <p>Выберите ребро или узел на карте.</p>
        <p className="muted">V — выбор, W — новое ребро, N — узел, S — разделить, M — соединить, T — светофор, P — переход.</p>
        <p className="muted">+ / − — масштаб, 0 — весь участок. Рисование ребра: Enter — готово, Esc или правая кнопка мыши — отменить, ⌫ — шаг назад.</p>
      </aside>
    );
  }
  const kind = way ? 'way' : 'node';
  const id = (way ?? node)!.id;
  const tags = (way ?? node)!.tags;
  const setTags = (patch: Record<string, string | null>, label?: string) =>
    onOp({ op: 'set_tags', args: { kind, id, tags: patch } }, label);

  return (
    <aside className="inspector" aria-label="Свойства объекта">
      <div className="insp-head">
        <div className="row gap8">
          <span className="badge">{way ? (way.layer === 'road' ? 'РЕБРО' : way.layer.toUpperCase()) : 'УЗЕЛ'}</span>
          <span className="mono muted">{kind}/{id}</span>
        </div>
        <h2>{tags.name || (way ? way.highway || 'Без названия' : nodeTitle(tags))}</h2>
        {way && <span className="muted small">{way.nodes.length} узлов{geometry ? ` · ${metrics(way, geometry)}` : ''}</span>}
        {node && <span className="muted small mono">{node.lat.toFixed(6)}, {node.lon.toFixed(6)}</span>}
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'attr'} onClick={() => setTab('attr')}>Атрибуты</button>
        <button role="tab" aria-selected={tab === 'tags'} onClick={() => setTab('tags')}>Теги OSM · {Object.keys(tags).length}</button>
      </div>
      <div className="insp-body">
        {tab === 'tags' && <TagEditor tags={tags} onChange={(p) => setTags(p)} />}
        {tab === 'attr' && way && (way.layer === 'building' || way.layer === 'area')
          && <AreaAttributes way={way} tags={tags} setTags={setTags} area={geometry ? areaM2(geometry) : 0} />}
        {tab === 'attr' && way && way.layer !== 'building' && way.layer !== 'area' && (
          <>
            <WayAttributes way={way} tags={tags} setTags={setTags} onOp={onOp} />
            {loadRow && onSetLoad && <RoadLoad key={way.id} row={loadRow} onSet={(v) => onSetLoad(way.id, v)} />}
          </>
        )}
        {tab === 'attr' && node && !nodeOnRoad && (
          <div className="card stack10 warn-card">
            <span>Узел не стоит на проезжей части — SUMO не увидит {tags.highway === 'crossing' ? 'переход' : 'светофор'}.</span>
            <button className="btn small primary" onClick={() => onOp({ op: 'attach_to_road', args: { node_id: node.id } }, 'Узел привязан к дороге')}>
              Привязать к ближайшей дороге
            </button>
          </div>
        )}
        {tab === 'attr' && node && <NodeAttributes tags={tags} setTags={setTags} />}
      </div>
      <div className="insp-foot">
        {way && way.layer !== 'building' && way.layer !== 'area' && (
          <button className="btn" onClick={() => onOp({ op: 'reverse_way', args: { id } })}>Развернуть</button>
        )}
        <button className="btn danger" onClick={() => onOp({ op: 'delete', args: { kind, id } })}>Удалить</button>
      </div>
    </aside>
  );
});

function metrics(way: WayProps, geometry: number[][]): string {
  if (way.layer === 'building' || way.layer === 'area') {
    return `${fmt(areaM2(geometry))} м² · периметр ${fmt(lengthM(geometry))} м`;
  }
  return `длина ${fmt(lengthM(geometry))} м`;
}

function AreaAttributes({ way, tags, setTags, area }: {
  way: WayProps; tags: Tags; area: number;
  setTags: (p: Record<string, string | null>, label?: string) => void;
}) {
  const levels = parseInt(tags['building:levels'] ?? '', 10);
  const flats = tags['building:flats'] ?? '';
  const [flatsDraft, setFlatsDraft] = useState(flats);
  const [prevFlats, setPrevFlats] = useState(flats);
  if (prevFlats !== flats) { setPrevFlats(flats); setFlatsDraft(flats); }
  return (
    <div className="stack16">
      <div className="kpis">
        <div><b>{fmt(area)}</b><span>м² пятно</span></div>
        <div><b>{Number.isFinite(levels) ? fmt(area * levels) : '—'}</b><span>м² этажей</span></div>
        <div><b>{Number.isFinite(levels) ? levels : '—'}</b><span>этажей</span></div>
      </div>
      {way.layer === 'building' && (
        <>
          <label className="field"><span>Тип здания</span>
            <select value={tags.building ?? 'yes'} onChange={(e) => setTags({ building: e.target.value })}>
              {BUILDING_TYPES.map(([v, l]) => <option key={v} value={v}>{l} ({v})</option>)}
              {tags.building && !BUILDING_TYPES.some(([v]) => v === tags.building) && <option value={tags.building}>{tags.building}</option>}
            </select>
          </label>
          <div className="grid2">
            <Stepper label="Этажей" value={Number.isFinite(levels) ? levels : 1}
              onChange={(v) => setTags({ 'building:levels': String(v) }, 'Изменена этажность')} max={60} />
            <label className="field"><span>Квартир</span>
              <input inputMode="numeric" value={flatsDraft} onChange={(e) => setFlatsDraft(e.target.value.replace(/\D/g, ''))}
                onBlur={() => flatsDraft !== flats && setTags({ 'building:flats': flatsDraft || null })} />
            </label>
          </div>
        </>
      )}
      <NameField value={tags.name ?? ''} onCommit={(v) => setTags({ name: v || null })} />
      <p className="muted small">Вершины контура можно перетаскивать на карте инструментом «Выбор».</p>
    </div>
  );
}

function nodeTitle(tags: Tags) {
  if (tags.highway === 'traffic_signals') return 'Светофор';
  if (tags.highway === 'crossing') return 'Пешеходный переход';
  if (tags.highway === 'bus_stop') return 'Остановка';
  return 'Узел';
}

function WayAttributes({ way, tags, setTags, onOp }: {
  way: WayProps; tags: Tags;
  setTags: (p: Record<string, string | null>, label?: string) => void;
  onOp: (op: Op | Op[], label?: string) => void;
}) {
  const mode = onewayMode(tags);
  const { forward, backward } = laneCounts(tags);
  const setLanes = (f: number, b: number, ow: OnewayMode) =>
    onOp({ op: 'set_lanes', args: { way_id: way.id, forward: f, backward: b, oneway: ow } }, 'Изменены полосы');
  const isRoad = way.layer === 'road';
  const bridge = tags.bridge !== undefined && tags.bridge !== 'no';
  const fwTurns = turnGlyphs(mode === 'no' ? tags['turn:lanes:forward'] : tags['turn:lanes'], forward);
  const bwTurns = mode === 'no' ? turnGlyphs(tags['turn:lanes:backward'], backward) : [];

  return (
    <div className="stack16">
      <div className="grid2">
        <label className="field"><span>Класс дороги</span>
          <select value={tags.highway ?? ''} onChange={(e) => setTags({ highway: e.target.value || null })}>
            <option value="">—</option>
            {HIGHWAY_CLASSES.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
            {tags.highway && !HIGHWAY_CLASSES.some((h) => h.value === tags.highway) && <option value={tags.highway}>{tags.highway}</option>}
          </select>
        </label>
        <label className="field"><span>Покрытие</span>
          <select value={tags.surface ?? ''} onChange={(e) => setTags({ surface: e.target.value || null })}>
            <option value="">—</option>
            {SURFACES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      </div>
      <NameField value={tags.name ?? ''} onCommit={(v) => setTags({ name: v || null })} />

      {isRoad && (
        <>
          <div className="field"><span>Направление движения</span>
            <div className="seg" role="group" aria-label="Направление">
              {([['no', '⇄ Двустороннее'], ['yes', '→ По ходу'], ['-1', '← Против']] as const).map(([v, label]) => (
                <button key={v} aria-pressed={mode === v} onClick={() => setLanes(forward, Math.max(1, backward), v)}>{label}</button>
              ))}
            </div>
          </div>

          <div className="card stack10">
            <div className="row between"><b>Полосы</b><span className="muted small">turn:lanes не подставляются</span></div>
            <div className="grid2">
              <Stepper label="Встречные" value={mode === 'no' ? backward : 0} disabled={mode !== 'no'}
                onChange={(v) => setLanes(forward, v, mode)} />
              <Stepper label={mode === 'no' ? 'Попутные' : 'Полосы'} value={forward} onChange={(v) => setLanes(v, backward, mode)} />
            </div>
            <div className="lanes" aria-label="Схема полос">
              {[...bwTurns].reverse().map((g, i) => <div key={`b${i}`} className="lane back"><span>{g}</span><small>B{bwTurns.length - i}</small></div>)}
              {fwTurns.map((g, i) => <div key={`f${i}`} className={`lane ${i === 0 && bwTurns.length ? 'center' : ''}`}><span>{g}</span><small>F{i + 1}</small></div>)}
            </div>
          </div>

          <div className="field"><span>Разрешённая скорость, км/ч</span>
            <div className="chips" role="group" aria-label="Скорость">
              {SPEEDS.map((s) => (
                <button key={s} aria-pressed={tags.maxspeed === String(s)} onClick={() => setTags({ maxspeed: String(s) })}>{s}</button>
              ))}
            </div>
          </div>

          <div className="field"><span>Доступ для автомобилей</span>
            <div className="seg" role="group" aria-label="Доступ">
              {([['', 'Открыт'], ['destination', 'Только к цели'], ['no', 'Запрещён']] as const).map(([v, label]) => (
                <button key={label} aria-pressed={(tags.motor_vehicle ?? '') === v}
                  onClick={() => setTags({ motor_vehicle: v || null }, 'Изменён доступ')}>{label}</button>
              ))}
            </div>
          </div>
          <div className="grid2">
            <label className="check card">
              <input type="checkbox" checked={tags.overtaking === 'no'}
                onChange={() => setTags({ overtaking: tags.overtaking === 'no' ? null : 'no' }, 'Изменён обгон')} />
              <span>Обгон запрещён<br /><small className="muted">overtaking=no</small></span>
            </label>
            <label className="check card">
              <input type="checkbox" checked={tags.priority_road === 'designated'}
                onChange={() => setTags({ priority_road: tags.priority_road === 'designated' ? null : 'designated' }, 'Изменён приоритет')} />
              <span>Главная дорога<br /><small className="muted">priority_road</small></span>
            </label>
          </div>
        </>
      )}

      <label className="check card">
        <input type="checkbox" checked={bridge}
          onChange={() => setTags(bridge ? { bridge: null, layer: null } : { bridge: 'yes', layer: tags.layer ?? '1' }, bridge ? 'Убран мост' : 'Задан мост')} />
        <span><b>Мост / эстакада</b><br /><small className="muted">bridge=yes, layer — уровень Z для Webots</small></span>
      </label>
      {bridge && (
        <div className="field"><span>Уровень (layer)</span>
          <div className="chips">
            {['1', '2', '3'].map((l) => <button key={l} aria-pressed={tags.layer === l} onClick={() => setTags({ layer: l })}>{l}</button>)}
          </div>
        </div>
      )}
      {(bridge || (tags.tunnel !== undefined && tags.tunnel !== 'no')) && (
        <button className="btn" onClick={() => onOp({ op: 'smooth_z', args: { way_id: way.id } }, 'Плавный Z-переход')}
          title="Высота ele = layer × 5 м на мосту и плавный съезд 60 м с каждой стороны">
          Плавный Z-переход (съезды по 60 м)
        </button>
      )}
    </div>
  );
}

function NodeAttributes({ tags, setTags }: { tags: Tags; setTags: (p: Record<string, string | null>, label?: string) => void }) {
  const presets: { label: string; tags: Record<string, string | null> }[] = [
    { label: 'Светофор', tags: { highway: 'traffic_signals' } },
    { label: 'Переход', tags: { highway: 'crossing', crossing: 'marked' } },
    { label: 'Переход со светофором', tags: { highway: 'crossing', crossing: 'traffic_signals' } },
    { label: 'Уступи дорогу', tags: { highway: 'give_way' } },
    { label: 'Стоп', tags: { highway: 'stop' } },
    { label: 'Обычный узел', tags: { highway: null, crossing: null } },
  ];
  return (
    <div className="stack16">
      <div className="field"><span>Тип узла</span>
        <div className="preset-grid">
          {presets.map((p) => (
            <button key={p.label} className="btn" aria-pressed={Object.entries(p.tags).every(([k, v]) => (tags[k] ?? null) === v)}
              onClick={() => setTags(p.tags, p.label)}>{p.label}</button>
          ))}
        </div>
      </div>
      {tags.highway === 'traffic_signals' && (
        <div className="field"><span>traffic_signals:direction</span>
          <div className="chips">
            {['forward', 'backward', 'both'].map((d) => (
              <button key={d} aria-pressed={tags['traffic_signals:direction'] === d}
                onClick={() => setTags({ 'traffic_signals:direction': d })}>{d}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function NameField({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [v, setV] = useState(value);
  const [prev, setPrev] = useState(value);
  if (prev !== value) { setPrev(value); setV(value); }
  return (
    <label className="field"><span>Название</span>
      <input value={v} onChange={(e) => setV(e.target.value)} onBlur={() => v !== value && onCommit(v)}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />
    </label>
  );
}

function Stepper({ label, value, onChange, disabled, max = 6 }: {
  label: string; value: number; onChange: (v: number) => void; disabled?: boolean; max?: number;
}) {
  return (
    <div className="field"><span>{label}</span>
      <div className={`stepper ${disabled ? 'disabled' : ''}`}>
        <button aria-label={`${label}: меньше`} disabled={disabled || value <= 1} onClick={() => onChange(value - 1)}>−</button>
        <span className="mono">{value}</span>
        <button aria-label={`${label}: больше`} disabled={disabled || value >= max} onClick={() => onChange(value + 1)}>+</button>
      </div>
    </div>
  );
}
