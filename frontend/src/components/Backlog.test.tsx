import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import ResizableTable from './ResizableTable.tsx';
import { PanelClose, PanelToggle } from './PanelClose.tsx';
import EditorMenus from './EditorMenus.tsx';
import TagEditor from './TagEditor.tsx';
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

test('column drag stores the width and release stops resizing', () => {
  act(() =>
    root.render(
      <ResizableTable id="drag-test">
        <thead>
          <tr>
            <th>Road</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>A</td>
          </tr>
        </tbody>
      </ResizableTable>,
    ),
  );
  const th = host.querySelector('th')!,
    handle = host.querySelector('[role=separator]')!;
  th.getBoundingClientRect = () => ({ width: 100 }) as DOMRect;
  const pointer = (type: string, x: number) => {
    const event = new dom.window.MouseEvent(type, { clientX: x, bubbles: true });
    Object.defineProperty(event, 'pointerId', { value: 1 });
    act(() => handle.dispatchEvent(event));
  };
  pointer('pointerdown', 10);
  pointer('pointermove', 40);
  assert.equal(th.style.width, '130px');
  assert.deepEqual(JSON.parse(localStorage.getItem('uds-table-drag-test')!), [130]);
  pointer('pointerup', 40);
  pointer('pointermove', 100);
  assert.equal(th.style.width, '130px');
});


test('tag pencil focuses and selects both dropdown and free-text values', () => {
  act(() => root.render(<TagEditor tags={{ surface: 'asphalt', name: 'Main street' }} onChange={() => undefined} />));
  for (const key of ['surface', 'name']) {
    click(host.querySelector(`[aria-label="Редактировать значение ${key} как текст"]`)!);
    const input = document.activeElement as HTMLInputElement;
    assert.equal(input.tagName, 'INPUT');
    assert.equal(input.selectionStart, 0);
    assert.equal(input.selectionEnd, input.value.length);
    assert.equal(input.value, key === 'surface' ? 'asphalt' : 'Main street');
  }
});
