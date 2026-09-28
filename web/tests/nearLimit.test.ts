import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nearLimit } from '../src/lib/nearLimit.ts';

const r = (id: string, span: number, stops = 1) =>
  ({ engineer_id: id, span_min: span, stops: Array.from({ length: stops }, () => ({})) });

test('день от 11 ч — в списке, длинные первыми', () => {
  const out = nearLimit([r('A', 665), r('B', 705), r('C', 600)]);
  assert.deepEqual(out.map(x => x.id), ['B', 'A']);
});

test('ровно 11 ч — уже упор', () => {
  assert.deepEqual(nearLimit([r('A', 660)]).map(x => x.id), ['A']);
});

test('бригада без заявок не упирается ни во что', () => {
  assert.deepEqual(nearLimit([r('A', 700, 0)]), []);
});

test('сколько осталось до норматива 12 ч', () => {
  assert.equal(nearLimit([r('A', 705)])[0].left, 15);
});
