import { useState } from 'react';

const KEY = 'uds-hint-collapsed';

function stored(): boolean {
  try { return localStorage.getItem(KEY) === '1'; } catch { return false; }
}

/** How to use the current tool. It folds into a small «i» button and unfolds from it. */
export function MapHint({ text }: { text: string }) {
  const [collapsed, setCollapsed] = useState(stored);
  const set = (v: boolean) => {
    setCollapsed(v);
    try { localStorage.setItem(KEY, v ? '1' : '0'); } catch { /* storage blocked: the choice lasts this session */ }
  };
  if (collapsed) {
    return (
      <button type="button" className="map-hint-icon" onClick={() => set(false)}
        aria-label="Показать подсказку к инструменту" title={text}>i</button>
    );
  }
  return (
    <div className="map-hint" role="note">
      <span>{text}</span>
      <button type="button" className="map-hint-fold" onClick={() => set(true)} aria-label="Свернуть подсказку" title="Свернуть">−</button>
    </div>
  );
}
