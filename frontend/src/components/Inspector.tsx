import { PanelClose } from './PanelClose';
import { NODE_PRESETS, presetPatch, isPreset } from '../utils/nodePresets';
import { memo, useState } from 'react';
import type { LoadRow, NodeProps, Op, RouteDetail, Tags, WayProps } from '../types';
import { SignalPanel, StopPanel } from './Transit';
import AdapterPanel from './AdapterPanel';
import TagEditor from './TagEditor';
import { RoadLoad } from './Analysis';
import { areaM2, fmt, lengthM } from '../utils/geom';
import { HIGHWAY_CLASSES, ROAD_PERMISSIONS, SPEEDS, SURFACES, laneCounts, onewayMode, turnGlyphs, type OnewayMode } from '../utils/osm';

interface Props {
  way?: WayProps;
  node?: NodeProps & { lon: number; lat: number };
  /** line coordinates of the way, or the outer ring of an area */
  geometry?: number[][];
  loadRow?: LoadRow;
  onSetLoad?: (wayId: number, volume: number | null) => void;
  /** the selected node is a vertex of a drivable way */
  nodeOnRoad?: boolean;
  /** the selected node is a shared junction that can be pulled apart */
  canSeparate?: boolean;
  /** shared junctions on the selected road, drawn as orange points */
  sharedJunctions?: number;
  /** for the signal and stop panels */
  docId?: string;
  rev?: number;
  openRoute?: RouteDetail | null;
  onOpenRoute?: (r: RouteDetail | null) => void;
  onScenarioChanged?: () => void;
  /** the selected road is closed in the scenario (accident, works) */
  closed?: boolean;
  onClosure?: (wayId: number, closed: boolean) => void;
  onObjectReport?: (kind: string, id: number, fmt: 'xlsx' | 'pdf') => void;
  /** apply building tags to every building of a type that has no storeys */
  onAutofill?: (buildingType: string, tags: Record<string, string>) => void;
  /** analytics mode: show information, hide editing */
  readOnly?: boolean;
  onOp: (op: Op | Op[], label?: string) => void;
}

const BUILDING_TYPES: [string, string][] = [
  ['yes', 'здание'], ['apartments', 'многоквартирный'], ['house', 'частный дом'], ['residential', 'жилое'],
  ['commercial', 'коммерческое'], ['retail', 'магазин'], ['office', 'офис'], ['school', 'школа'],
  ['university', 'университет'], ['hospital', 'больница'], ['industrial', 'промышленное'], ['garage', 'гараж'],
];

