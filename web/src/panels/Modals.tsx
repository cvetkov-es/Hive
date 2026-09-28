// Окна, которые открываются поверх пульта: полная таблица проверок, отчёт
// аудитора, ручное переназначение, приблизительные расстояния и индикатор
// счёта.
//
// Всё это спрятано за кнопкой не ради чистоты экрана, а потому что диспетчеру
// в обычной работе не нужно. Нужно — когда он не согласен с решением; тогда
// нужны все проверки до одной.

import { useEffect, useRef, useState } from 'react';
import type { AuditReport, CheckRow, Health, MoveCandidate, Passport, Plan } from '../api';
import { api } from '../api';
import type { Moved } from '../controller';
import { count, plural, signed } from '../text';

export function Modal({ title, onClose, children, foot, wide }: {
  title: string; onClose: () => void;
  children: React.ReactNode; foot?: React.ReactNode; wide?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  return (
    <div className="scrim" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true"
           aria-label={title} tabIndex={-1} ref={box}>
        <div className="modal-head">
          <span>{title}</span>
          <button onClick={onClose}>Закрыть</button>
        </div>
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

const yn = (ok: boolean) => <span className={ok ? 'yes' : 'no'}>{ok ? 'да' : 'нет'}</span>;

/** Клетка проверки, которую могли и не выполнять. «Нет» там, где время не
 *  считали, утверждало бы то, чего никто не проверял: бригада без навыка
 *  отсеивается сразу, и ни окно, ни время по ней не считаются. */
const ynq = (ok: boolean | null) => ok === null
  ? <span className="na" title="не проверяли: бригада отсеялась раньше">—</span>
  : yn(ok);

/** Полная таблица: бригада × навык × транспорт × зона × окно × оборудование ×
 *  время × вывод. Ровно те проверки, которыми пользовался планировщик. */
export function ChecksModal({ jobId, rows, onClose }: {
  jobId: string; rows: CheckRow[]; onClose: () => void;
}) {
  const fit = rows.filter(r => !r.is_current && r.time_ok === true).length;
  return (
    <Modal title={`Заявка ${jobId}: проверка по всему парку`} onClose={onClose} wide>
      <p className="note" style={{ marginTop: 0 }}>
        Это те же проверки, которыми планировщик отбирал бригаду, — не пересказ,
        а тот же код. Поэтому таблица не может разойтись с планом. Кроме текущей
        бригады, заявку {fit ? <>сейчас могли бы взять ещё <b>{count(fit, 'бригада', 'бригады', 'бригад')}</b></>
          : <>не может взять больше никто</>}.
      </p>
      <div className="table-scroll">
        <table className="checks">
          <thead>
            <tr>
              <th>Бригада</th><th>На чём ездит</th><th>Навык</th><th>Нужный транспорт</th>
              <th>Зона</th><th>Окно и смена</th><th>Оборудование</th><th>Успевает</th>
              <th className="n">Насколько длиннее маршрут</th><th>Вывод</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.engineer_id} className={r.is_current ? 'hl' : ''}>
                <td className="nowrap"><b>{r.engineer_id}</b>{r.is_current && <div className="note">сейчас у неё</div>}</td>
                <td>{r.transport}</td>
                <td>{yn(r.skill_ok)}</td>
                <td>{yn(r.transport_ok)}</td>
                <td>{yn(r.cluster_ok)}</td>
                <td>{ynq(r.window_ok)}</td>
                <td>{yn(r.equipment_ok)}</td>
                <td>{ynq(r.time_ok)}</td>
                <td className="n nowrap">
                  {r.added_km === null ? <span className="na">—</span> : `${signed(r.added_km, 2)} км`}
                </td>
                <td>{r.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">
        «—» — не проверяли: бригада отсеялась раньше, по навыку, транспорту, зоне
        или смене. «Насколько длиннее маршрут» у текущей бригады — сколько заявка
        добавила к её дню; у остальных — во что обошлась бы вставка в лучшее место
        их маршрута.
      </p>
    </Modal>
  );
}

export function AuditModal({ report, onClose }: {
  report: AuditReport; onClose: () => void;
}) {
  return (
    <Modal title={report.ok
             ? `План проверен: соблюдены все ${report.total} правил`
             : `План проверен: нарушено ${report.total - report.passed} из ${report.total} правил`}
           onClose={onClose}>
      <p className="note" style={{ marginTop: 0 }}>
        Готовый план проверяет отдельный скрипт. Код, который строил план, он не
        использует: сам заново считает время приезда и километры каждого маршрута
        и сверяет каждое правило. Поэтому ошибка планировщика не может спрятаться
        в его же проверке.
      </p>
      <div className="sub-head">Что проверено</div>
      <table>
        <thead><tr><th>№</th><th>Правило</th><th>Итог</th></tr></thead>
        <tbody>
          {report.checks.map(c => (
            <tr key={c.code}>
              <td>{c.code}</td>
              <td>
                {c.title}
                {c.problems.slice(0, 3).map((p, i) => (
                  <div key={i} className="no" style={{ fontSize: 12 }}>{p}</div>
                ))}
              </td>
              <td>{yn(c.ok)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  );
}

/** Что именно не прошло самопроверку при старте.
 *
 *  Плашка в шапке говорит «не прошло проверок: 2 из 13», и на этом её работа
 *  заканчивается. Дальше диспетчеру нужно не «что-то сломано», а строка с
 *  именем команды, которой это чинится, — иначе предупреждение превращается
 *  в тревожный шум, который учатся игнорировать. */
export function HealthModal({ health, onClose }: {
  health: Health; onClose: () => void;
}) {
  return (
    <Modal title={`Самопроверка окружения: ${health.passed} из ${health.total}`}
           onClose={onClose}>
      <p className="note" style={{ marginTop: 0 }}>
        Провал самопроверки не останавливает работу: план, карта и объяснения
        считаются из того, что на месте. Но там, где данных нет, экран покажет
        не «ничего», а пустоту, поэтому знать о провале нужно заранее.
      </p>
      <table>
        <thead><tr><th>Проверка</th><th>Итог</th><th>Что показала</th></tr></thead>
        <tbody>
          {health.checks.map(c => (
            <tr key={c.name} className={c.ok ? '' : 'hl'}>
              <td>{c.name}</td>
              <td>{yn(c.ok)}</td>
              <td className={c.ok ? '' : 'no'}>{c.detail || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  );
}

/** Приблизительные расстояния — окном, а не всплывающей подсказкой на
 *  плашке: на показе экрана подсказку не видно вовсе. */
export function EstimatedModal({ plan, onClose, onPickJob }: {
  plan: Plan; onClose: () => void; onPickJob: (jobId: string) => void;
}) {
  const rows = plan.routes.flatMap(r => r.stops.filter(s => s.estimated)
    .map(s => ({ ...s, engineer_id: r.engineer_id })));
  return (
    <Modal title={`${count(rows.length, 'адрес', 'адреса', 'адресов')} вне таблицы расстояний`}
           onClose={onClose}>
      <p style={{ marginTop: 0 }}>
        Расстояния и время в пути между адресами выгрузки посчитаны заранее, по
        настоящим дорогам. {rows.length === 1 ? 'Этого адреса' : 'Этих адресов'} в
        таблице нет: {plural(rows.length, 'он появился', 'они появились', 'они появились')} уже
        в течение дня — новая заявка события или адрес, введённый диспетчером.
      </p>
      <p>
        Расстояние до {rows.length === 1 ? 'него' : 'них'} считается по прямой и
        умножается на 1.45 — насколько дороги в среднем длиннее прямой, по
        замеру на этих же данных. Это оценка, а не измерение: километры и минуты
        по таким участкам приблизительные, а на карте участок нарисован пунктиром.
      </p>
      <table>
        <thead><tr><th>Заявка</th><th>Бригада</th><th>Адрес</th><th className="n">Участок</th></tr></thead>
        <tbody>
          {rows.map(s => (
            <tr key={s.job_id} className="clickable" onClick={() => { onPickJob(s.job_id); onClose(); }}>
              <td><b>{s.job_id}</b></td>
              <td>{s.engineer_id}</td>
              <td>{s.address}</td>
              <td className="n nowrap">≈ {s.km.toFixed(1)} км, {s.travel_min} мин</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  );
}

/** Индикатор счёта. Время известно заранее — это лимит, который дан
 *  планировщику, поэтому полоса честная, а не бегущая. Через показ экрана по
 *  видеосвязи смена подписи на кнопке не видна вовсе: 10–20 секунд без
 *  признаков жизни выглядят как зависание. */
export function BusyOverlay({ label, limit, since }: {
  label: string; limit: number; since: number;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(t);
  }, []);
  const elapsed = Math.max(0, (now - since) / 1000);
  const left = Math.ceil(limit - elapsed);
  const share = Math.min(1, elapsed / limit);
  return (
    <div className="busy-scrim" role="status" aria-live="polite">
      <div className="busy-card">
        <div className="busy-title">Считаем план: около {limit} с</div>
        <div className="busy-what">{label}</div>
        <div className="busy-bar"><i style={{ width: `${share * 100}%` }} /></div>
        <div className="busy-left">
          {left > 0 ? `осталось примерно ${left} с` : 'почти готово: собираем ответ…'}
        </div>
        <p className="note">
          Планировщик перебирает варианты ровно столько, сколько ему дали, и
          отдаёт лучший найденный. Потом покажем результат и что изменилось.
        </p>
      </div>
    </div>
  );
}

/** Что держит заявку, если отдать её некому: какое требование отсеивает
 *  больше всего бригад, потом — какое отсеивает оставшихся. Имена требований
 *  из паспорта заявки. Одной главной причины мало: «держит навык» при том,
 *  что ещё четыре бригады не подходят по зоне, — полуправда. */
function holdText(others: MoveCandidate[], p?: Passport): string {
  const reqs = [
    { fails: (r: MoveCandidate) => !r.skill_ok,
      main: p ? `навыка «${p.skill}»` : 'навыка', by: p ? `по навыку «${p.skill}»` : 'по навыку' },
    { fails: (r: MoveCandidate) => !r.transport_ok,
      main: p?.transport_required ? `транспорта «${p.transport_required}»` : 'транспорта',
      by: p?.transport_required ? `по транспорту («${p.transport_required}»)` : 'по транспорту' },
    { fails: (r: MoveCandidate) => !r.cluster_ok,
      main: p ? `зоны «${p.zone}»` : 'зоны', by: p ? `по зоне «${p.zone}»` : 'по зоне' },
    { fails: (r: MoveCandidate) => !r.equipment_ok,
      main: p ? `оборудования (${p.equipment_text})` : 'оборудования',
      by: p ? `по оборудованию (${p.equipment_text})` : 'по оборудованию' },
  ];
  let rest = others;
  const parts: string[] = [];
  while (rest.length) {
    const best = reqs.map(q => ({ q, n: rest.filter(q.fails).length }))
      .sort((a, b) => b.n - a.n)[0];
    if (!best || best.n === 0) {
      parts.push(`${parts.length ? 'ещё у' : 'у'} ${count(rest.length, 'бригады', 'бригад', 'бригад')} `
                 + 'с подходящим профилем заявка не помещается в день');
      break;
    }
    parts.push(parts.length
      ? `ещё ${best.n} не ${plural(best.n, 'подходит', 'подходят', 'подходят')} ${best.q.by}`
      : `главное препятствие — требование ${best.q.main}: ему не отвечают ${best.n} `
        + `из ${count(others.length, 'бригады', 'бригад', 'бригад')}`);
    rest = rest.filter(r => !best.q.fails(r));
  }
  return parts.join('; ') || 'подходящих бригад нет';
}

/** Ручное переназначение. Постановщик сказал прямо: «такую функциональность
 *  необходимо заложить». Выпадающий список, а не перетаскивание: перетащить
 *  точку можно куда угодно, а выбрать — только из тех, у кого есть вердикт.
 *  И каждая строка сразу называет цену. */
export function MoveModal({ planId, jobId, currentEngineer, passport, onClose, onMoved }: {
  planId: string; jobId: string; currentEngineer: string | null;
  passport?: Passport;
  onClose: () => void;
  onMoved: (m: Moved) => void;
}) {
  const [rows, setRows] = useState<MoveCandidate[] | null>(null);
  const [locked, setLocked] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    api.validateMove(planId, jobId)
      .then(r => { setRows(r.candidates); setLocked(r.locked ?? null); })
      .catch(e => setError(String((e as Error).message)));
  }, [planId, jobId]);

  const apply = async (engineerId: string) => {
    setBusy(engineerId); setError('');
    try {
      const r = await api.move(planId, jobId, engineerId);
      onMoved({ planId: r.plan.plan_id, jobId, engineerId,
                addedKm: r.added_km, position: r.position });
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy('');
    }
  };

  const others = (rows ?? []).filter(r => !r.is_current);
  const ok = others.filter(r => r.allowed)
    .sort((a, b) => (a.added_km ?? 0) - (b.added_km ?? 0));
  const cheapest = ok[0];

  return (
    <Modal title={`${currentEngineer ? 'Переназначить' : 'Назначить вручную'} заявку ${jobId}`}
           onClose={onClose} wide>
      {error && <div className="un-reason" style={{ marginBottom: 8 }}>{error}</div>}
      {rows && locked && (
        <div className="verdict bad">
          Переназначить нельзя: {locked}.
        </div>
      )}
      {rows && !locked && (
        ok.length > 0 ? (
          <div className="verdict ok">
            {plural(ok.length, 'Допустима', 'Допустимы', 'Допустимо')}{' '}
            {count(ok.length, 'бригада', 'бригады', 'бригад')}
            {cheapest && cheapest.added_km !== null && (
              <>: дешевле всех — {cheapest.engineer_id}, маршрут {signed(cheapest.added_km, 2)} км</>
            )}.
          </div>
        ) : (
          <div className="verdict bad">
            Допустимых вариантов нет: {holdText(others, passport)}.
          </div>
        )
      )}
      <p className="note" style={{ marginTop: 0 }}>
        Показан весь парк, включая тех, кто заявку взять не может: скрытая
        строка читается как «система что-то прячет». Цена посчитана при
        неизменных маршрутах остальных бригад.
        {currentEngineer && <> Сейчас заявка у <b>{currentEngineer}</b>.</>}
      </p>
      {!rows && !error && <div className="note">Считаем цену по каждой бригаде…</div>}
      {rows && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Бригада</th><th>На чём ездит</th>
                <th className="n">Пробег</th><th className="n">День</th>
                <th className="n">Место в маршруте</th><th>Вывод</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.engineer_id} className={r.is_current ? 'hl' : ''}>
                  <td className="nowrap"><b>{r.engineer_id}</b></td>
                  <td>{r.transport}</td>
                  <td className="n nowrap">
                    {r.added_km === null ? '—' : `${signed(r.added_km, 2)} км`}
                  </td>
                  <td className="n nowrap">
                    {r.added_min === null ? '—' : `${signed(r.added_min)} мин`}
                  </td>
                  <td className="n">{r.position ?? '—'}</td>
                  <td className={r.allowed || r.is_current ? '' : 'no'}>{r.verdict}</td>
                  <td>
                    {!locked && r.allowed && !r.is_current && (
                      <button disabled={!!busy} onClick={() => apply(r.engineer_id)}>
                        {busy === r.engineer_id ? 'Применяем…' : 'Отдать сюда'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
