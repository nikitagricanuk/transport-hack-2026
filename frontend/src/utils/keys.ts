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

/**
 * Set for the rest of this keydown turn when the way draft consumes
 * Delete or Backspace. Cleared after the current dispatch, so the next
 * keypress can delete a selected object once the draft is gone.
 */
let draftPointKeyClaimed = false;

export function claimDraftPointKey(): void {
  draftPointKeyClaimed = true;
  queueMicrotask(() => {
    draftPointKeyClaimed = false;
  });
}

export function isDraftPointKeyClaimed(): boolean {
  return draftPointKeyClaimed;
}

/**
 * Global Delete/Backspace removes the selection. A way draft owns both keys
 * while it has points (and for the keypress that removes the last one):
 * they only drop a draft point. Backspace stays reserved for the way tool
 * even before the first point. Ctrl/Alt/Meta chords are not this shortcut.
 */
export function shouldDeleteSelection(
  e: KeyInfo,
  tool: string,
  draftPoints: number,
  draftOwnsKey = false,
): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey) return false;
  const pointKey = e.key === 'Delete' || e.key === 'Backspace';
  if (pointKey && (draftOwnsKey || (tool === 'way' && draftPoints > 0))) return false;
  if (e.key === 'Delete') return true;
  if (e.key === 'Backspace') return tool !== 'way';
  return false;
}
