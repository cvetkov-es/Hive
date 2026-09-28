// Событие в середине дня и разница «до и после».
//
// ТЗ 2.1.6 требует одно событие на выбор, здесь их три: новая заявка, отмена,
// недоступность исполнителя.
//
// Момент события — обязательное поле, а не удобство. От него зависит, что уже
// нельзя трогать: заявки, к которым бригада приступила, и визиты, к которым
// она уже едет, заморожены. Остальные бригады продолжают день с адреса
// последней начатой работы, а если ничего не начинали — со своей стартовой
// точки, не раньше момента события.
//
// Форма стоит в правой панели, а не в окне поверх экрана: карта и лента
// остаются видны, пока диспетчер выбирает, что отменить или кто выбыл.
//
// Новая заявка по умолчанию — показательная авария: сценарий показа не должен
// удлиняться. Своя заявка задаётся полями ТЗ 2.4: вид работ, окно, длительность
// по нормативу, транспорт, оборудование, адрес или координаты. Обычная заявка
// встаёт в свободный интервал, не трогая остальной план; авария или «Срочная»
// перепланирует остаток дня (разъяснение куратора от 22.09).

import { useEffect, useState } from 'react';
import type { Diff, EventBody, Geocode, Plan } from '../api';
import type { EventPick } from '../controller';
import { count, HHMM_RE, settleTime, signed, typingTime } from '../text';
import { hhmm, toMin } from '../store';

const KINDS = [
  { key: 'urgent', label: 'Новая заявка' },
  { key: 'cancel', label: 'Отмена заявки' },
  { key: 'unavailable', label: 'Исполнитель выбыл' },
] as const;

// Виды работ формы и их нормативы. Числа — из Нормативы.xlsx через
// backend/app/io/normatives.py: на месте = норматив − 20 мин дороги (дорогу
// считаем по карте) + 10 мин на парковку и подход.
const WORK = {
  emergency: { label: 'Авария', skill: 3, norm: 100, transport: 'car', priority: 'Срочная', equipment: {} },
  connect: { label: 'Подключение', skill: 2, norm: 90, transport: '', priority: 'Обычная', equipment: {} },
  local: { label: 'Локальные работы', skill: 1, norm: 50, transport: '', priority: 'Обычная', equipment: {} },
  extra: { label: 'Дозаказ оборудования', skill: 2, norm: 40, transport: '', priority: 'Обычная', equipment: { router: 1 } },
} as const;
type WorkKey = keyof typeof WORK;
const onSite = (norm: number) => norm - 20 + 10;

const TRANSPORTS = [
  { key: '', label: 'любой' },
  { key: 'car', label: 'Автомобиль' },
  { key: 'transit', label: 'Общественный транспорт' },
  { key: 'bike', label: 'Велосипед' },
  { key: 'foot', label: 'Пешком' },
];
const EQUIPMENT = [
  { key: 'router', label: 'Роутер' },
  { key: 'tvbox', label: 'ТВ-приставка' },
  { key: 'speaker', label: 'Умная колонка' },
] as const;

/** Поле времени ЧЧ:ММ в 24-часовом виде независимо от локали браузера. */
function TimeField({ id, value, onChange, label }: {
  id: string; value: string; onChange: (v: string) => void; label: string;
}) {
  const bad = !HHMM_RE.test(value);
  return (
    <input id={id} className={`time-field ${bad ? 'invalid' : ''}`} type="text"
           inputMode="numeric" autoComplete="off" placeholder="ЧЧ:ММ" maxLength={5}
           aria-label={label} aria-invalid={bad} value={value}
           onChange={e => onChange(typingTime(e.target.value))}
           onBlur={e => onChange(settleTime(e.target.value))} />
  );
}

