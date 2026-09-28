import { test } from 'node:test';
import assert from 'node:assert/strict';
import { searchPlan } from '../src/lib/search.ts';

const plan = {
  routes: [
    {
      engineer_id: 'BR-ЮГО-01', engineer_name: 'Бригада Ефимов', transport: 'Автомобиль', jobs: 2,
      stops: [
        { job_id: '1287', address: 'Москва Бирюлёвская ул. д. 44', start: '09:39' },
        { job_id: '40919', address: 'Город Москва, проезд Борисовский, д. 44 к 3', start: '11:25' },
      ],
    },
    {
      engineer_id: 'BR-ЮГО-11', engineer_name: 'Бригада Русаков', transport: 'Автомобиль', jobs: 1,
      stops: [{ job_id: '75745', address: 'Домодедово, ул. Зеленая, д. 85', start: '11:29' }],
    },
  ],
  reserve: [
    { engineer_id: 'BR-ЮГО-05', engineer_name: 'Бригада Лаптев', transport: 'Общественный транспорт', cluster: 'Москва' },
  ],
  unassigned: [
    { job_id: '48227', address: 'Москва, Таганская ул., д. 3', window: '10:00-12:00' },
  ],
};

test('меньше двух символов — ничего не ищем', () => {
  assert.deepEqual(searchPlan(plan, ''), []);
  assert.deepEqual(searchPlan(plan, ' 4 '), []);
});

test('номер заявки целиком — она первая, с бригадой', () => {
  const hits = searchPlan(plan, '40919');
  assert.equal(hits[0].kind, 'job');
  assert.equal(hits[0].id, '40919');
  assert.equal(hits[0].kind === 'job' && hits[0].engineerId, 'BR-ЮГО-01');
});

test('бригада по короткому коду без «BR-», регистр не важен', () => {
  const hits = searchPlan(plan, 'юго-11');
  assert.equal(hits[0].kind, 'engineer');
  assert.equal(hits[0].id, 'BR-ЮГО-11');
});

test('бригада по фамилии', () => {
  const hits = searchPlan(plan, 'русаков');
  assert.deepEqual(hits.map(h => h.id), ['BR-ЮГО-11']);
});

test('заявки по части адреса', () => {
  const hits = searchPlan(plan, 'зеленая');
  assert.deepEqual(hits.map(h => h.id), ['75745']);
});

test('«ё» и «е» не различаются', () => {
  assert.deepEqual(searchPlan(plan, 'бирюлевская').map(h => h.id), ['1287']);
  assert.deepEqual(searchPlan(plan, 'зелёная').map(h => h.id), ['75745']);
});

test('незакрытая заявка находится и помечена', () => {
  const hits = searchPlan(plan, 'таганская');
  assert.equal(hits.length, 1);
  assert.equal(hits[0].kind, 'job');
  assert.equal(hits[0].kind === 'job' && hits[0].engineerId, null);
  assert.match(hits[0].meta, /не назначена/);
});

test('бригада из резерва тоже находится', () => {
  const hits = searchPlan(plan, 'лаптев');
  assert.equal(hits[0].id, 'BR-ЮГО-05');
  assert.match(hits[0].meta, /резерв/);
});

test('совпадений не больше лимита', () => {
  assert.equal(searchPlan(plan, 'москва', 2).length, 2);
});
