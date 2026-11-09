export interface KeyInfo {
  key: string;
  code: string;
  repeat?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  keyCode?: number;
}

/**
 * F toggles fullscreen. The same key leaves it.
 * `code` is the physical key, so a Russian layout (ФЫВА, where F types «а»)
 * still matches. `key` covers browsers that omit `code` (older iOS).
 * Escape is not a fullscreen key: it only cancels the current editor action.
 */
export function isFullscreenToggle(e: KeyInfo): boolean {
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return false;
  if (e.code === 'KeyF') return true;
  const k = e.key.toLowerCase();
  return k === 'f' || k === 'а';
}

export function isEscapeKey(e: KeyInfo): boolean {
  return e.key === 'Escape' || e.key === 'Esc' || e.code === 'Escape' || e.keyCode === 27;
}
