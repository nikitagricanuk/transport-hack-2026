import { useEffect, useRef } from 'react';

/** Text fields keep Enter for themselves (a name, a tag value). */
function typingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  if (el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable) return true;
  if (el.tagName !== 'INPUT') return false;
  const type = (el as HTMLInputElement).type;
  return !['checkbox', 'radio', 'button', 'submit', 'range'].includes(type);
}

/**
 * Enter confirms: while `enabled`, pressing Enter anywhere except a text field calls
 * `onEnter` (apply a reviewed fix, save the checked export).
 */
export function useEnterKey(enabled: boolean, onEnter: () => void): void {
  const fn = useRef(onEnter);
  fn.current = onEnter;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || e.repeat || e.ctrlKey || e.metaKey || e.altKey || typingTarget(e.target)) return;
      e.preventDefault();
      fn.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled]);
}
