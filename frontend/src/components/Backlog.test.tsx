import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import ResizableTable from './ResizableTable.tsx';
import { PanelClose, PanelToggle } from './PanelClose.tsx';
import EditorMenus from './EditorMenus.tsx';
let dom: JSDOM, root: Root, host: HTMLDivElement;
beforeEach(() => {
  dom = new JSDOM('<html><body></body></html>', { url: 'https://test.local' });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    localStorage: dom.window.localStorage,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  dom.window.close();
});
const click = (e: Element) =>
  act(() => e.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })));
test('column keyboard resizing persists width and double click resets', () => {
  act(() =>
    root.render(
      <ResizableTable id="test">
        <thead>
          <tr>
            <th>Road</th>
            <th>z</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>A</td>
            <td>1</td>
          </tr>
        </tbody>
      </ResizableTable>,
    ),
  );
  const handle = host.querySelector('[role=separator]')!;
  act(() =>
    handle.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })),
  );
  assert.equal(host.querySelector('th')!.style.width, '48px');
  assert.deepEqual(JSON.parse(localStorage.getItem('uds-table-test')!), [48]);
  act(() => handle.dispatchEvent(new dom.window.MouseEvent('dblclick', { bubbles: true })));
  assert.equal(host.querySelector('th')!.style.width, '');
});
test('panel close invokes the shared window toggle', () => {
  let panel = '';
  act(() =>
    root.render(
      <PanelToggle.Provider
        value={(p) => {
          panel = p;
        }}
      >
        <PanelClose panel="right" />
      </PanelToggle.Provider>,
    ),
  );
  click(host.querySelector('button')!);
  assert.equal(panel, 'right');
});
test('reports menu and help close on Escape', () => {
  act(() => root.render(<EditorMenus onReport={() => undefined} onDownload={() => undefined} />));
  click([...host.querySelectorAll('button')].find((b) => b.textContent === 'Отчёты')!);
  assert.equal(host.querySelectorAll('[role=menuitem]').length, 4);
  act(() => window.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' })));
  assert.equal(host.querySelector('[role=menu]'), null);
  click([...host.querySelectorAll('button')].find((b) => b.textContent === 'Справка')!);
  click(host.querySelector('[role=menuitem]')!);
  assert.equal(host.querySelectorAll('[role=dialog] section').length, 8);
  act(() => window.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' })));
  assert.equal(host.querySelector('[role=dialog]'), null);
});
