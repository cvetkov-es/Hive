import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inZone, zoneOptions } from '../src/lib/zones.ts';

test('одна зона — фильтр не нужен', () => {
  assert.deepEqual(zoneOptions({ 'Москва': 66 }), []);
});

test('несколько зон: «Все», потом Москва, потом остальные по алфавиту', () => {
  const z = zoneOptions({ 'Домодедово': 12, 'Москва': 51, 'Кашира-Ступино': 20 });
  assert.deepEqual(z.map(x => x.key), ['all', 'Москва', 'Домодедово', 'Кашира-Ступино']);
});

test('подпись — имя до дефиса, полное имя в подсказке', () => {
  const z = zoneOptions({ 'Домодедово': 12, 'Москва': 51, 'Кашира-Ступино': 20 });
  const k = z.find(x => x.key === 'Кашира-Ступино')!;
  assert.equal(k.label, 'Кашира');
  assert.match(k.title, /Кашира-Ступино/);
  assert.equal(z[0].label, 'Все');
});

test('у «Все» заявок столько, сколько во всех зонах', () => {
  const z = zoneOptions({ 'Домодедово': 12, 'Москва': 51, 'Кашира-Ступино': 20 });
  assert.equal(z[0].count, 83);
  assert.equal(z.find(x => x.key === 'Москва')!.count, 51);
});

test('«Все» пропускает любую зону, конкретная — только свою', () => {
  assert.equal(inZone('Москва', 'all'), true);
  assert.equal(inZone('Москва', 'Москва'), true);
  assert.equal(inZone('Москва', 'Домодедово'), false);
});

test('подсказка зоны склоняет число заявок', () => {
  const z = zoneOptions({ 'Москва': 51, 'Домодедово': 12, 'Кашира-Ступино': 22 });
  assert.match(z.find(x => x.key === 'Москва')!.title, /51 заявка/);
  assert.match(z.find(x => x.key === 'Кашира-Ступино')!.title, /22 заявки/);
  assert.match(z.find(x => x.key === 'Домодедово')!.title, /12 заявок/);
});
