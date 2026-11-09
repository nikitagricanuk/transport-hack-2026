import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { isEscapeKey, isFullscreenToggle } from './keys.ts';
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
