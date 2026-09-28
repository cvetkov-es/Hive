// Объяснение маршрута бригады целиком.
//
// ТЗ 2.4.2 дословно: «для выбранного маршрута — краткое объяснение, какие
// ограничения и факторы повлияли на решение». Карточка заявки отвечает, почему
// заявка у этой бригады; здесь — почему день бригады выглядит так: откуда
// порядок, откуда ожидание, что держит день и почему бригада без заявок стоит
// в резерве.
//
// Краткое объяснение (summary) идёт первым: три-пять строк, которые читаются
// вслух. Всё остальное — подробности для того, кто хочет проверить.

import type { RouteExplanation } from '../api';
import type { Mark } from '../colors';
import { dur } from '../text';

const STATUS_CLASS: Record<string, string> = {
  'в работе': 'ok', 'в резерве': '', 'выбыла': 'bad',
};

function DayBlock({ day }: { day: NonNullable<RouteExplanation['day']> }) {
  // Шкала — норматив 12 ч, а не длина дня: иначе все полосы были бы полными,
  // и упор в предел не был бы виден. Ожидание и дорога — части дня наравне
  // с работой: норматив считает день от выезда до конца последней работы.
  const L = day.limit_min;
  const part = (m: number) => `${Math.max(0, (m / L) * 100)}%`;
  const over = day.span_min > L;
  return (
    <>
      <div className={`daybig ${over ? 'over' : ''}`} role="img"
           aria-label={`день ${day.span_text} из ${day.limit_text}`}>
        <i className="work" style={{ width: part(day.work_min) }} title={`на месте у клиентов ${dur(day.work_min)}`} />
        <i className="road" style={{ width: part(day.travel_min) }} title={`в дороге ${dur(day.travel_min)}`} />
        {day.wait_min > 0 && (
          <i className="wait" style={{ width: part(day.wait_min) }} title={`ожидание окон ${dur(day.wait_min)}`} />
        )}
      </div>
      <div className="daybig-legend">
        <span><i className="work" />работа {dur(day.work_min)}</span>
        <span><i className="road" />дорога {dur(day.travel_min)}</span>
        {day.wait_min > 0 && <span><i className="wait" />ожидание {dur(day.wait_min)}</span>}
        <span className={day.left_min <= 30 ? 'no' : ''}>
          {day.left_min >= 0 ? `запас до 12 ч — ${dur(day.left_min)}` : `сверх нормы ${dur(-day.left_min)}`}
        </span>
      </div>
      <div className="note">
        Выезд <b>{day.depart}</b>, конец последней работы <b>{day.finish}</b>: день{' '}
        <b>{day.span_text}</b> из {day.limit_text} по нормативу; {day.km.toFixed(1)} км.
      </div>
    </>
  );
}

export function RoutePanel({ route, mark, error, onPickJob }: {
  route: RouteExplanation | null;
  mark?: Mark;
  error: string;
  onPickJob: (jobId: string) => void;
}) {
  if (error) return <div className="explain"><div className="un-reason">{error}</div></div>;
  if (!route) return <div className="explain"><div className="note">Загружаем объяснение маршрута…</div></div>;

  const h = route.header;
  const order = route.order;
  // Строки порядка, которые уже вошли в краткое объяснение, второй раз не
  // печатаются: повтор той же фразы выглядит как заполнитель.
  const shown = new Set(route.summary);
  const orderRest = (order?.text ?? []).filter(t => !shown.has(t));
  const waitsRest = (order?.waits ?? []).filter(w => !shown.has(w.text));
  const legRest = order?.longest_leg && !shown.has(order.longest_leg.text)
    ? order.longest_leg.text : '';
  const constraints = [...route.constraints].sort((a, b) =>
    Number(b.binding) - Number(a.binding) || a.rank - b.rank);

  return (
    <div className="explain route">
      <div className="route-head">
        {mark && <i className={`swatch ${mark.shape}`} style={{ background: mark.color }} />}
        <b>{h.engineer_id}</b>
        <span className="route-name">{h.name}</span>
        <span className={`badge ${STATUS_CLASS[route.status] ?? ''}`}>{route.status}</span>
      </div>
      <div className="route-meta">
        {h.transport} · зона «{h.zone}» · смена {h.shift}
        <br />
        выезжает: {h.start_point} · умеет: {h.skills.join(', ').toLowerCase()}
        <br />
        оборудование утром: {h.equipment_text}
      </div>

      <div className="sub-head">Коротко</div>
      <ul className="reasons">
        {route.summary.map((t, i) => <li key={i}>{t}</li>)}
      </ul>

      {route.day && (
        <>
          <div className="sub-head">День против норматива 12 ч</div>
          <DayBlock day={route.day} />
        </>
      )}

      {route.stops.length > 0 && (
        <>
          <div className="sub-head">Остановки по порядку</div>
          <ol className="stops">
            {route.stops.map(s => (
              <li key={s.job_id} className={s.frozen ? 'frozen' : ''}
                  onClick={() => onPickJob(s.job_id)} role="button" tabIndex={0}
                  onKeyDown={e => { if (e.key === 'Enter') onPickJob(s.job_id); }}
                  title="Щёлкните — объяснение заявки">
                <span className="seq">{s.seq}</span>
                <span className="what"><b>{s.job_id}</b> {s.work_kind}</span>
                <span className="when">{s.start}–{s.end}</span>
                <span className="sub">
                  окно {s.window} · приезд {s.arrive} · {s.leg_km.toFixed(1)} км, {s.leg_min} мин
                  {s.frozen && ' · заморожен событием'}
                </span>
                {s.wait_min > 0 && (
                  <span className="sub wait">ждёт открытия окна {dur(s.wait_min)}</span>
                )}
              </li>
            ))}
          </ol>
        </>
      )}

      {(orderRest.length > 0 || waitsRest.length > 0 || legRest) && (
        <>
          <div className="sub-head">Почему такой порядок</div>
          <ul className="reasons">
            {orderRest.map((t, i) => <li key={`o${i}`}>{t}</li>)}
            {waitsRest.map(w => <li key={`w${w.job_id}`}>{w.text}</li>)}
            {legRest && <li>{legRest}</li>}
          </ul>
        </>
      )}

      {constraints.length > 0 && (
        <>
          <div className="sub-head">Что ограничивает маршрут</div>
          <ul className="constraints">
            {constraints.map(c => (
              <li key={c.key} className={c.binding ? 'binding' : ''}>
                {c.binding && <span className="chip">на пределе</span>}
                {c.text}
              </li>
            ))}
          </ul>
        </>
      )}

      {route.reserve && !route.summary.includes(route.reserve.text) && (
        <>
          <div className="sub-head">Почему в резерве</div>
          <div className="note">{route.reserve.text}</div>
        </>
      )}

      {route.frozen_text && <div className="caveat">{route.frozen_text}</div>}
      {route.status !== 'в работе' ? null : (
        <div className="caveat">
          {route.caveat[0].toUpperCase() + route.caveat.slice(1)}.
        </div>
      )}
    </div>
  );
}