export default memo(function Inspector({
  way, node, geometry, loadRow, onSetLoad, nodeOnRoad, canSeparate, sharedJunctions, docId, rev = 0, openRoute = null,
  onOpenRoute, onScenarioChanged, onOp, closed = false, onClosure, onObjectReport, onAutofill, readOnly = false,
}: Props) {
  const [selectedTab, setTab] = useState<'attr' | 'tags' | 'adapters'>('attr');
  const showAdapters = way?.layer === 'road' && Boolean(docId);
  const tab = selectedTab === 'adapters' && !showAdapters ? 'attr' : selectedTab;
  if (!way && !node) {
    return (
      <aside className="inspector empty" aria-label="Свойства объекта">
        <PanelClose panel="right" /><p>Выберите ребро или узел на карте.</p>
        <p className="muted">V — выбор, W — новое ребро, N — узел, S — разделить, M — соединить, T — светофор, P — переход.</p>
        <p className="muted">+ / − — масштаб, 0 — весь участок, F — полный экран (выход — снова F). Рисование ребра: Enter — готово, Esc или правая кнопка мыши — отменить, ⌫ — шаг назад.</p>
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
      <PanelClose panel="right" /><div className="insp-head">
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
        {showAdapters && <button role="tab" aria-selected={tab === 'adapters'} onClick={() => setTab('adapters')}>
          Как увидят адаптеры
        </button>}
      </div>
      <div className={`insp-body ${readOnly ? 'read-only' : ''}`}>
        {readOnly && <p className="small muted">Режим аналитики: объект только для просмотра. Править — в режиме «Редактирование».</p>}
        <label className="check card planned-check">
          <input type="checkbox" disabled={readOnly} checked={tags['uds:planned'] === 'yes'}
            onChange={() => setTags({ 'uds:planned': tags['uds:planned'] === 'yes' ? null : 'yes' },
              tags['uds:planned'] === 'yes' ? 'Объект построен' : 'Объект отмечен как планируемый')} />
          <span><b>Планируемый объект</b><br /><small className="muted">ещё не построен: рисуется полупрозрачно с пунктиром (тег uds:planned)</small></span>
        </label>
        {way && way.layer === 'road' && onClosure && (
          <div className={`card stack6 ${closed ? 'warn-card' : ''}`}>
            <div className="row between">
              <b>{closed ? 'Дорога перекрыта' : 'Сценарий ДТП / перекрытия'}</b>
              <button className={`btn small ${closed ? 'primary' : 'danger'}`} onClick={() => onClosure(way.id, !closed)}>
                {closed ? 'Открыть движение' : 'Перекрыть'}
              </button>
            </div>
            <span className="small muted">Поток уходит на кратчайший объезд; в .osm перекрытие не записывается.</span>
          </div>
        )}
        {tab === 'adapters' && docId && way && <AdapterPanel key={`${docId}:${way.id}:${rev}`} docId={docId} wayId={way.id} />}
        {tab === 'tags' && <TagEditor tags={tags} onChange={(p) => setTags(p)} />}
        {tab === 'attr' && way && (way.layer === 'building' || way.layer === 'area')
          && <AreaAttributes way={way} tags={tags} setTags={setTags} area={geometry ? areaM2(geometry) : 0} onAutofill={onAutofill} />}
        {tab === 'attr' && way && way.layer !== 'building' && way.layer !== 'area' && (
          <>
            <WayAttributes way={way} tags={tags} setTags={setTags} onOp={onOp} sharedJunctions={sharedJunctions} />
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
        {tab === 'attr' && node && docId && (tags.highway === 'traffic_signals' || tags.crossing === 'traffic_signals') && (
          <SignalPanel docId={docId} nodeId={node.id} rev={rev} onChanged={() => onScenarioChanged?.()} />
        )}
        {tab === 'attr' && node && docId && (tags.highway === 'bus_stop' || tags.public_transport === 'platform'
          || tags.public_transport === 'stop_position' || tags.railway === 'tram_stop') && (
          <StopPanel docId={docId} nodeId={node.id} rev={rev} openRoute={openRoute}
            onOpenRoute={(r) => onOpenRoute?.(r)} onChanged={() => onScenarioChanged?.()} />
        )}
        {tab === 'attr' && node && <NodeAttributes tags={tags} setTags={setTags} stopInRoute={!!node.stop_routes?.length || !!openRoute?.stop_list.some((st) => st.id === node.id)} />}
      </div>
      <div className="insp-foot wrap">
        {onObjectReport && (
          <>
            <button className="btn small" onClick={() => onObjectReport(kind, id, 'pdf')} title="Отчёт по объекту: характеристики, графики">Отчёт PDF</button>
            <button className="btn small" onClick={() => onObjectReport(kind, id, 'xlsx')} title="Цифры и график в Excel">Excel</button>
          </>
        )}
        {!readOnly && node && canSeparate && (
          <button className="btn" onClick={() => onOp({ op: 'separate_ways', args: { node_id: node.id } }, 'Дороги разъединены')}>
            Разделить дороги
          </button>
        )}
        {!readOnly && way && way.layer !== 'building' && way.layer !== 'area' && (
          <button className="btn" onClick={() => onOp({ op: 'reverse_way', args: { id } })}>Развернуть</button>
        )}
        {!readOnly && <button className="btn danger" onClick={() => onOp({ op: 'delete', args: { kind, id } })}>Удалить</button>}
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

const BUILDING_TEMPLATES: { name: string; tags: Record<string, string> }[] = [
  { name: 'Хрущёвка 5 эт.', tags: { building: 'apartments', 'building:levels': '5', 'building:flats': '80' } },
  { name: 'Панель 9 эт.', tags: { building: 'apartments', 'building:levels': '9', 'building:flats': '144' } },
  { name: 'Монолит 17 эт.', tags: { building: 'apartments', 'building:levels': '17', 'building:flats': '272' } },
  { name: 'Частный дом', tags: { building: 'house', 'building:levels': '1', 'building:flats': '1' } },
  { name: 'Офисный центр', tags: { building: 'office', 'building:levels': '8' } },
  { name: 'Школа', tags: { building: 'school', 'building:levels': '3' } },
];
const PERSONS_PER_FLAT = 2.5;

function AreaAttributes({ way, tags, setTags, area, onAutofill }: {
  onAutofill?: (buildingType: string, tags: Record<string, string>) => void;
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
        <div className="field"><span>Шаблон здания</span>
          <div className="preset-grid">
            {BUILDING_TEMPLATES.map((t) => (
              <button key={t.name} className="btn small" onClick={() => setTags(t.tags, `Шаблон «${t.name}»`)}>{t.name}</button>
            ))}
          </div>
          {onAutofill && tags['building:levels'] && (
            <button className="btn small" title="Записать этажность и квартиры этого здания всем зданиям того же типа без этажности"
              onClick={() => onAutofill(tags.building ?? 'yes', {
                'building:levels': tags['building:levels'], ...(tags['building:flats'] ? { 'building:flats': tags['building:flats'] } : {}) })}>
              Заполнить так же все «{tags.building ?? 'yes'}» без этажности
            </button>
          )}
          {tags['building:flats'] && (
            <span className="small muted">Население ≈ {Math.round(Number(tags['building:flats']) * PERSONS_PER_FLAT)} чел. ({PERSONS_PER_FLAT} на квартиру)</span>
          )}
        </div>
      )}
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
      <div className="field"><span>Порядок над соседями (OSM layer = {tags.layer ?? 0})</span>
        <div className="grid2">
          <button className="btn" onClick={() => setTags({ layer: zStep(tags.layer, 1) }, 'Объект поднят')}>▲ Выше</button>
          <button className="btn" onClick={() => setTags({ layer: zStep(tags.layer, -1) }, 'Объект опущен')}>▼ Ниже</button>
        </div>
      </div>
      <p className="muted small">Контур можно перетащить целиком или за одну вершину инструментом «Выбор».</p>
    </div>
  );
}

function nodeTitle(tags: Tags) {
  if (tags.highway === 'traffic_signals') return 'Светофор';
  if (tags.highway === 'crossing') return 'Пешеходный переход';
  if (tags.highway === 'bus_stop') return 'Остановка';
  return 'Узел';
}

export function WayAttributes({ way, tags, setTags, onOp, sharedJunctions }: {
  way: WayProps; tags: Tags; sharedJunctions?: number;
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
        <label className="field"><span>Классификация дороги</span>
          <select value={tags.highway ?? ''} onChange={(e) => setTags({ highway: e.target.value || null })}>
            <option value="">не указано</option>
            {HIGHWAY_CLASSES.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
            {tags.highway && !HIGHWAY_CLASSES.some((h) => h.value === tags.highway) && <option value={tags.highway}>{tags.highway}</option>}
          </select>
        </label>
        <label className="field"><span>Покрытие</span>
          <select value={tags.surface ?? ''} onChange={(e) => setTags({ surface: e.target.value || null })}>
            <option value="">—</option>
            {SURFACES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            {tags.surface && !SURFACES.some((s) => s.value === tags.surface) && <option value={tags.surface}>{tags.surface}</option>}
          </select>
        </label>
      </div>
      <NameField value={tags.name ?? ''} onCommit={(v) => setTags({ name: v || null })} />
      {!!sharedJunctions && (
        <p className="muted small">Оранжевая точка на общем узле разделяет соединённые дороги. Линию можно перетащить целиком.</p>
      )}

      {(way.layer === 'road' || way.layer === 'path') && (
        <div className="field">
          <span>Разрешения</span>
          <div className="perm-list" role="group" aria-label="Разрешения">
            {ROAD_PERMISSIONS.map((p) => {
              const on = way.permissions[p.key];
              return (
                <label key={p.key} className="check">
                  <input type="checkbox" checked={on}
                    onChange={() => setTags({ [p.key]: on ? 'no' : 'yes' }, `Разрешение: ${p.label}`)} />
                  <span>{p.label}<br /><small className="muted">{p.key}</small></span>
                </label>
              );
            })}
          </div>
        </div>
      )}

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
              {([['', 'Открыт'], ['destination', 'Служебные машины'], ['no', 'Запрещён']] as const).map(([v, label]) => (
                <button key={label} title={`motor_vehicle=${v || 'yes'}`} aria-pressed={(tags.motor_vehicle ?? '') === v}
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

function NodeAttributes({ tags, setTags, stopInRoute }: { tags: Tags; stopInRoute: boolean; setTags: (p: Record<string, string | null>, label?: string) => void }) {
  return <div className="stack16"><div className="field"><span>Тип узла</span>
    <div className="preset-grid">{NODE_PRESETS.map((p) => <button key={p.label} className="btn" aria-pressed={isPreset(tags, p)}
      onClick={() => {
        if (p.label !== 'Остановка' && (stopInRoute || tags.public_transport) && !window.confirm('Узел может входить в маршрут как остановка. Снять теги остановки?')) return;
        setTags(presetPatch(tags, p), `Тип узла: ${p.label}`);
      }}>{p.label}</button>)}</div></div>
      {tags.highway === 'traffic_signals' && <label className="field">Дополнительная секция
        <select value={tags['uds:signal:arrow'] ?? ''} onChange={(e) => setTags({ 'uds:signal:arrow': e.target.value || null })}>
          <option value="">Обычный</option><option value="left">Стрелка налево</option><option value="right">Стрелка направо</option><option value="through">Стрелка прямо</option>
        </select></label>}
      {tags['uds:signal:tram'] === 'yes' && <p className="muted small">Трамвайный светофор. Схему сигналов сверяйте с ГОСТ Р 52289-2019.</p>}
    </div>;
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

/** next OSM layer value for a zone or building; 0 removes the tag */
function zStep(layer: string | undefined, delta: number): string | null {
  const cur = parseInt(layer ?? '0', 10) || 0;
  const next = Math.max(-5, Math.min(5, cur + delta));
  return next === 0 ? null : String(next);
}
