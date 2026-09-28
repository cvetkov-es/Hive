// Карточка заявки: что это за заявка, почему она досталась этой бригаде — или
// что делать, если не досталась никому.
//
// Порядок карточки — порядок вопросов диспетчера. Сначала паспорт одной
// строкой: без него «почему у этой бригады» бессмысленно — не сказано, что за
// работа, когда клиент ждёт и сколько она займёт. Потом ответ (кто и когда),
// причины, время визита словами (ожидание окна называется, иначе «приедет в
// 15:41, начнёт в 20:00» читается как ошибка), список проверенных правил и
// только потом подробности.
//
// Объём ограничен намеренно. Постановщик назвал антипаттерном «портянку»:
// «довольно большое количество параметров… читать исполнитель не будет».
// Поэтому причин — до трёх, отклонённых — до трёх, полная таблица проверок
// открывается кнопкой, а кнопки стоят в подвале панели и видны всегда.
//
// Оговорка внизу не вежливость. Сравнение с альтернативами посчитано при
// НЕИЗМЕННЫХ маршрутах остальных бригад: без этой строки «дешевле у Петрова»
// читается как «надо было отдать Петрову», хотя перестройка дня Петрова
// стоила бы дороже всей экономии.

import type { AssignedExplanation, CancelledExplanation, Explanation, Passport,
              UnassignedExplanation } from '../api';
import type { Mark } from '../colors';

const MODE_ORDER = ['car', 'transit', 'bike', 'foot'] as const;
const MODE_RU: Record<string, string> = {
  car: 'Автомобиль', transit: 'Общественный транспорт',
  bike: 'Велосипед', foot: 'Пешком',
};
const MODE_ON: Record<string, string> = {
  car: 'на автомобиле', transit: 'на общественном транспорте',
  bike: 'на велосипеде', foot: 'пешком',
};

export function ExplainPanel({ explanation, error, loading, marks, busy,
                               onPickEngineer }: {
  explanation: Explanation | null;
  error: string;
  loading: boolean;
  marks: Record<string, Mark>;
  busy: boolean;
  onPickEngineer: (engineerId: string) => void;
}) {
  if (error) {
    return <div className="explain"><div className="un-reason">{error}</div></div>;
  }
  if (!explanation) {
    return (
      <div className="explain">
        <div className="note">
          {busy ? 'Считаем…'
            : loading ? 'Загружаем объяснение…'
            : 'Выберите заявку на карте или на ленте смены — покажем, почему она у этой '
              + 'бригады. Щелчок по бригаде в списке слева, по её линии на карте или по '
              + 'имени на ленте объяснит маршрут целиком.'}
        </div>
      </div>
    );
  }
  if (explanation.status === 'не назначена') return <Unassigned e={explanation} />;
  if (explanation.status === 'отменена') return <Cancelled e={explanation} />;
  return <Assigned e={explanation} marks={marks} onPickEngineer={onPickEngineer} />;
}

function PassportLine({ p, fallback }: { p?: Passport; fallback: string }) {
  return <div className="passport-line">{p?.summary ?? fallback}</div>;
}

/** Ключевые поля паспорта, которых нет в строке-сводке: приоритет по
 *  справочнику, откуда длительность, транспорт, оборудование, зона. */
