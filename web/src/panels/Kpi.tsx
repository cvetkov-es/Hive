// Метрики плана. Первые две — обязательные по ТЗ 2.3: сколько исполнителей
// задействовано и какой суммарный пробег; пробег по каждой бригаде стоит в её
// строке списка и ленты.
//
// Под каждым числом — то же число в реальном дне (контрольная выборка
// организаторов): «8 против 12» и есть ответ, зачем всё это. Пробег реального
// дня посчитать нельзя — в данных нет порядка объезда, — и это сказано прямо,
// а не пропущено молча. Размер штата — в подсказке: рядом с числом бригад в
// работе он читался бы как сравнение.
//
// Просрочки поданы утверждением, а не переменной. В статическом плане их
// всегда ноль: заявка либо помещается в окно клиента, либо честно уходит в
// «не назначено» с причиной — поэтому «ноль», а не «0».
//
// Незакрытые заявки — «в явном виде» (ТЗ 2.2): если они есть, под числом
// назначенных стоит красная ссылка на их список с причинами.

import type { LiveDay, Plan } from '../api';
import { hours } from '../store';
import { count } from '../text';

export function Kpi({ plan, live, fleet, look, onUnassigned }: {
  plan: Plan | null; live?: LiveDay; fleet: number;
  look: 'grid' | 'strip';
  onUnassigned: () => void;
}) {
  if (!plan) {
    return <div className={`kpi-${look}`}><div className="kpi"><span className="kpi-label">План</span>
      <div className="kpi-value">—</div></div></div>;
  }
  const unassigned = plan.unassigned.length;
  const late = plan.late_jobs;

  return (
    <>
      {plan.warning && <div className="warnstrip">{plan.warning}</div>}
      <div className={`kpi-${look}`}>
        <div className="kpi" title={`Бригады, которым досталась хотя бы одна заявка. Всего в штате — ${fleet}`}>
          <span className="kpi-label">Бригад в работе</span>
          <div className="kpi-value">{plan.used_engineers}</div>
          {live && <div className="kpi-note">в реальный день <b>{live.engineers}</b></div>}
        </div>
        <div className="kpi" title={`Все маршруты вместе; по каждой бригаде — в её строке. В пути ${hours(plan.travel_min)}`}>
          <span className="kpi-label">Пробег</span>
          <div className="kpi-value">{plan.total_km.toFixed(1)}<span className="unit">км</span></div>
          {live && <div className="kpi-note">в реальный день — нет данных</div>}
        </div>
        <div className="kpi" title={live?.breakdown ? `Реальный день: ${live.breakdown}` : undefined}>
          <span className="kpi-label">Назначено</span>
          <div className="kpi-value">{plan.assigned}<span className="unit">из {plan.total_jobs}</span></div>
          {unassigned > 0
            ? <button className="kpi-link" onClick={onUnassigned}>
                не назначено {count(unassigned, 'заявка', 'заявки', 'заявок')} →
              </button>
            : live && <div className="kpi-note">в реальный день <b>{live.assigned}</b></div>}
        </div>
        <div className={`kpi ${late ? 'problem' : 'claim'}`}
             title="Опоздания не планируем: заявку, на которую не успеть, отдаём в «не назначено» с причиной">
          <span className="kpi-label">Просрочек</span>
          <div className="kpi-value">{late ? late : 'ноль'}</div>
          {live && <div className="kpi-note">в реальный день <b>{live.late}</b></div>}
        </div>
      </div>
    </>
  );
}
