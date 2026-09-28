// Список бригад (раскладка A). Он же легенда карты и ленты: цвет и форма
// метки здесь те же, что на точках и на блоках. В раскладке B отдельного
// списка нет — строка бригады слита со строкой ленты смены.
//
// Полоса дня показывает не «сколько сделано», а упор в норматив 12 часов.
// Это ограничение чаще географии решает, сколько нужно людей: бригада с
// одиннадцатичасовым днём выглядит незагруженной по числу заявок и при этом
// не может взять ни одной новой. От 11 часов полоса янтарная.
//
// Бригады без заявок стоят отдельным блоком «Резерв»: без него вопрос «а
// почему эта бригада сегодня не работает» остаётся без ответа.

import type { Plan } from '../api';
import type { Mark } from '../colors';
import { inZone } from '../lib/zones.ts';
import { NEAR_FROM_MIN, NORM_MIN } from '../lib/nearLimit.ts';
import { count, dur, shortTransport } from '../text';

export function Swatch({ mark }: { mark?: Mark }) {
  const m = mark ?? { color: 'var(--ink-3)', shape: 'ci' as const };
  return <i className={`swatch ${m.shape}`} style={{ background: m.color }} />;
}

/** Полоса дня против норматива 12 ч и подпись к ней. */
export function DayBar({ span }: { span: number }) {
  const tone = span > NORM_MIN ? 'over' : span >= NEAR_FROM_MIN ? 'near' : '';
  return (
    <div className={`daybar ${tone}`} title={`день ${dur(span)} из норматива 12 ч`}>
      <span className="daybar-track"><i style={{ width: `${Math.min(100, (span / NORM_MIN) * 100)}%` }} /></span>
      <span className="daybar-txt">{dur(span)} из 12 ч</span>
    </div>
  );
}

export function FleetList({ plan, marks, selected, zone, onSelect }: {
  plan: Plan | null;
  marks: Record<string, Mark>;
  selected: string | null;
  zone: string;
  onSelect: (engineerId: string | null) => void;
}) {
  const routes = (plan?.routes ?? []).filter(r => r.stops.length && inZone(r.cluster, zone));
  const reserve = (plan?.reserve ?? []).filter(r => inZone(r.cluster, zone));

  const row = (id: string, title: string, body: React.ReactNode) => {
    const isSel = selected === id;
    return (
      <div key={id}
           className={`fleet-row ${isSel ? 'sel' : ''} ${selected && !isSel ? 'dim' : ''}`}
           onClick={() => onSelect(isSel ? null : id)}
           role="button" tabIndex={0} aria-pressed={isSel} title={title}
           onKeyDown={e => { if (e.key === 'Enter') onSelect(isSel ? null : id); }}>
        {body}
      </div>
    );
  };

  return (
    <div className="fleet">
      {routes.map(r => {
        const last = r.stops[r.stops.length - 1];
        return row(r.engineer_id,
          `${r.engineer_name}, ${r.transport}, зона «${r.cluster}». Щёлкните — объяснение маршрута`,
          <>
            <div className="fleet-top">
              <Swatch mark={marks[r.engineer_id]} />
              <b className="fleet-id">{r.engineer_id}</b>
              <span className="fleet-num">{count(r.jobs, 'заявка', 'заявки', 'заявок')} · {r.km.toFixed(1)} км</span>
            </div>
            <div className="fleet-meta">{shortTransport(r.transport)} · {r.depart}–{last.end}</div>
            <DayBar span={r.span_min} />
          </>);
      })}

      {reserve.length > 0 && (
        <>
          <div className="fleet-sub">Резерв · {count(reserve.length, 'бригада', 'бригады', 'бригад')} без заявок</div>
          {reserve.map(r => row(r.engineer_id,
            `${r.engineer_name}: сегодня без заявок. Щёлкните — почему`,
            <>
              <div className="fleet-top">
                <Swatch mark={marks[r.engineer_id]} />
                <b className="fleet-id">{r.engineer_id}</b>
                <span className="fleet-num">резерв</span>
              </div>
              <div className="fleet-meta">{shortTransport(r.transport)} · зона «{r.cluster}» · {r.skills.join(', ').toLowerCase()}</div>
            </>))}
        </>
      )}

      {!plan && <div className="empty">Плана пока нет.</div>}
      {plan && !routes.length && !reserve.length && <div className="empty">В этой зоне бригад нет.</div>}
    </div>
  );
}
