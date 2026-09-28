// «Сводка смены» — правая панель, пока ничего не выбрано.
//
// Первый экран справа — не подсказка, а то, ради чего диспетчер смотрит
// на план:
//   * незакрытые заявки — «в явном виде» и с причиной по каждой (ТЗ 2.2);
//   * бригады, упёршиеся в норматив 12 ч: они не возьмут новую заявку;
//   * резерв — кто сегодня без заявок (в раскладке B, где нет списка бригад);
//   * что будет, если каждая работа затянется — прогноз опозданий (ТЗ 2.5).
//
// Пустой список незакрытых значит две разные вещи, и путать их нельзя: «все
// заявки закрыты» и «плана ещё нет». Первое — результат, второе — его
// отсутствие. При полном парке список пуст, и это правда: планировщик
// закрывает все заявки во всех трёх регионах. Чтобы показать, как система
// объясняет отказ, здесь же кнопка «Оставить N бригад».

import type { Plan } from '../api';
import type { Mark } from '../colors';
import type { DelayRisk } from '../lib/delay.ts';
import { nearLimit } from '../lib/nearLimit.ts';
import { count, dur, plural, shortTransport } from '../text';
import { Swatch } from './FleetList';

export function DaySummary({ plan, marks, keep, fleetSize, busy, showReserve, delay, risk,
                             onDelay, onPickJob, onPickEngineer, onKeep }: {
  plan: Plan | null;
  marks: Record<string, Mark>;
  keep: number;
  fleetSize: number;
  busy: boolean;
  showReserve: boolean;
  delay: number;
  risk: DelayRisk | null;
  onDelay: (minutes: number) => void;
  onPickJob: (jobId: string) => void;
  onPickEngineer: (engineerId: string) => void;
  onKeep: (keep: number) => void;
}) {
  const rows = plan?.unassigned ?? [];
  const short = Math.max(2, Math.round(fleetSize * 0.4));
  const tight = nearLimit(plan?.routes ?? []);
  const reserve = plan?.reserve ?? [];

  return (
    <div className="summary">
      <p className="hint">
        Выберите заявку на карте или на ленте — покажем, почему она у этой бригады.
        Щелчок по бригаде объяснит её маршрут целиком.
      </p>

      <section className="sum-block">
        <div className="sum-head">
          <span>Не назначено</span>
          <span className={rows.length ? 'no' : 'count'}>
            {!plan ? '—' : rows.length ? `${rows.length} из ${plan.total_jobs}` : 'ни одной'}
          </span>
        </div>

        {keep > 0 && (
          <div className="keep-strip">
            <div>
              <b>Учебный сценарий:</b> в парке {plural(keep, 'оставлена', 'оставлено', 'оставлено')}{' '}
              {count(keep, 'бригада', 'бригады', 'бригад')} из {fleetSize}, чтобы показать
              причины отказа. Рабочий план дня — с полным парком.
            </div>
            <button disabled={busy} onClick={() => onKeep(0)}>Вернуть весь парк ({fleetSize})</button>
          </div>
        )}

        {rows.map(u => (
          <div key={u.job_id} className="un-row" role="button" tabIndex={0}
               onClick={() => onPickJob(u.job_id)}
               onKeyDown={e => { if (e.key === 'Enter') onPickJob(u.job_id); }}
               title="Щёлкните — полный разбор и все средства">
            <div className="un-id"><b>{u.job_id}</b> <span className="note">окно {u.window.replace('-', '–')}</span></div>
            <div className="un-reason">{u.reason}</div>
            {u.remedy && <div className="un-remedy">{u.remedy}</div>}
            <div className="un-addr">{u.address}</div>
          </div>
        ))}

        {!plan && (
          <div className="empty">{busy ? 'Считаем план — список заполнится, когда он придёт.'
            : 'Плана пока нет: здесь будут незакрытые заявки и что с ними делать.'}</div>
        )}
        {plan && !rows.length && keep === 0 && (
          <div className="empty">
            <div className="ok-line"><i />Весь день закрыт: незакрытых заявок нет.</div>
            <div>Хотите увидеть, как система объясняет отказ? Уберём часть бригад.</div>
            <button disabled={busy} onClick={() => onKeep(short)}>
              Оставить {count(short, 'бригаду', 'бригады', 'бригад')}
            </button>
          </div>
        )}
      </section>

      {tight.length > 0 && (
        <section className="sum-block">
          <div className="sum-head"><span>День почти 12 часов</span>
            <span className="count">{count(tight.length, 'бригада', 'бригады', 'бригад')}</span></div>
          <p className="note">Работают 11 часов и дольше при норме 12: длинную заявку таким бригадам уже не добавить.</p>
          {tight.map(t => (
            <button key={t.id} className="sum-row" onClick={() => onPickEngineer(t.id)}>
              <Swatch mark={marks[t.id]} />
              <b>{t.id}</b>
              <span className="warnish">день {dur(t.span)}</span>
              <span className="note">{t.left >= 0 ? `запас ${dur(t.left)}` : `сверх нормы ${dur(-t.left)}`}</span>
            </button>
          ))}
        </section>
      )}

      {showReserve && reserve.length > 0 && (
        <section className="sum-block">
          <div className="sum-head"><span>Резерв</span>
            <span className="count">{count(reserve.length, 'бригада свободна', 'бригады свободны', 'бригад свободны')}</span></div>
          {reserve.map(r => (
            <button key={r.engineer_id} className="sum-row" onClick={() => onPickEngineer(r.engineer_id)}
                    title="Щёлкните — почему бригада сегодня без заявок">
              <Swatch mark={marks[r.engineer_id]} />
              <b>{r.engineer_id}</b>
              <span className="note">{shortTransport(r.transport)} · {r.cluster}</span>
            </button>
          ))}
        </section>
      )}

      {plan && (
        <section className="sum-block whatif">
          <div className="sum-head"><span>Если каждая работа затянется</span></div>
          <div className="whatif-row">
            <input type="range" min={0} max={30} step={5} value={delay}
                   onChange={e => onDelay(Number(e.target.value))}
                   aria-label="Задержка на каждой заявке, минут" />
            <b>+{delay} мин</b>
          </div>
          <div className="whatif-txt">
            {!risk
              ? 'Сдвиньте ползунок: покажем на ленте, какие заявки выйдут за окно клиента.'
              : <>За окно клиента {plural(risk.late, 'выйдет', 'выйдут', 'выйдут')}{' '}
                  <b className={risk.late ? 'no' : 'yes'}>{count(risk.late, 'заявка', 'заявки', 'заявок')}</b>,
                  за двенадцатичасовой день — <b className={risk.overtime ? 'no' : 'yes'}>{count(risk.overtime, 'бригада', 'бригады', 'бригад')}</b>.
                  {risk.byEngineer.length > 0 && (
                    <> Первыми не успеют {risk.byEngineer.slice(0, 3).map(x => x.id).join(', ')}.</>
                  )}</>}
          </div>
          <p className="note">
            Пересчёт по готовому плану, без нового расчёта. Ожидание окна клиента
            съедает задержку: раньше открытия окна бригада всё равно не начала бы.
          </p>
        </section>
      )}
    </div>
  );
}
