import { useEffect, useState } from 'react';
import type { Tags } from '../types';
import { KEY_SUGGESTIONS, VALUE_SUGGESTIONS } from '../utils/osm';
import { choicesFor, keyRu, valueRu } from '../utils/tagNames';

interface Props {
  tags: Tags;
  original?: Tags;
  onChange: (patch: Record<string, string | null>) => void;
}

/**
 * Tag editor with Russian names: «Покрытие: асфальт» instead of surface=asphalt.
 * The OSM key stays visible in small print and is what gets saved; yes/no and
 * other known values are picked from a dropdown («да / нет»), free text stays a
 * text field, and «✎» switches any row to raw editing.
 */
export default function TagEditor({ tags, onChange }: Props) {
  const [newKey, setNewKey] = useState('');
  const [newVal, setNewVal] = useState('');
  const [draft, setDraft] = useState<Tags>(tags);
  const [raw, setRaw] = useState<Set<string>>(new Set());
  useEffect(() => setDraft(tags), [tags]);

  const commit = (k: string) => {
    if (draft[k] !== tags[k]) onChange({ [k]: draft[k] === '' ? null : draft[k] });
  };
  const add = () => {
    const k = newKey.trim();
    if (!k || !newVal.trim()) return;
    onChange({ [k]: newVal.trim() });
    setNewKey('');
    setNewVal('');
  };
  const toggleRaw = (k: string) => setRaw((prev) => {
    const next = new Set(prev);
    if (next.has(k)) next.delete(k); else next.add(k);
    return next;
  });

  return (
    <div className="tags">
      <datalist id="osm-keys">
        {KEY_SUGGESTIONS.map((k) => <option key={k} value={k} label={keyRu(k) ?? undefined} />)}
      </datalist>
      <datalist id="osm-values">
        {(VALUE_SUGGESTIONS[newKey] ?? []).map((v) => <option key={v} value={v} label={valueRu(newKey, v) ?? undefined} />)}
      </datalist>
      {Object.keys(tags).map((k) => {
        const choices = raw.has(k) ? null : choicesFor(k, tags[k], VALUE_SUGGESTIONS[k]);
        const ru = keyRu(k);
        return (
          <div className="tag-row" key={k}>
            <span className="tag-key" title={`OSM: ${k}`}>
              {ru ?? k}
              {ru && <small className="tag-osm mono">{k}</small>}
            </span>
            {choices ? (
              <select aria-label={`Значение: ${ru ?? k}`} value={tags[k]}
                onChange={(e) => onChange({ [k]: e.target.value })}>
                {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            ) : (
              <input
                aria-label={`Значение: ${ru ?? k}`}
                value={draft[k] ?? ''}
                list={`osm-values-${k}`}
                title={valueRu(k, draft[k] ?? '') ?? undefined}
                onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                onBlur={() => commit(k)}
                onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
              />
            )}
            <datalist id={`osm-values-${k}`}>
              {(VALUE_SUGGESTIONS[k] ?? []).map((v) => <option key={v} value={v} label={valueRu(k, v) ?? undefined} />)}
            </datalist>
            <button className="icon-btn" aria-label={`Редактировать значение ${k} как текст`}
              title={raw.has(k) ? 'Выбор из списка' : 'Ввести значение вручную'} onClick={() => toggleRaw(k)}>✎</button>
            <button className="icon-btn" aria-label={`Удалить тег ${k}`} title="Удалить тег" onClick={() => onChange({ [k]: null })}>×</button>
          </div>
        );
      })}
      <div className="tag-row tag-new">
        <input aria-label="Новый ключ" placeholder="ключ (можно искать по-русски)" list="osm-keys" value={newKey}
          onChange={(e) => setNewKey(e.target.value)} />
        <input aria-label="Новое значение" placeholder="значение" list="osm-values" value={newVal}
          onChange={(e) => setNewVal(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') add(); }} />
        <button className="icon-btn" aria-label="Добавить тег" title="Добавить тег" onClick={add}>+</button>
      </div>
      {newKey && keyRu(newKey) && <p className="small muted pad0">{keyRu(newKey)}</p>}
    </div>
  );
}
