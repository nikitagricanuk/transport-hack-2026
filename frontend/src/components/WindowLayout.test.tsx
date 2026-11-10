import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { DEFAULT_LAYOUT, LIMITS, MenuBar, Splitter, useLayout } from './WindowLayout.tsx';

let dom: JSDOM;
let root: Root;
let host: HTMLDivElement;

function EditorLayout() {
  const { layout, toggle, resize, reset, style } = useLayout();
  return <div style={style}>
    <MenuBar layout={layout} onToggle={toggle} onReset={reset} />
    {(['left', 'right', 'bottom'] as const).map((panel) => layout.visible[panel] &&
      <section key={panel} data-panel={panel}>
        <Splitter panel={panel} value={layout.size[panel]} onResize={(value) => resize(panel, value)} label={panel} />
      </section>)}
  </div>;
}

beforeEach(() => {
  dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://editor.test' });
  Object.assign(globalThis, { window: dom.window, document: dom.window.document,
    localStorage: dom.window.localStorage, IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); dom.window.close(); });
const mount = () => act(() => root.render(<EditorLayout />));
const click = (element: Element) => act(() => element.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
const splitter = (panel: string) => host.querySelector(`[data-panel="${panel}"] [role="separator"]`)!;
const size = (panel: string) => Number(splitter(panel).getAttribute('aria-valuenow'));
function key(panel: string, value: string, shiftKey = false) {
  act(() => splitter(panel).dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: value, shiftKey, bubbles: true })));
}
function pointer(element: Element, type: string, x: number, y = 0, button = 0) {
  const event = new dom.window.MouseEvent(type, { clientX: x, clientY: y, button, bubbles: true });
  Object.defineProperties(event, { pointerId: { value: 1 }, isPrimary: { value: true } });
  Object.assign(element, { setPointerCapture() {} });
  act(() => element.dispatchEvent(event));
}

test('window menu hides and restores panels, persists layout, and reset restores defaults', () => {
  mount();
  click(host.querySelector('.menu-title')!);
  const right = host.querySelectorAll('[role="menuitemcheckbox"]')[2];
  click(right);
  assert.equal(host.querySelector('[data-panel="right"]'), null);
  assert.equal(right.getAttribute('aria-checked'), 'false');
  assert.equal(JSON.parse(localStorage.getItem('uds-editor-layout')!).visible.right, false);
  click(right);
  assert.equal(size('right'), DEFAULT_LAYOUT.size.right);
  key('left', 'ArrowRight', true);
  click(host.querySelector('[role="menuitem"]')!);
  assert.equal(size('left'), DEFAULT_LAYOUT.size.left);
  assert.equal(host.querySelector('[role="menu"]'), null);
});

test('keyboard resizing uses the correct direction and clamps every panel', () => {
  mount();
  for (const [panel, grow, shrink] of [['left', 'ArrowRight', 'ArrowLeft'], ['right', 'ArrowLeft', 'ArrowRight'], ['bottom', 'ArrowUp', 'ArrowDown']] as const) {
    key(panel, grow);
    assert.equal(size(panel), DEFAULT_LAYOUT.size[panel] + 10);
    key(panel, shrink, true);
    assert.equal(size(panel), DEFAULT_LAYOUT.size[panel] - 30);
    for (let i = 0; i < 20; i++) key(panel, grow, true);
    assert.equal(size(panel), LIMITS[panel][1]);
    for (let i = 0; i < 20; i++) key(panel, shrink, true);
    assert.equal(size(panel), LIMITS[panel][0]);
  }
});

test('dragging, release and double click update the panel through its normal event handlers', () => {
  mount();
  const handle = splitter('right');
  pointer(handle, 'pointerdown', 500);
  pointer(handle, 'pointermove', 450);
  assert.equal(size('right'), 390);
  pointer(handle, 'pointerup', 450);
  pointer(handle, 'pointermove', 400);
  assert.equal(size('right'), 390);
  act(() => handle.dispatchEvent(new dom.window.MouseEvent('dblclick', { bubbles: true })));
  assert.equal(size('right'), DEFAULT_LAYOUT.size.right);
});

test('a secondary mouse button and lost pointer capture cannot keep resizing a panel', () => {
  mount();
  const handle = splitter('left');
  pointer(handle, 'pointerdown', 100, 0, 2);
  pointer(handle, 'pointermove', 200);
  assert.equal(size('left'), DEFAULT_LAYOUT.size.left);
  pointer(handle, 'pointerdown', 100);
  pointer(handle, 'lostpointercapture', 100);
  pointer(handle, 'pointermove', 200);
  assert.equal(size('left'), DEFAULT_LAYOUT.size.left);
});

test('saved dimensions and visibility survive remounting', () => {
  localStorage.setItem('uds-editor-layout', JSON.stringify({ visible: { right: false }, size: { left: 300, bottom: 450 } }));
  mount();
  assert.equal(size('left'), 300);
  assert.equal(size('bottom'), 450);
  assert.equal(host.querySelector('[data-panel="right"]'), null);
});

test('malformed stored fields fall back individually instead of producing invalid CSS or hidden panels', () => {
  localStorage.setItem('uds-editor-layout', JSON.stringify({ visible: { left: 'false', right: null }, size: { left: 'broken', right: {}, bottom: 9000 } }));
  mount();
  assert.equal(size('left'), DEFAULT_LAYOUT.size.left);
  assert.equal(size('right'), DEFAULT_LAYOUT.size.right);
  assert.equal(size('bottom'), LIMITS.bottom[1]);
});

test('Escape and outside clicks close the window menu', () => {
  mount();
  click(host.querySelector('.menu-title')!);
  act(() => window.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  assert.equal(host.querySelector('[role="menu"]'), null);
  click(host.querySelector('.menu-title')!);
  act(() => document.body.dispatchEvent(new dom.window.MouseEvent('mousedown', { bubbles: true })));
  assert.equal(host.querySelector('[role="menu"]'), null);
});

test('menu keys do not trigger editor hotkeys and Escape returns focus to the menu button', () => {
  mount();
  const title = host.querySelector<HTMLButtonElement>('.menu-title')!;
  click(title);
  const item = host.querySelector<HTMLButtonElement>('[role="menuitemcheckbox"]')!;
  item.focus();
  let editorKeys = 0;
  const onEditorKey = () => { editorKeys++; };
  window.addEventListener('keydown', onEditorKey);
  act(() => item.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'v', bubbles: true })));
  act(() => item.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  assert.equal(editorKeys, 0);
  assert.equal(host.querySelector('[role="menu"]'), null);
  assert.equal(document.activeElement, title);
  window.removeEventListener('keydown', onEditorKey);
});