function PassportDetails({ p }: { p: Passport }) {
  const rows: [string, string][] = [
    ['Вид работ', p.work_kind + (p.bk || p.hd ? ` (в выгрузке: ${[p.bk, p.hd].filter(Boolean).join(' / ')})` : '')],
    ['Приоритет', p.priority_text],
    ['Окно клиента', p.window_text],
    ['Длительность', p.duration_text],
    ['Транспорт', p.transport_text],
    ['Оборудование', p.equipment_text],
    ['Адрес', `${p.address}${p.district ? `, район ${p.district}` : ''}; зона «${p.zone}»`],
  ];
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

function Assigned({ e, marks, onPickEngineer }: {
  e: AssignedExplanation; marks: Record<string, Mark>;
  onPickEngineer: (engineerId: string) => void;
}) {
  const mark = marks[e.engineer_id];
  const sch = e.schedule;
  // Сводка отклонённых иногда уже стоит среди причин («единственная бригада…
  // остальные отсеялись») — второй раз её не повторяем.
  const summary = e.rejected_summary && !e.reasons.includes(e.rejected_summary)
    ? e.rejected_summary : '';
  const used = e.travel_by_mode[e.used_mode];

  return (
    <div className="explain">
      <PassportLine p={e.passport} fallback={`Заявка ${e.job_id}`} />

      <h3>
        {mark && <i className={`swatch ${mark.shape}`}
                    style={{ background: mark.color, marginRight: 6 }} />}
        {e.headline}
      </h3>
      {e.frozen && (
        <div className="chip-line"><span className="chip">заморожена событием: визит начат или бригада уже едет</span></div>
      )}

      <ul className="reasons">
        {e.reasons.map((r, i) => <li key={i}>{r}</li>)}
      </ul>

      {sch && (
        <>
          <div className="sub-head">Визит</div>
          <div className="visit">
            <span><b>{sch.seq}-я</b> из {sch.of} в маршруте</span>
            <span>приезд <b>{sch.arrive}</b></span>
            {sch.wait_min > 0
              ? <span className="wait">ждёт окна <b>{sch.wait_text}</b></span>
              : <span>начинает сразу</span>}
            <span>работа <b>{sch.start}–{sch.end}</b></span>
            <span>переезд {sch.leg_km.toFixed(1)} км, {sch.travel_min} мин</span>
          </div>
          {e.position && <div className="note" style={{ marginTop: 4 }}>{e.position}.</div>}
        </>
      )}

      {e.checked && e.checked.length > 0 && (
        <>
          <div className="sub-head">Что проверено</div>
          <ul className="checked">
            {e.checked.map(c => (
              <li key={c.key} className={c.ok ? '' : 'bad'}>
                <span className="mark" aria-label={c.ok ? 'выполнено' : 'нарушено'}>{c.ok ? '✓' : '✗'}</span>
                <span className="lbl">{c.label}</span>
                <span className="txt">{c.text}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {(e.rejected.length > 0 || summary) && (
        <>
          <div className="sub-head">Кто ещё рассматривался</div>
          <div className="rejected">
            {e.rejected.map(r => (
              <div key={r.engineer_id}><b>{r.engineer_id}</b> — {r.reason}</div>
            ))}
            {summary && <div className="note">{summary[0].toUpperCase() + summary.slice(1)}.</div>}
          </div>
        </>
      )}

      <div className="sub-head">Дорога сюда от предыдущей точки маршрута</div>
      <div className="modes">
        {MODE_ORDER.filter(m => e.travel_by_mode[m] !== undefined).map(m => {
          const on = m === e.used_mode;
          return (
            <div key={m} className={on ? 'on' : ''}>
              <span>{MODE_RU[m]}</span>
              <span className="n">{e.travel_by_mode[m]} мин</span>
              <span className="tag">{on ? '← эта бригада' : ''}</span>
            </div>
          );
        })}
      </div>
      <div className="note" style={{ marginTop: 4 }}>
        {used !== undefined && <>Бригада едет сюда {MODE_ON[e.used_mode] ?? ''} — {used} мин. </>}
        Остальные строки — та же дорога на другом транспорте, для сравнения.
        {e.passport?.transport_required && (
          <> Заявка требует: {e.passport.transport_required.toLowerCase()} — бригады на
            другом транспорте её не брали.</>
        )}
      </div>

      {e.passport && (
        <>
          <div className="sub-head">Паспорт заявки</div>
          <PassportDetails p={e.passport} />
        </>
      )}

      <button className="linkish" onClick={() => onPickEngineer(e.engineer_id)}>
        Маршрут {e.engineer_id} целиком →
      </button>

      <div className="caveat">
        {e.caveat[0].toUpperCase() + e.caveat.slice(1)}.
      </div>
    </div>
  );
}

/** Отменённая событием заявка: её никто не ждёт, поэтому ни причин
 *  назначения, ни «что можно сделать» у неё нет — только что с ней стало. */
function Cancelled({ e }: { e: CancelledExplanation }) {
  const cap = (t: string) => t[0].toUpperCase() + t.slice(1);
  return (
    <div className="explain">
      <h3>{e.headline}</h3>
      <div className="note">{cap(e.reason)}.</div>
      {e.detail && <div className="note" style={{ marginTop: 4 }}>{cap(e.detail)}.</div>}
    </div>
  );
}

function Unassigned({ e }: { e: UnassignedExplanation }) {
  return (
    <div className="explain">
      <PassportLine p={e.passport} fallback={`Заявка ${e.job_id}`} />
      <h3 className="no">{e.job_id} — не назначена</h3>
      <div className="un-reason">{e.reason}</div>
      {e.detail && <div className="note" style={{ marginTop: 4 }}>{e.detail}.</div>}

      {e.remedies.length > 0 && (
        <>
          <div className="sub-head">Что можно сделать</div>
          {e.remedies.map((r, i) => (
            <div className="remedy" key={i}>
              {r.text
                ? <span className="remedy-text">{r.text}</span>
                : <><b>{r.action}</b><span>{r.effect}</span></>}
            </div>
          ))}
        </>
      )}

      {e.blocked_by && e.blocked_by.length > 0 && (
        <>
          <div className="sub-head">Кто мог бы взять и почему не взял</div>
          <div className="rejected">
            {e.blocked_by.map(b => (
              <div key={b.engineer_id}><b>{b.engineer_id}</b> — {b.text}</div>
            ))}
          </div>
        </>
      )}

      {e.passport && (
        <>
          <div className="sub-head">Паспорт заявки</div>
          <PassportDetails p={e.passport} />
        </>
      )}

      <div className="caveat">
        Сдвинуть окно может только человек: это обещание, данное клиенту.
        Система показывает, что именно это даст, но не делает этого сама.
      </div>
    </div>
  );
}
