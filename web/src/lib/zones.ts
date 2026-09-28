// Фильтр по зонам обслуживания.
//
// На Юго-востоке три зоны: Москва, Домодедово и Кашира-Ступино в 90 км от
// города. Бригада работает только в своей зоне, и диспетчеру удобно смотреть
// зону целиком — чьи там бригады и как они загружены. В регионе с одной
// зоной фильтру нечего делать, и он не показывается.
//
// Подпись — имя до дефиса («Кашира»), чтобы четыре кнопки влезли в панель;
// полное имя — в подсказке. Москва первой (там офис и больше всего заявок),
// остальные по алфавиту.

import { count } from '../text.ts';

export type ZoneOption = { key: string; label: string; title: string; count: number };

export const ALL = 'all';

export function zoneOptions(clusters: Record<string, number>): ZoneOption[] {
  const names = Object.keys(clusters);
  if (names.length <= 1) return [];
  names.sort((a, b) => (a === 'Москва' ? -1 : b === 'Москва' ? 1 : a.localeCompare(b, 'ru')));
  const total = names.reduce((n, k) => n + clusters[k], 0);
  return [
    { key: ALL, label: 'Все', title: 'Все зоны региона', count: total },
    ...names.map(name => ({
      key: name, label: name.split('-')[0], count: clusters[name],
      title: `Зона «${name}»: ${count(clusters[name], 'заявка', 'заявки', 'заявок')}`,
    })),
  ];
}

export function inZone(cluster: string, zone: string): boolean {
  return zone === ALL || cluster === zone;
}
