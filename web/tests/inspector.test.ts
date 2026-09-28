import { test } from 'node:test';
import assert from 'node:assert/strict';
import { backTarget, inspectorView, neighbours } from '../src/lib/inspector.ts';

const base = { eventOpen: false, selection: 'none' as const, hasDiff: false, summaryTab: 'diff' as const };

test('открытая форма события важнее любого выбора', () => {
  assert.equal(inspectorView({ ...base, eventOpen: true, selection: 'job' }), 'event');
});

test('выбор заявки и бригады', () => {
  assert.equal(inspectorView({ ...base, selection: 'job' }), 'job');
  assert.equal(inspectorView({ ...base, selection: 'engineer' }), 'engineer');
});

test('без выбора: после события — «Что изменилось», иначе сводка', () => {
  assert.equal(inspectorView({ ...base, hasDiff: true }), 'diff');
  assert.equal(inspectorView({ ...base, hasDiff: true, summaryTab: 'summary' }), 'summary');
  assert.equal(inspectorView(base), 'summary');
});

test('«назад» ведёт туда, откуда пришли: на вкладку сводки, если её выбрали', () => {
  assert.equal(backTarget({ ...base, hasDiff: true, summaryTab: 'diff' }), 'Что изменилось');
  assert.equal(backTarget({ ...base, hasDiff: true, summaryTab: 'summary' }), 'Сводка смены');
  assert.equal(backTarget(base), 'Сводка смены');
});

test('соседи в списке незакрытых', () => {
  assert.deepEqual(neighbours(['a', 'b', 'c'], 'b'), { index: 2, total: 3, prev: 'a', next: 'c' });
  assert.deepEqual(neighbours(['a', 'b', 'c'], 'a'), { index: 1, total: 3, prev: null, next: 'b' });
  assert.deepEqual(neighbours(['a', 'b', 'c'], 'c'), { index: 3, total: 3, prev: 'b', next: null });
  assert.equal(neighbours(['a', 'b'], 'x'), null);
});
