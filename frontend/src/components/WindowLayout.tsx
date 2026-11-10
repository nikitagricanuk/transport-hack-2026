import {
  useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as RKeyboardEvent,
  type PointerEvent as RPointerEvent, type ReactNode,
} from 'react';

/** Panels the «Окно» menu can show or hide; sizes are dragged with the splitters. */
export type PanelKey = 'tools' | 'left' | 'right' | 'bottom';

export interface Layout {
  visible: Record<PanelKey, boolean>;
  /** px: width of the layers panel and the inspector, height of the bottom panel */
  size: { left: number; right: number; bottom: number };
}

const STORAGE_KEY = 'uds-editor-layout';
export const DEFAULT_LAYOUT: Layout = {
  visible: { tools: true, left: true, right: true, bottom: true },
  size: { left: 248, right: 340, bottom: 260 },
};
export const LIMITS = { left: [180, 520], right: [260, 640], bottom: [120, 640] } as const;

export const PANEL_NAMES: Record<PanelKey, string> = {
  tools: 'Инструменты', left: 'Слои и подложка', right: 'Свойства объекта', bottom: 'Проблемы и история',
};

const clamp = (k: keyof Layout['size'], v: number) => Math.round(Math.min(LIMITS[k][1], Math.max(LIMITS[k][0], v)));

function load(): Layout {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    if (!raw) return DEFAULT_LAYOUT;
    return {
      visible: { ...DEFAULT_LAYOUT.visible, ...raw.visible },
      size: {
        left: clamp('left', raw.size?.left ?? DEFAULT_LAYOUT.size.left),
        right: clamp('right', raw.size?.right ?? DEFAULT_LAYOUT.size.right),
        bottom: clamp('bottom', raw.size?.bottom ?? DEFAULT_LAYOUT.size.bottom),
      },
    };
  } catch {
    return DEFAULT_LAYOUT;
  }
}

/** Window layout of this viewer, remembered in the browser. */
export function useLayout() {
  const [layout, setLayout] = useState<Layout>(load);
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout)); } catch { /* storage blocked */ }
  }, [layout]);
  const toggle = useCallback((k: PanelKey) =>
    setLayout((l) => ({ ...l, visible: { ...l.visible, [k]: !l.visible[k] } })), []);
  const resize = useCallback((k: keyof Layout['size'], v: number) =>
    setLayout((l) => ({ ...l, size: { ...l.size, [k]: clamp(k, v) } })), []);
  const reset = useCallback(() => setLayout(DEFAULT_LAYOUT), []);
  /** CSS variables the stylesheet reads for panel sizes */
  const style = {
    '--left-w': `${layout.size.left}px`, '--right-w': `${layout.size.right}px`, '--bottom-h': `${layout.size.bottom}px`,
  } as CSSProperties;
  return { layout, toggle, resize, reset, style };
}

/**
 * Drag handle at the inner edge of a panel: dragging toward the panel shrinks it,
 * away from it grows it (arrow keys work too, Shift for bigger steps).
 * Double click resets that panel to its default size.
 */
export function Splitter({ panel, value, onResize, label }: {
  panel: keyof Layout['size'];
  value: number;
  onResize: (v: number) => void;
  label: string;
}) {
  const start = useRef<{ pos: number; value: number } | null>(null);
  const vertical = panel === 'bottom';
  // left panel grows when dragged right; inspector and bottom panel grow when dragged left / up
  const sign = panel === 'left' ? 1 : -1;
  const onPointerDown = (e: RPointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    start.current = { pos: vertical ? e.clientY : e.clientX, value };
  };
  const onPointerMove = (e: RPointerEvent) => {
    if (!start.current) return;
    const d = (vertical ? e.clientY : e.clientX) - start.current.pos;
    onResize(start.current.value + sign * d);
  };
  const end = () => { start.current = null; };
  const onKeyDown = (e: RKeyboardEvent) => {
    const step = e.shiftKey ? 40 : 10;
    const grow = vertical ? e.key === 'ArrowUp' : (panel === 'left' ? e.key === 'ArrowRight' : e.key === 'ArrowLeft');
    const shrink = vertical ? e.key === 'ArrowDown' : (panel === 'left' ? e.key === 'ArrowLeft' : e.key === 'ArrowRight');
    if (grow || shrink) { e.preventDefault(); onResize(value + (grow ? step : -step)); }
  };
  return (
    <div className={`splitter ${vertical ? 'horizontal' : 'vertical'}`} role="separator" tabIndex={0}
      aria-orientation={vertical ? 'horizontal' : 'vertical'} aria-label={label}
      aria-valuenow={value} aria-valuemin={LIMITS[panel][0]} aria-valuemax={LIMITS[panel][1]}
      title={`${label}: перетащите, чтобы изменить размер · двойной клик — по умолчанию`}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={end} onPointerCancel={end}
      onDoubleClick={() => onResize(DEFAULT_LAYOUT.size[panel])} onKeyDown={onKeyDown} />
  );
}

/** Menu bar at the very top. «Окно» shows or hides panels and resets their sizes. */
export function MenuBar({ layout, onToggle, onReset, extra }: {
  layout: Layout;
  onToggle: (k: PanelKey) => void;
  onReset: () => void;
  /** more menus, e.g. «Файл» */
  extra?: ReactNode;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(null); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(null); };
    window.addEventListener('mousedown', close);
    window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('mousedown', close); window.removeEventListener('keydown', esc); };
  }, [open]);
  return (
    <nav className="menubar" ref={ref} aria-label="Меню">
      {extra}
      <div className="menu">
        <button className="menu-title" aria-haspopup="menu" aria-expanded={open === 'window'}
          onClick={() => setOpen(open === 'window' ? null : 'window')}>Окно</button>
        {open === 'window' && (
          <div className="menu-list" role="menu">
            {(Object.keys(PANEL_NAMES) as PanelKey[]).map((k) => (
              <button key={k} role="menuitemcheckbox" aria-checked={layout.visible[k]} className="menu-item"
                onClick={() => onToggle(k)}>
                <span className="menu-check" aria-hidden="true">{layout.visible[k] ? '✓' : ''}</span>
                {PANEL_NAMES[k]}
              </button>
            ))}
            <div className="menu-sep" role="separator" />
            <button role="menuitem" className="menu-item" onClick={() => { onReset(); setOpen(null); }}>
              <span className="menu-check" aria-hidden="true" />Сбросить расположение панелей
            </button>
          </div>
        )}
      </div>
      <span className="muted small menubar-hint">Границы панелей можно перетаскивать</span>
    </nav>
  );
}
