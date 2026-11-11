import { useEffect, useRef, useState } from 'react';

const SHOW_MS = 5000;
const FADE_MS = 600;

/** A notification that fades out on its own after 5 s; hovering keeps it on screen. */
export function Toast({ kind, text, onClose }: { kind: 'notice' | 'error'; text: string; onClose: () => void }) {
  const [leaving, setLeaving] = useState(false);
  const [hover, setHover] = useState(false);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    setLeaving(false);
    if (hover) return;
    const fade = window.setTimeout(() => setLeaving(true), SHOW_MS);
    const gone = window.setTimeout(() => close.current(), SHOW_MS + FADE_MS);
    return () => { window.clearTimeout(fade); window.clearTimeout(gone); };
  }, [text, hover]);
  return (
    <div className={`toast ${kind === 'notice' ? 'notice' : 'error'}${leaving ? ' leaving' : ''}`}
      role={kind === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      {text}
      <button className="icon-btn" aria-label="Закрыть" onClick={() => close.current()}>×</button>
    </div>
  );
}
