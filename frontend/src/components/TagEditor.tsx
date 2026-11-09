import { useEffect, useState } from 'react';
import type { Tags } from '../types';
import { KEY_SUGGESTIONS, VALUE_SUGGESTIONS } from '../utils/osm';

interface Props {
  tags: Tags;
  original?: Tags;
  onChange: (patch: Record<string, string | null>) => void;
}

/** Raw key=value editor with autocomplete (datalist) for keys and values. */
export default function TagEditor({ tags, onChange }: Props) {
  const [newKey, setNewKey] = useState('');
  const [newVal, setNewVal] = useState('');
  const [draft, setDraft] = useState<Tags>(tags);
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

  return (
    <div className="tags">
      <datalist id="osm-keys">{KEY_SUGGESTIONS.map((k) => <option key={k} value={k} />)}</datalist>
      <datalist id="osm-values">{(VALUE_SUGGESTIONS[newKey] ?? []).map((v) => <option key={v} value={v} />)}</datalist>
      {Object.keys(tags).map((k) => (
        <div className="tag-row" key={k}>
          <span className="tag-key" title={k}>{k}</span>
          <input
            aria-label={`Значение ${k}`}
            value={draft[k] ?? ''}
            list={`osm-values-${k}`}
            onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
            onBlur={() => commit(k)}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          />
          <datalist id={`osm-values-${k}`}>{(VALUE_SUGGESTIONS[k] ?? []).map((v) => <option key={v} value={v} />)}</datalist>
          <button className="icon-btn" aria-label={`Удалить тег ${k}`} title="Удалить тег" onClick={() => onChange({ [k]: null })}>×</button>
        </div>
      ))}
      <div className="tag-row tag-new">
        <input aria-label="Новый ключ" placeholder="ключ" list="osm-keys" value={newKey} onChange={(e) => setNewKey(e.target.value)} />
        <input aria-label="Новое значение" placeholder="значение" list="osm-values" value={newVal}
          onChange={(e) => setNewVal(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') add(); }} />
        <button className="icon-btn" aria-label="Добавить тег" title="Добавить тег" onClick={add}>+</button>
      </div>
    </div>
  );
}
