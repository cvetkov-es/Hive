// Шапка и её части: логотип, выбор набора данных, экраны, действия, плашки.
//
// Части отдельные, потому что две раскладки ставят их в разные места: в A
// набор данных и плашки стоят в левой панели, в B — в шапке и в полосе метрик.
//
// Даты на экране нет. Эксперты написали прямо: контрольная выборка — «один из
// реальных дней сентября», дата в выгрузке обезличена, и подписывать экран ею
// значило бы выдать условную дату за настоящую.
//
// Плашка аудита — единственное утверждение на экране, которое проверяет само
// себя: числа пересчитаны отдельной программой, не видевшей ни планировщика,
// ни его проверок. Рядом — плашки о том, чему на экране верить НЕЛЬЗЯ:
// провал самопроверки окружения и приблизительные расстояния. Обе появляются
// только когда есть о чём предупредить, и обе открывают окно с объяснением:
// всплывающая подсказка на показе экрана не видна вовсе.

import { useEffect, useRef, useState } from 'react';
import type { Controller } from '../controller';
import type { Board, Screen } from '../store';
import { count } from '../text';

export function Brand() {
  return <div className="wordmark" aria-label="Улей">УЛ<span>Е</span>Й</div>;
}

const DATE_NOTE = 'Заявки — из реального рабочего дня этого участка в сентябре. '
  + 'Дату организаторы скрыли.';

/** Выбор тестового набора — шаг 1 сценария ТЗ: «Загрузите или откройте
 *  тестовый набор данных». Поэтому подпись прямо говорит «Набор данных», а в
 *  списке у каждого набора видно, сколько в нём заявок и бригад. */
