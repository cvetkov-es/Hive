import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cascadeRoute, delayRisk } from '../src/lib/delay.ts';

// Выезд 09:00. Вторая заявка ждёт открытия окна 20 минут, у третьей окно
// закрывается в 12:15 — впритык к плановому началу 12:10.
const A = {
  engineer_id: 'E1', depart: '09:00', span_min: 240,
  stops: [
    { job_id: 's1', arrive: '09:20', start: '09:20', end: '10:20', window: '09:00-11:00' },
    { job_id: 's2', arrive: '10:40', start: '11:00', end: '12:00', window: '11:00-13:00' },
    { job_id: 's3', arrive: '12:10', start: '12:10', end: '13:00', window: '12:00-12:15' },
  ],
};

// День почти в норматив: 11 ч 50 мин от выезда до конца работы.
const B = {
  engineer_id: 'E2', depart: '09:00', span_min: 710,
  stops: [
    { job_id: 'b1', arrive: '09:10', start: '09:10', end: '20:50', window: '09:00-10:00' },
  ],
};

const starts = (r: ReturnType<typeof cascadeRoute>) => r.stops.map(s => s.start);

test('без задержки план не меняется', () => {
  const r = cascadeRoute(A, 0);
  assert.deepEqual(starts(r), [9 * 60 + 20, 11 * 60, 12 * 60 + 10]);
  assert.deepEqual(r.stops.map(s => s.late), [false, false, false]);
  assert.equal(r.span, 240);
  assert.equal(r.overtime, false);
});

test('ожидание окна съедает задержку: вторая заявка начинается вовремя', () => {
  const r = cascadeRoute(A, 10);
  // первая работа длится на 10 мин дольше, бригада приезжает во вторую
  // в 10:50 вместо 10:40 — окно всё равно открывается в 11:00
  assert.equal(r.stops[1].start, 11 * 60);
  assert.equal(r.stops[1].shift, 0);
});

test('приезд сдвигается на задержку предыдущей работы, дорога та же', () => {
  const r = cascadeRoute(A, 10);
  // первая заявка: приезд по плану, дорога от выезда не меняется
  assert.equal(r.stops[0].arrive, 9 * 60 + 20);
  // вторая: первая работа кончилась в 10:30 вместо 10:20, дорога 20 мин
  assert.equal(r.stops[1].arrive, 10 * 60 + 50);
});

test('после ожидания задержка снова копится', () => {
  const r = cascadeRoute(A, 10);
  // вторая работа тоже на 10 мин дольше: третья начинается в 12:20
  assert.equal(r.stops[2].start, 12 * 60 + 20);
  assert.equal(r.stops[2].shift, 10);
});

test('опоздание — начало позже закрытия окна', () => {
  const r = cascadeRoute(A, 10);
  assert.deepEqual(r.stops.map(s => s.late), [false, false, true]);
});

test('большая задержка пробивает и ожидание', () => {
  const r = cascadeRoute(A, 30);
  // первая до 10:50, дорога 20 мин — во вторую в 11:10, на 10 мин позже окна
  assert.equal(r.stops[1].start, 11 * 60 + 10);
  assert.equal(r.stops[1].shift, 10);
  assert.equal(r.stops[2].start, 12 * 60 + 50);
});

test('конец работы сдвигается вместе с началом и растёт на задержку', () => {
  const r = cascadeRoute(A, 10);
  assert.equal(r.stops[0].end, 10 * 60 + 30);
  assert.equal(r.stops[2].end, 13 * 60 + 20);
  assert.equal(r.span, 260);
});

test('день сверх 12 ч — перебор нормы в минутах', () => {
  assert.equal(cascadeRoute(B, 10).overtime, false);   // ровно 12 ч — ещё норма
  const r = cascadeRoute(B, 15);
  assert.equal(r.overtime, true);
  assert.equal(r.overBy, 5);
});

test('окно на весь день разбирается', () => {
  const r = cascadeRoute({
    engineer_id: 'E3', depart: '10:00', span_min: 90,
    stops: [{ job_id: 'x', arrive: '10:30', start: '10:30', end: '11:30', window: '00:01-23:59' }],
  }, 30);
  assert.equal(r.stops[0].late, false);
});

test('по плану целиком: сколько опоздает и кто первым', () => {
  const risk = delayRisk([A, B], 15);
  assert.equal(risk.late, 1);
  assert.equal(risk.overtime, 1);
  assert.deepEqual(risk.byEngineer, [{ id: 'E1', late: 1 }]);
  assert.equal(risk.routes.E1.stops[2].late, true);
});

test('бригада без заявок в расчёт не попадает', () => {
  const risk = delayRisk([{ engineer_id: 'E0', depart: '09:00', span_min: 0, stops: [] }], 30);
  assert.equal(risk.late, 0);
  assert.equal(risk.overtime, 0);
});

test('после события начатое до него не затягивается — это уже прошлое', () => {
  // событие в 11:00: первая работа (09:20–10:20) выполнена, остальные ещё впереди
  const r = cascadeRoute(A, 10, 11 * 60);
  assert.equal(r.stops[0].end, 10 * 60 + 20);
  assert.equal(r.stops[0].shift, 0);
  // вторая начинается в 11:00 и длится на 10 мин дольше, третья — в 12:20
  assert.equal(r.stops[1].end, 12 * 60 + 10);
  assert.equal(r.stops[2].start, 12 * 60 + 20);
});

test('прогноз по плану после события считает от момента события', () => {
  const risk = delayRisk([A], 10, 11 * 60);
  assert.equal(risk.routes.E1.stops[0].end, 10 * 60 + 20);
});
