// Слова и числа на экране.
//
// Одно место для склонений и единиц, потому что ошибка здесь видна всем:
// «42 заявок» и «1 заявок» на экране читаются как небрежность, и после них
// перестают верить аккуратным числам рядом.

/** Форма слова по числу: plural(1, 'заявка', 'заявки', 'заявок') -> «заявка». */
export function plural(n: number, one: string, few: string, many: string): string {
  const k = Math.abs(Math.trunc(n)) % 100;
  if (k >= 11 && k <= 19) return many;
  const d = k % 10;
  if (d === 1) return one;
  if (d >= 2 && d <= 4) return few;
  return many;
}

/** Число вместе со словом: count(4, 'бригада', 'бригады', 'бригад') -> «4 бригады». */
export function count(n: number, one: string, few: string, many: string): string {
  return `${n} ${plural(n, one, few, many)}`;
}

/** 259 -> «4 ч 19 мин», 45 -> «45 мин», 120 -> «2 ч». */
export function dur(minutes: number): string {
  const m = Math.round(minutes);
  const sign = m < 0 ? '−' : '';
  const a = Math.abs(m);
  const h = Math.floor(a / 60), mm = a % 60;
  if (h && mm) return `${sign}${h} ч ${mm} мин`;
  return h ? `${sign}${h} ч` : `${sign}${mm} мин`;
}

/** Число со знаком и настоящим минусом: «+17.6», «−3», «0». */
export function signed(x: number, digits = 0): string {
  const s = Math.abs(x).toFixed(digits);
  if (Number(s) === 0) return (0).toFixed(digits);
  return x > 0 ? `+${s}` : `−${s}`;
}

/** Транспорт коротко — для строк списка и ленты, где полное «Общественный
 *  транспорт» съело бы половину ширины. Полное название — в подсказке. */
const TRANSPORT_SHORT: Record<string, string> = {
  'Автомобиль': 'авто', 'Общественный транспорт': 'общ. транспорт',
  'Велосипед': 'велосипед', 'Пешеход': 'пешком', 'Пешком': 'пешком',
};
export function shortTransport(t: string): string {
  return TRANSPORT_SHORT[t] ?? t.toLowerCase();
}

/** Время события и окна — всегда ЧЧ:ММ в 24-часовом виде, от 00:00 до 23:59.
 *  Это же правило проверяет бэкенд (schemas.HHMM), иначе ответ 422. */
export const HHMM_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Как человек набирает время: «1540» -> «15:40», «9:30» -> «09:30» при уходе
 *  из поля. Браузерный <input type="time"> здесь не годится: в английской
 *  локали он показывает «03:40 PM», и диспетчер видит не то время, что ввёл. */
export function typingTime(raw: string): string {
  let s = raw.replace(/[^\d:]/g, '');
  if (/^\d{3,4}$/.test(s)) s = `${s.slice(0, 2)}:${s.slice(2)}`;
  return s.slice(0, 5);
}

export function settleTime(raw: string): string {
  const m = /^(\d):(\d\d)$/.exec(raw.trim());
  return m ? `0${m[1]}:${m[2]}` : raw.trim();
}
