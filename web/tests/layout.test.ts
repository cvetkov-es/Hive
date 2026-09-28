import { test } from 'node:test';
import assert from 'node:assert/strict';
import { layoutFor } from '../src/lib/layout.ts';

test('на большом экране карта во весь экран (A)', () => {
  assert.equal(layoutFor(1680, 1000), 'wide');
  assert.equal(layoutFor(1920, 1080), 'wide');
  assert.equal(layoutFor(1536, 864), 'wide');
});

test('порог включительный: ровно 1440×820 — ещё A', () => {
  assert.equal(layoutFor(1440, 820), 'wide');
});

test('ноутбук 1366×768 — лента в центре (B)', () => {
  assert.equal(layoutFor(1366, 768), 'compact');
});

test('узко, но высоко — B: панели по бокам закрыли бы карту', () => {
  assert.equal(layoutFor(1439, 1200), 'compact');
});

test('широко, но низко — B: лента снизу закрыла бы карту', () => {
  assert.equal(layoutFor(1920, 819), 'compact');
});