export function EventForm({ plan, busy, onClose, onApply, registerPick }: {
  plan: Plan;
  busy: boolean;
  onClose: () => void;
  onApply: (body: EventBody, replan: boolean) => Promise<string>;
  /** Щелчок по заявке или бригаде на пульте, пока форма открыта, подставляет
   *  её в «Какую заявку отменил клиент» или «Кто выбыл». */
  registerPick?: (fn: EventPick | null) => void;
}) {
  const last = typeof plan.meta?.at_min === 'number' ? plan.meta.at_min : null;
  const [kind, setKind] = useState<string>('urgent');
  const [at, setAt] = useState(hhmm(Math.max(15 * 60 + 40, last ?? 0)));
  const [error, setError] = useState('');

  // --- новая заявка ---
  const [custom, setCustom] = useState(false);
  const [address, setAddress] = useState('');
  const [work, setWork] = useState<WorkKey>('connect');
  const [priority, setPriority] = useState('Обычная');
  const [winFrom, setWinFrom] = useState(at);
  const [winTo, setWinTo] = useState(hhmm(Math.min(toMin(at) + 120, 23 * 60 + 30)));
  const [duration, setDuration] = useState(String(onSite(WORK.connect.norm)));
  const [transport, setTransport] = useState('');
  const [equipment, setEquipment] = useState<Record<string, number>>({});
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const nextId = 1 + [...plan.routes.flatMap(r => r.stops.map(s => s.job_id)),
                      ...plan.unassigned.map(u => u.job_id)]
    .filter(id => id.startsWith('НОВАЯ-')).length;
  const [jobId, setJobIdText] = useState(`НОВАЯ-${nextId}`);

  const atOk = HHMM_RE.test(at);
  const atMin = atOk ? toMin(at) : (last ?? 0);
  const future = plan.routes.flatMap(r =>
    r.stops.filter(s => toMin(s.start) > atMin).map(s => ({ ...s, engineer_id: r.engineer_id })));
  const working = plan.routes.filter(r => r.stops.length);

  const [cancelJob, setCancelJob] = useState('');
  const [engineerId, setEngineerId] = useState('');
  const pickedJob = future.some(s => s.job_id === cancelJob) ? cancelJob : future[0]?.job_id ?? '';
  // Бригада проверяется так же, как заявка: после пересчёта или ручной
  // передачи выбранной раньше бригады в плане может уже не быть.
  const pickedEngineer = working.some(r => r.engineer_id === engineerId)
    ? engineerId : working[0]?.engineer_id ?? '';

  useEffect(() => {
    if (!registerPick) return;
    registerPick(({ jobId, engineerId: id }) => {
      if (kind === 'cancel' && jobId && future.some(s => s.job_id === jobId)) {
        setCancelJob(jobId); return true;
      }
      if (kind === 'unavailable' && id && working.some(r => r.engineer_id === id)) {
        setEngineerId(id); return true;
      }
      return false;
    });
    return () => registerPick(null);
  });

  const chooseWork = (w: WorkKey) => {
    setWork(w);
    setDuration(String(onSite(WORK[w].norm)));
    setTransport(WORK[w].transport);
    setPriority(WORK[w].priority);
    setEquipment({ ...WORK[w].equipment });
  };

  const emergency = work === 'emergency' || priority === 'Срочная';
  const coordsGiven = lat.trim() !== '' || lon.trim() !== '';
  const coordsOk = !coordsGiven || (Number.isFinite(Number(lat)) && Number.isFinite(Number(lon))
    && lat.trim() !== '' && lon.trim() !== '');
  const durationOk = /^\d+$/.test(duration) && Number(duration) > 0 && Number(duration) <= 600;
  const windowOk = HHMM_RE.test(winFrom) && HHMM_RE.test(winTo) && winTo >= winFrom;

  const problems: string[] = [];
  if (!atOk) problems.push('время события — ЧЧ:ММ, например 15:40');
  if (last !== null && atOk && atMin < last) {
    problems.push(`предыдущее событие было в ${hhmm(last)}: время назад не идёт`);
  }
  if (kind === 'urgent' && custom) {
    if (!windowOk) problems.push('окно клиента — «с» и «до» в виде ЧЧ:ММ, конец не раньше начала');
    if (!durationOk) problems.push('длительность — целое число минут');
    if (!address.trim() && !coordsGiven) problems.push('нужен адрес или координаты');
    if (!coordsOk) problems.push('координаты — два числа: широта и долгота');
    if (!jobId.trim()) problems.push('нужен номер заявки');
  }
  if (kind === 'cancel' && !pickedJob) problems.push(`после ${at} нет заявок, которые ещё не начаты`);
  if (kind === 'unavailable' && !pickedEngineer) problems.push('нет бригад в работе');

  const submit = async () => {
    setError('');
    const body: EventBody = { kind, at };
    let replan = true;
    if (kind === 'cancel') body.job_id = pickedJob;
    if (kind === 'unavailable') body.engineer_id = pickedEngineer;
    if (kind === 'urgent' && !custom && address.trim()) body.address = address.trim();
    if (kind === 'urgent' && custom) {
      const eq = Object.fromEntries(Object.entries(equipment).filter(([, n]) => n > 0));
      body.job = {
        id: jobId.trim(), address: address.trim(),
        ...(coordsGiven ? { lat: Number(lat), lon: Number(lon) } : {}),
        duration_min: Number(duration), window_start: winFrom, window_end: winTo,
        priority, required_skill: WORK[work].skill,
        required_transport: transport, equipment: eq,
      };
      replan = emergency;
    }
    const err = await onApply(body, replan);
    if (err) setError(err);
  };

  const w = WORK[work];

  return (
    <div className="event-form">
      <div className="field">
        <span className="label">Что случилось</span>
        <div className="seg seg-fill" role="radiogroup" aria-label="Что случилось">
          {KINDS.map(k => (
            <button key={k.key} role="radio" aria-checked={kind === k.key}
                    className={kind === k.key ? 'on' : ''}
                    onClick={() => { setKind(k.key); setError(''); }}>
              {k.label}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label htmlFor="ev-at">Время события</label>
        <div className="inline">
          <TimeField id="ev-at" label="Время события" value={at} onChange={setAt} />
          <span className="note">ЧЧ:ММ, 24 часа{last !== null && ` · не раньше ${hhmm(last)}, предыдущего события`}</span>
        </div>
      </div>

      {kind === 'urgent' && (
        <div className="field">
          <span className="label">Какая заявка</span>
          <div className="choice">
            <label>
              <input type="radio" name="ev-src" checked={!custom} onChange={() => setCustom(false)} />
              {' '}показательная авария — в самой дефицитной зоне региона
            </label>
            <label>
              <input type="radio" name="ev-src" checked={custom} onChange={() => setCustom(true)} />
              {' '}своя заявка — вид работ, окно, адрес
            </label>
          </div>
        </div>
      )}

      {kind === 'urgent' && !custom && (
        <div className="field">
          <label htmlFor="ev-addr">Адрес аварии</label>
          <input id="ev-addr" type="text" value={address}
                 placeholder="пусто — показательная точка в дефицитной зоне"
                 onChange={e => setAddress(e.target.value)} />
        </div>
      )}

      {kind === 'urgent' && custom && (
        <>
          <div className="field">
            <label htmlFor="ev-work">Вид работ</label>
            <select id="ev-work" value={work} onChange={e => chooseWork(e.target.value as WorkKey)}>
              {(Object.keys(WORK) as WorkKey[]).map(k => <option key={k} value={k}>{WORK[k].label}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ev-prio">Приоритет</label>
            <div className="inline">
              <select id="ev-prio" value={work === 'emergency' ? 'Срочная' : priority}
                      disabled={work === 'emergency'} onChange={e => setPriority(e.target.value)}>
                <option value="Обычная">Обычная</option>
                <option value="Срочная">Срочная</option>
              </select>
              <span className="note">
                {emergency ? 'перепланирует остаток дня, около 10 с'
                  : 'встанет в свободный интервал, остальной план не тронут'}
              </span>
            </div>
          </div>
          <div className="field">
            <span className="label">Окно клиента</span>
            <div className="inline">
              с <TimeField id="ev-from" label="Окно клиента, начало" value={winFrom} onChange={setWinFrom} />
              до <TimeField id="ev-to" label="Окно клиента, конец" value={winTo} onChange={setWinTo} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="ev-dur">Длительность</label>
            <div className="inline">
              <input id="ev-dur" className="num-field" type="text" inputMode="numeric"
                     value={duration} onChange={e => setDuration(e.target.value.replace(/\D/g, '').slice(0, 3))} />
              <span className="note">
                мин на месте; по нормативу {onSite(w.norm)}: {w.norm} мин минус 20 мин
                дороги плюс 10 мин на парковку и подход
              </span>
            </div>
          </div>
          <div className="field">
            <label htmlFor="ev-tr">Нужный транспорт</label>
            <select id="ev-tr" value={transport} onChange={e => setTransport(e.target.value)}>
              {TRANSPORTS.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </div>
          <div className="field">
            <span className="label">Оборудование</span>
            <div className="inline">
              {EQUIPMENT.map(x => (
                <label key={x.key} className="eq">
                  {x.label}{' '}
                  <input type="text" inputMode="numeric" className="num-field small"
                         aria-label={`${x.label}, штук`}
                         value={String(equipment[x.key] ?? 0)}
                         onChange={e => setEquipment(v => ({
                           ...v, [x.key]: Math.min(9, Number(e.target.value.replace(/\D/g, '') || 0)) }))} />
                </label>
              ))}
            </div>
          </div>
          <div className="field">
            <label htmlFor="ev-caddr">Адрес</label>
            <input id="ev-caddr" type="text" value={address}
                   placeholder="например: Кашира, ул. Победы, д. 9"
                   onChange={e => setAddress(e.target.value)} />
          </div>
          <div className="field">
            <span className="label">или координаты</span>
            <div className="inline">
              <input type="text" className="coord" placeholder="широта" aria-label="Широта"
                     value={lat} onChange={e => setLat(e.target.value.replace(',', '.'))} />
              <input type="text" className="coord" placeholder="долгота" aria-label="Долгота"
                     value={lon} onChange={e => setLon(e.target.value.replace(',', '.'))} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="ev-id">Номер заявки</label>
            <input id="ev-id" type="text" value={jobId} onChange={e => setJobIdText(e.target.value)} />
          </div>
        </>
      )}

      {kind === 'cancel' && (
        <div className="field">
          <label htmlFor="ev-job">Какую заявку отменил клиент</label>
          <select id="ev-job" value={pickedJob} onChange={e => setCancelJob(e.target.value)}>
            {future.map(s => (
              <option key={s.job_id} value={s.job_id}>
                {s.job_id} — {s.engineer_id}, начало {s.start}
              </option>
            ))}
          </select>
        </div>
      )}

      {kind === 'unavailable' && (
        <div className="field">
          <label htmlFor="ev-eng">Кто выбыл</label>
          <select id="ev-eng" value={pickedEngineer}
                  onChange={e => setEngineerId(e.target.value)}>
            {working.map(r => (
              <option key={r.engineer_id} value={r.engineer_id}>
                {r.engineer_id} — {count(r.jobs, 'заявка', 'заявки', 'заявок')}, {r.km.toFixed(1)} км
              </option>
            ))}
          </select>
        </div>
      )}

      <p className="note">
        {kind === 'urgent' && !custom && (address.trim()
          ? <>Координаты найдутся в офлайновом справочнике адресов; если адреса там
            нет — один запрос к геокодеру, а без сети система откажет словами, а не
            поставит точку наугад. Куда встала точка, покажем после расчёта.
            Дороги до нового адреса запросим у картографического сервиса; нет сети —
            посчитаем по прямой с поправкой на извилистость дорог и пометим расстояния
            как приблизительные.</>
          : <>Авария встанет в самой дефицитной зоне региона, по адресу, которого нет в
            таблице расстояний: дороги до него запросим у картографического сервиса,
            а без сети посчитаем по прямой с поправкой на извилистость и пометим
            расстояния как приблизительные. Одним
            событием видно и зону обслуживания, и требование автомобиля, и заморозку
            уже начатого. Авария перепланирует остаток дня — это около 10 с.</>)}
        {kind === 'urgent' && custom && (
          <>Обычная заявка встаёт в свободный интервал между уже запланированными
            работами и не перестраивает план; места нет — останется в «Не
            назначено» с причиной. Авария или «Срочная» перепланирует остаток дня.
            Зона определяется по адресу: Кашира, Ступино и Домодедово обслуживают
            свои бригады.</>
        )}
        {kind === 'cancel' && 'Отменённая заявка исчезает из плана и не попадает в неназначенные: её никто не ждёт. Заявку можно выбрать и щелчком по карте или ленте.'}
        {kind === 'unavailable' && 'Выполненное этой бригадой останется за ней. Перераспределяется только то, к чему она ещё не приступила. Бригаду можно выбрать и щелчком по ленте, списку или её линии на карте.'}
      </p>
      <p className="note">
        Всё, к чему бригады приступили до {atOk ? at : '…'}, и визиты, к которым они уже
        едут, заморожены: прошлое не переписывается, иначе на карте бригады
        оказались бы утром в другом районе.
      </p>
      {error && <div className="verdict bad">{error}</div>}
      <div className="form-foot">
        {problems.length > 0 && <div className="foot-hint no">{problems[0]}</div>}
        <div className="form-btns">
          <button onClick={onClose}>Отменить</button>
          <button className="primary" disabled={busy || problems.length > 0} onClick={submit}>
            {busy ? 'Считаем…'
              : kind === 'urgent' && custom && !emergency ? 'Добавить в план' : 'Перепланировать'}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Название места от геокодера — до района и города, без округа, индекса и
 *  страны: «44/6, Бирюлёвская улица, район Бирюлёво Восточное, Москва». */
function placeName(name: string): string {
  const parts = name.split(',').map(s => s.trim()).filter(Boolean);
  const drop = /федеральный округ|^\d{6}$|^Россия$/i;
  return parts.filter(s => !drop.test(s)).slice(0, 4).join(', ');
}

/** Разница «до и после»: несколько строк текстом, остальное — на карте и ленте. */
/** Как посчитаны расстояния до новой точки — своими словами: пометка
 *  сервера называет сервис дорог по имени и приводит технические причины
 *  отказа («HTTP 502»). Её текст остаётся в подсказке. */
function roadsLine(g: Geocode) {
  const line = g.roads === 'по дорогам'
    ? 'Расстояния до неё — по дорогам, от картографического сервиса.'
    : g.roads === 'по прямой'
      ? 'Дороги до неё взять не удалось: расстояния по прямой с поправкой на '
        + 'извилистость дорог — приблизительные.'
      : g.note ? `${g.note[0].toUpperCase()}${g.note.slice(1)}.` : '';
  if (!line) return null;
  return <><br /><span title={g.roads ? g.note : undefined}>{line}</span></>;
}

export function DiffPanel({ diff, events, geocode, busy, onBack, onPickJob }: {
  diff: Diff; events: string[]; geocode: Geocode | null; busy: boolean;
  onBack: () => void; onPickJob: (jobId: string) => void;
}) {
  const m = diff.metrics;
  const enRoute = diff.en_route ?? 0;
  const rejected = diff.rejected ?? [];
  const cancelled = diff.cancelled ?? [];

  // Для «Назначено» больше — лучше, для остальных меньше — лучше. Цвет не
  // единственный признак: рядом стоит слово, иначе на проекторе и у
  // дальтоника плюс одна закрытая заявка читалась бы как беда.
  const line = (key: string, title: string, unit: string, digits: number, moreIsBetter: boolean) => {
    const v = m[key];
    if (!v) return null;
    const d = v.delta;
    const good = moreIsBetter ? d > 0 : d < 0;
    // Отмена клиентом уменьшает «Назначено», но план от этого не хуже:
    // заявку просто больше никто не ждёт.
    const byCancel = key === 'assigned' && cancelled.length > 0 && d === -cancelled.length;
    const cls = d === 0 || byCancel ? 'note' : good ? 'yes' : 'no';
    const word = d === 0 ? 'без изменений'
      : byCancel ? (cancelled.length === 1 ? 'заявку отменил клиент' : 'заявки отменил клиент')
      : good ? 'лучше' : 'хуже';
    return (
      <tr key={key}>
        <td>{title}</td>
        <td className="n nowrap">{v.before.toFixed(digits)} → <b>{v.after.toFixed(digits)}</b>{unit}</td>
        <td className={`n ${cls}`}>{d === 0 ? word : <><span className="nowrap">{signed(d, digits)}{unit}</span> · {word}</>}</td>
      </tr>
    );
  };

  return (
    <div className="explain">
      <h3>{diff.event}</h3>
      {diff.mode_text && (
        <div className="chip-line">
          <span className="chip">{diff.mode_text}</span>
        </div>
      )}
      {events.length > 1 && (
        <>
          <div className="sub-head">Цепочка событий дня</div>
          <ol className="events">{events.map((t, i) => <li key={i}>{t}</li>)}</ol>
        </>
      )}
      <div className="note" style={{ marginBottom: 8 }}>
        Заморожено {count(diff.frozen, 'визит', 'визита', 'визитов')}:{' '}
        {enRoute === 0 ? 'все начаты до события'
          : <>{count(diff.frozen - enRoute, 'начат', 'начаты', 'начаты')} до события,
            {' '}{enRoute === 1 ? 'к одному клиенту бригада уже едет'
              : `к ${enRoute} клиентам бригады уже едут`}</>}.
        {' '}Эту часть дня событие не трогает.
      </div>

      {geocode && (
        <div className="note" style={{ marginBottom: 8 }}>
          Адрес «{geocode.address}» — точка встала:{' '}
          {geocode.name ? <b>{placeName(geocode.name)}</b> : <>{geocode.lat.toFixed(5)}, {geocode.lon.toFixed(5)}</>}
          {geocode.km_from_depot != null && geocode.depot && (
            <>, в {geocode.km_from_depot.toFixed(1)} км от места выезда бригад ({geocode.depot})</>
          )}
          {' '}({geocode.source === 'кэш' ? 'из офлайнового справочника адресов'
                                           : 'один запрос к геокодеру'}).
          {roadsLine(geocode)}
        </div>
      )}

      {rejected.length > 0 && (
        <div className="rejected-block">
          <div className="sub-head no" style={{ marginTop: 0 }}>Не удалось принять</div>
          {rejected.map(r => (
            <div key={r.job_id} className="rej">
              <b>{r.job_id}</b> — {r.reason}.
              {r.detail && <div className="note">{r.detail[0].toUpperCase() + r.detail.slice(1)}.</div>}
              <button className="linkish" onClick={() => onPickJob(r.job_id)}>
                Почему и что можно сделать →
              </button>
            </div>
          ))}
        </div>
      )}

      {diff.added.map(a => (
        <div className="remedy" key={a.job_id}>
          <b>{a.job_id} — принята</b>
          <span>{a.to}, визит №{a.seq}. </span>
          <button className="linkish" onClick={() => onPickJob(a.job_id)}>Почему эта бригада →</button>
        </div>
      ))}

      {cancelled.length > 0 && (
        <>
          <div className="sub-head">Отменены клиентом</div>
          <div className="rejected">
            {cancelled.map(x => (
              <div key={x.job_id}><b>{x.job_id}</b>{x.from ? ` — была у ${x.from}` : ''}</div>
            ))}
          </div>
        </>
      )}

      {diff.moved.length > 0 && (
        <>
          <div className="sub-head">Сменили исполнителя</div>
          <div className="rejected">
            {diff.moved.map(x => (
              <div key={x.job_id}>{x.job_id}: <b>{x.from}</b> → <b>{x.to}</b></div>
            ))}
          </div>
        </>
      )}

      {diff.resequenced.length > 0 && (
        <div className="note" style={{ marginTop: 8 }}>
          Порядок объезда изменился у {count(diff.resequenced.length, 'заявки', 'заявок', 'заявок')} —
          прежнее время показано на ленте бледным блоком.
        </div>
      )}

      {diff.dropped.length > 0 && (
        <>
          <div className="sub-head">Вытеснены</div>
          <div className="rejected">
            {diff.dropped.map(x => (
              <div key={x.job_id}>
                <b>{x.job_id}</b> (было у {x.from}) — <span className="no">{x.reason}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {diff.is_quiet && <div className="note">План не изменился.</div>}

      <div className="sub-head">Обязательные метрики</div>
      <table className="metrics">
        <thead><tr><th /><th className="n">было → стало</th><th className="n">разница</th></tr></thead>
        <tbody>
          {line('used_engineers', 'Исполнителей', '', 0, false)}
          {line('total_km', 'Пробег', ' км', 1, false)}
          {line('assigned', 'Назначено', '', 0, true)}
          {line('late_jobs', 'Просрочек', '', 0, false)}
        </tbody>
      </table>

      <div className="caveat">
        Бригада, которая в момент события уже едет к клиенту и иначе не успеет,
        завершает этот визит. Остальные продолжают день с адреса последней
        начатой работы, а если ещё ничего не начинали — со своей стартовой точки,
        не раньше момента события. Где именно бригада была между адресами, модель
        не знает.
      </div>

      <button style={{ marginTop: 10 }} disabled={busy} onClick={onBack}>
        Вернуться к плану дня
      </button>
    </div>
  );
}
