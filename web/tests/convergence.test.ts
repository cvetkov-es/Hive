import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allClosedAt, bestAt, conclusion, logScale, secondsLabel, stepLine,
         timeTicks } from '../src/lib/convergence.ts';

const east = [
  { seconds: 0.07, engineers: 12, km: 166.27, assigned: 45 },
  { seconds: 1.2, engineers: 11, km: 230.0, assigned: 60 },
  { seconds: 4.3, engineers: 9, km: 218.48, assigned: 66 },
  { seconds: 16.03, engineers: 9, km: 198.22, assigned: 66 },
  { seconds: 18.11, engineers: 9, km: 194.59, assigned: 66 },
];

test('лучший план к моменту — последний найденный не позже него', () => {
  assert.equal(bestAt(east, 0.01), null);
  assert.equal(bestAt(east, 0.07)?.engineers, 12);
  assert.equal(bestAt(east, 10)?.km, 218.48);
  assert.equal(bestAt(east, 600)?.km, 194.59);
});

test('когда закрыты все заявки', () => {
  assert.equal(allClosedAt(east, 66), 4.3);
  assert.equal(allClosedAt(east, 67), null);
});

test('подписи времени: доли секунды, секунды, минуты', () => {
  assert.equal(secondsLabel(0.07), '0.07 с');
  assert.equal(secondsLabel(0.1), '0.1 с');
  assert.equal(secondsLabel(4.3), '4.3 с');
  assert.equal(secondsLabel(2), '2 с');
  assert.equal(secondsLabel(18.11), '18 с');
  assert.equal(secondsLabel(60), '1 мин');
  assert.equal(secondsLabel(600), '10 мин');
  assert.equal(secondsLabel(150), '2 мин 30 с');
});

test('логарифмическая шкала: края и середина по порядкам', () => {
  assert.equal(logScale(0.1, 0.1, 1000, 400), 0);
  assert.equal(logScale(1000, 0.1, 1000, 400), 400);
  assert.equal(logScale(10, 0.1, 1000, 400), 200);
  assert.equal(logScale(0.01, 0.1, 1000, 400), 0, 'за краем — на краю');
});

test('деления — только внутри шкалы', () => {
  assert.deepEqual(timeTicks(0.05, 30).map(t => t.label), ['0.1 с', '1 с', '10 с']);
  assert.deepEqual(timeTicks(0.05, 600).map(t => t.label), ['0.1 с', '1 с', '10 с', '1 мин', '10 мин']);
});

test('ступеньки: значение держится до следующего плана и до конца счёта', () => {
  const pts = stepLine(east.slice(0, 3), s => s.engineers, 30);
  assert.deepEqual(pts, [[0.07, 12], [1.2, 12], [1.2, 11], [4.3, 11], [4.3, 9], [30, 9]]);
});

test('вывод: когда закрыто всё и что в плане к каждому ожиданию интерфейса', () => {
  assert.equal(conclusion(east, 66, 30),
    'Все 66 заявок закрыты через 4.3 с. '
    + 'За 10 с (столько считается событие дня) — 9 бригад, 218.5 км; '
    + 'за 20 с (кнопка «Пересчитать») — 9 бригад, 194.6 км; '
    + 'за 30 с — без изменений.');
});

test('вывод: момент 10 мин — план дня; моменты за концом счёта не упоминаются', () => {
  const long = [...east, { seconds: 400, engineers: 8, km: 205.3, assigned: 66 }];
  assert.match(conclusion(long, 66, 600), /за 10 мин \(столько считали план дня\) — 8 бригад, 205\.3 км\.$/);
  assert.doesNotMatch(conclusion(east, 66, 15), /20 с/);
});

test('вывод: заявки не закрыты — так и сказано', () => {
  const short = east.slice(0, 2);
  assert.equal(conclusion(short, 66, 10),
    'За 10 с закрыто заявок: 60 из 66. '
    + 'За 10 с (столько считается событие дня) — закрыто 60 из 66, 11 бригад, 230.0 км.');
});
