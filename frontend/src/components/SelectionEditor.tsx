import { PanelClose } from './PanelClose';
import { useState } from 'react';
import type { Fix, Geo, Issue, MultiSelection, Op } from '../types';
import { TEMPLATES, templateOps } from '../utils/osm';
export interface Segment {
  way_id: number;
  start: { lat: number; lon: number };
  end: { lat: number; lon: number };
}
export default function SelectionEditor({
  multi,
  segment,
  geo,
  onOp,
  onFix,
  onClear,
}: {
  multi: MultiSelection;
  segment: Segment | null;
  geo: Geo;
  onOp: (ops: Op | Op[], label?: string) => void;
  onFix: (i: Issue, f: Fix) => void;
  onClear: () => void;
}) {
  const [key, setKey] = useState('lanes'),
    [value, setValue] = useState('3');
  const common = multi.length
    ? Object.keys(
        (multi[0].kind === 'way'
          ? geo.ways.features.find((f) => f.properties.id === multi[0].id)
          : geo.nodes.features.find((f) => f.properties.id === multi[0].id)
        )?.properties.tags ?? {},
      ).filter((k) =>
        multi.every((ref) => {
          const tags = (
            ref.kind === 'way'
              ? geo.ways.features.find((f) => f.properties.id === ref.id)
              : geo.nodes.features.find((f) => f.properties.id === ref.id)
          )?.properties.tags;
          return tags && k in tags;
        }),
      )
    : [];
  const apply = () => {
    if (segment) {
      const fix = {
        label: 'Изменить участок (ребро будет разделено до 3 частей)',
        op: 'edit_segment',
        args: { ...segment, tags: { [key]: value || null } },
      };
      onFix(
        {
          id: 'segment-edit',
          severity: 'info',
          category: 'data',
          title: 'Редактирование участка',
          description:
            'Разделить ребро в выбранных точках и изменить только среднюю часть; отношения обновятся.',
          refs: [{ kind: 'way', id: segment.way_id }],
          location: null,
          fix,
          fixes: [fix],
          levels: [],
        },
        fix,
      );
    } else
      onOp(
        multi.map((ref) => ({ op: 'set_tags', args: { ...ref, tags: { [key]: value || null } } })),
        'Теги выбранных объектов',
      );
  };
  return (
    <aside className="inspector">
      <PanelClose panel="right" />
      <div className="insp-head">
        <h2>{segment ? 'Участок ребра' : `Выбрано: ${multi.length}`}</h2>
        <button className="btn small" onClick={onClear}>
          Сбросить выбор
        </button>
      </div>
      <div className="insp-body stack10">
        <p className="small">
          {segment
            ? 'Перед применением проверьте разделение на карте.'
            : `Общие ключи: ${common.join(', ') || 'нет'}`}
        </p>
        <label>
          Тег <input value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        <label>
          Значение (пусто — удалить)
          <input value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <button className="btn primary" disabled={!key.trim()} onClick={apply}>
          {segment ? 'Предпросмотр' : 'Задать теги'}
        </button>
        {!segment && (
          <>
            <label>
              Шаблон дороги
              <select
                defaultValue=""
                onChange={(e) => {
                  const t = TEMPLATES.find((t) => t.name === e.target.value);
                  if (t)
                    onOp(
                      multi.filter((r) => r.kind === 'way').flatMap((r) => templateOps(r.id, t)),
                      'Шаблон для выбранных рёбер',
                    );
                }}
              >
                <option value="">Выберите…</option>
                {TEMPLATES.map((t) => (
                  <option key={t.name}>{t.name}</option>
                ))}
              </select>
            </label>
            <button
              className="btn danger"
              onClick={() => {
                if (window.confirm(`Удалить ${multi.length} объектов?`))
                  onOp(
                    multi.map((r) => ({ op: 'delete', args: r })),
                    'Удалить выбранные объекты',
                  );
              }}
            >
              Удалить выбранные
            </button>
            <p className="muted small">
              Перетащите выбранный объект, чтобы сдвинуть группу. Одна отмена возвращает всю правку.
            </p>
          </>
        )}
      </div>
    </aside>
  );
}