export function RegionPicker({ b, big }: { b: Board; big?: boolean }) {
  const { s } = b;
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('mousedown', away);
    window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('mousedown', away); window.removeEventListener('keydown', esc); };
  }, [open]);

  return (
    <div className={`picker ${big ? 'big' : ''}`} ref={box}>
      <button className="picker-btn" aria-haspopup="listbox" aria-expanded={open}
              disabled={!s.regions.length} onClick={() => setOpen(v => !v)} title={DATE_NOTE}>
        <span className="picker-cap">Набор данных</span>
        <span className="picker-name">{s.region || '—'} <i aria-hidden="true">▾</i></span>
      </button>
      {open && (
        <div className="picker-menu" role="listbox" aria-label="Набор данных">
          <div className="picker-note">{DATE_NOTE}</div>
          {s.regions.map(r => (
            <button key={r.name} role="option" aria-selected={r.name === s.region}
                    className={r.name === s.region ? 'on' : ''}
                    disabled={!!s.busy}
                    onClick={() => { setOpen(false); if (r.name !== s.region) b.loadPlan(r.name); }}>
              <b>{r.name}</b>
              <span>{count(r.jobs, 'заявка', 'заявки', 'заявок')} · {count(r.engineers, 'бригада', 'бригады', 'бригад')}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const SCREENS: { key: Screen; label: string }[] = [
  { key: 'board', label: 'План дня' },
  { key: 'compare', label: 'Сравнение' },
  { key: 'curve', label: 'Сколько нужно людей' },
  { key: 'sensitivity', label: 'Цена ограничений' },
];

export function ScreenTabs({ b, look }: { b: Board; look: 'seg' | 'line' }) {
  return (
    <div className={look === 'seg' ? 'seg' : 'tabs-line'} role="tablist" aria-label="Экран">
      {SCREENS.map(v => (
        <button key={v.key} role="tab" aria-selected={v.key === b.s.screen}
                className={v.key === b.s.screen ? 'on' : ''}
                onClick={() => b.setScreen(v.key)}>
          {v.label}
        </button>
      ))}
    </div>
  );
}

/** Шаг 2 сценария ТЗ — «Запустите автоматическое планирование» — это
 *  «Пересчитать»: маршруты строятся заново прямо сейчас. «Событие» — шаг 5. */
export function Actions({ b, c, short }: { b: Board; c: Controller; short?: boolean }) {
  const { s } = b;
  return (
    <div className="actions">
      {s.busy && !s.busyLimit && <span className="busy-note">{s.busy}…</span>}
      {/* Один вариант, 20 с: по замеру кривой 10 с, 20 с и минута почти везде
          дают один и тот же план — выбор времени ничего бы не показал. */}
      <button disabled={!s.plan || !!s.busy}
              onClick={() => b.loadPlan(s.region, { recompute: true, keep: s.keep })}
              title="Построить маршруты заново прямо сейчас (20 секунд) и сравнить с сохранённым планом">
        Пересчитать
      </button>
      {s.plan
        ? <a className="btn" href={`/api/export/${s.plan.plan_id}`} download
             title="План в CSV: кто, куда, во сколько">{short ? 'Выгрузить' : 'Выгрузить план'}</a>
        : <button disabled>{short ? 'Выгрузить' : 'Выгрузить план'}</button>}
      <button className="primary" disabled={!s.plan || !!s.busy} onClick={c.openEvent}>
        Событие
      </button>
    </div>
  );
}

/** `short` — раскладка B: коротко «Аудит 12/12», а плашка источника плана —
 *  только после «Пересчитать» или урезания парка («живой расчёт»), то есть
 *  когда числа могут разойтись с сохранённым планом. После события и ручной
 *  передачи план тоже новый, но об этом уже говорит «Что изменилось», а
 *  полоса метрик на ноутбуке не вмещает лишних плашек. */
export function StatusChips({ b, c, short }: { b: Board; c: Controller; short?: boolean }) {
  const { s } = b;
  const audit = s.audit;
  const health = s.health;
  // Участки до точек, которых не было в офлайновой таблице расстояний,
  // посчитаны по прямой с коэффициентом извилистости. Такое расстояние —
  // оценка, и выдавать её за измерение нельзя.
  const estimated = (s.plan?.routes ?? [])
    .reduce((n, r) => n + r.stops.filter(st => st.estimated).length, 0);
  const saved = s.plan?.source === 'эталон';
  const showSource = !!s.plan && (short ? s.plan.source === 'живой расчёт' : true);

  return (
    <div className="chips">
      {showSource && (
        <span className={`chip-st ${saved ? '' : 'warn'}`}
              title={saved
                ? 'План посчитан заранее, 10 минут на участок, и сохранён: у всех одинаковый. «Пересчитать» строит маршруты заново прямо сейчас.'
                : 'План построен только что, на этом сервере: числа могут немного отличаться от сохранённого плана.'}>
          {saved ? 'План посчитан заранее' : 'Посчитано сейчас'}
        </span>
      )}
      {estimated > 0 && (
        <button className="chip-st warn" onClick={() => c.setOverlay({ kind: 'estimated' })}
                title={`${count(estimated, 'адрес', 'адреса', 'адресов')} вне таблицы расстояний: почему расстояния до них приблизительные`}>
          {short ? `≈ расстояния: ${estimated}` : `${count(estimated, 'адрес', 'адреса', 'адресов')} вне таблицы расстояний`}
        </button>
      )}
      {health && !health.ok && (
        <button className="chip-st bad" onClick={() => c.setOverlay({ kind: 'health' })}>
          {health.headline}
        </button>
      )}
      {audit && (
        <button className={`chip-st ${audit.ok ? 'ok' : 'bad'}`}
                onClick={() => c.setOverlay({ kind: 'audit' })}
                title="План заново проверил отдельный скрипт. Нажмите — что именно проверено">
          {short ? `Проверено ${audit.passed}/${audit.total}`
            : audit.ok ? `План проверен: ${audit.passed} из ${audit.total} правил`
            : `Проверка: нарушено ${audit.total - audit.passed} из ${audit.total} правил`}
        </button>
      )}
    </div>
  );
}
