import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { claimDraftPointKey, isDraftPointKeyClaimed, isEscapeKey, isFullscreenToggle, shouldDeleteSelection } from './keys.ts';
import { APP_VERSION, isFrontendOutdated } from './version.ts';
import { DEFAULT_CENTER, DEFAULT_ZOOM, defaultCamera } from './view.ts';

const plain = { key: '', code: '', repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false };

test('default camera fits the file north-up and untilted', () => {
  const camera = defaultCamera([52, 104, 53, 105]);
  assert.deepEqual(camera, {
    kind: 'fit',
    bounds: [[104, 52], [105, 53]],
    bearing: 0,
    pitch: 0,
  });
});

test('default camera without a file is the initial city view', () => {
  assert.deepEqual(defaultCamera(null), {
    kind: 'ease',
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    bearing: 0,
    pitch: 0,
  });
});

test('F toggles fullscreen on a US or Russian layout and Escape does not', () => {
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'f' }), true);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'а' }), true);
  assert.equal(isFullscreenToggle({ ...plain, code: '', key: 'F' }), true);
  assert.equal(isFullscreenToggle({ ...plain, code: '', key: 'А' }), true);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyA', key: 'a' }), false);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'f', ctrlKey: true }), false);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'f', metaKey: true }), false);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'f', altKey: true }), false);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'f', shiftKey: true }), false);
  assert.equal(isFullscreenToggle({ ...plain, code: 'KeyF', key: 'f', repeat: true }), false);
  assert.equal(isFullscreenToggle({ ...plain, key: 'Escape', code: 'Escape' }), false);
  assert.equal(isEscapeKey({ ...plain, key: 'Escape', code: 'Escape' }), true);
  assert.equal(isEscapeKey({ ...plain, key: 'Esc', code: '' }), true);
  assert.equal(isEscapeKey({ ...plain, key: 'а', code: 'KeyF', keyCode: 27 }), true);
});

test('a way draft owns Delete and Backspace, including the key that removes the last point', async () => {
  const del = { ...plain, key: 'Delete' };
  const back = { ...plain, key: 'Backspace' };

  assert.equal(shouldDeleteSelection(del, 'way', 3), false);
  assert.equal(shouldDeleteSelection(back, 'way', 1), false);
  // global listener runs before the draft mutates: the point is still there
  assert.equal(shouldDeleteSelection(del, 'way', 1, false), false);

  let points = 1;
  claimDraftPointKey();
  points -= 1;
  assert.equal(points, 0);
  assert.equal(shouldDeleteSelection(del, 'way', points, isDraftPointKeyClaimed()), false);
  assert.equal(shouldDeleteSelection(back, 'way', points, isDraftPointKeyClaimed()), false);

  await new Promise((resolve) => queueMicrotask(resolve));
  assert.equal(isDraftPointKeyClaimed(), false);
  assert.equal(shouldDeleteSelection(del, 'way', 0), true);
  assert.equal(shouldDeleteSelection(back, 'way', 0), false);

  assert.equal(shouldDeleteSelection(del, 'select', 0), true);
  assert.equal(shouldDeleteSelection(back, 'select', 0), true);
  assert.equal(shouldDeleteSelection(back, 'node', 0), true);
  assert.equal(shouldDeleteSelection({ ...del, shiftKey: true }, 'select', 0), true);
  assert.equal(shouldDeleteSelection({ ...del, ctrlKey: true }, 'select', 0), false);
  assert.equal(shouldDeleteSelection({ ...del, metaKey: true }, 'select', 0), false);
  assert.equal(shouldDeleteSelection({ ...back, altKey: true }, 'select', 0), false);
  assert.equal(shouldDeleteSelection({ ...plain, key: 'а', code: 'KeyF' }, 'way', 2), false);
  assert.equal(shouldDeleteSelection({ ...plain, key: 'f', code: 'KeyF' }, 'select', 0), false);
});

test('global shortcuts stay on the bubble phase so a draft can swallow Delete', () => {
  const app = readFileSync(new URL('../App.tsx', import.meta.url), 'utf8');
  const map = readFileSync(new URL('../components/MapView.tsx', import.meta.url), 'utf8');
  assert.match(app, /window\.addEventListener\('keydown', onKey, false\)/);
  assert.doesNotMatch(app, /addEventListener\('keydown', onKey, true\)/);
  assert.match(map, /window\.addEventListener\('keydown', onKey, true\)/);
  assert.match(map, /e\.stopImmediatePropagation\(\)/);
  assert.match(map, /claimDraftPointKey\(\)/);
});

test('page version matches package.json and is outdated only against a newer server', () => {
  const pkg = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8')) as { version: string };
  assert.equal(APP_VERSION, pkg.version);
  assert.equal(isFrontendOutdated(APP_VERSION, APP_VERSION), false);
  assert.equal(isFrontendOutdated('0.1.0', '0.1.1'), true);
  assert.equal(isFrontendOutdated('0.1.0', '0.2.0'), true);
  assert.equal(isFrontendOutdated('0.9.0', '1.0.0'), true);
  assert.equal(isFrontendOutdated('1.0.0', '0.9.9'), false);
  assert.equal(isFrontendOutdated('v0.1.0', '0.1.0'), false);
  assert.equal(isFrontendOutdated('abc', 'def'), true);
  assert.equal(isFrontendOutdated('abc', 'abc'), false);
  assert.equal(isFrontendOutdated('0.1.0', ''), false);
  assert.equal(isFrontendOutdated('0.1.0', null), false);
});
