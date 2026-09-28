// Поведение пульта, общее для обеих раскладок: что выбрано, какое окно
// открыто, открыта ли форма события, задержка, зона, свёрнута ли лента.
//
// Раскладки A и B отличаются только тем, где стоят панели. Всё, что панели
// делают, живёт здесь, в одном месте: иначе две оболочки разошлись бы в
// поведении, и на показе экран на ноутбуке вёл бы себя не так, как на мониторе.

import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type CheckRow, type EventBody } from './api';
import { delayRisk } from './lib/delay.ts';
import { ALL } from './lib/zones.ts';
import type { Board } from './store';

export type Overlay =
  | { kind: 'none' }
  | { kind: 'audit' }
  | { kind: 'health' }
  | { kind: 'estimated' }
  | { kind: 'checks'; jobId: string; rows: CheckRow[] }
  | { kind: 'move'; jobId: string };

/** Итог ручного переназначения — для сообщения в правой панели. */
export type Moved = { planId: string; jobId: string; engineerId: string;
                      addedKm: number; position: number };

/** Щелчок по карте или ленте, пока открыта форма события: форма может
 *  забрать его себе (подставить заявку для отмены или выбывшую бригаду) и
 *  вернуть true — тогда выбор на пульте не меняется. */
export type EventPick = (p: { jobId?: string; engineerId?: string }) => boolean;

export function useController(b: Board) {
  const { s } = b;
  const [overlay, setOverlay] = useState<Overlay>({ kind: 'none' });
  // Форма события: открыта — видна в правой панели; черновик — форма
  // существует, даже спрятанная. Уход к заявке или бригаде форму прячет, но
  // не стирает: диспетчер посмотрел на карту и вернулся к недописанному.
  // Стирают её только «Закрыть», «Отменить» и применённое событие.
  const [eventOpen, setEventOpen] = useState(false);
  const [eventDraft, setEventDraft] = useState(false);
  const eventPick = useRef<EventPick | null>(null);
  const [flash, setFlash] = useState<{ planId: string; text: string } | null>(null);
  const [delay, setDelay] = useState(0);
  const [zone, setZone] = useState(ALL);
  const [timelineCollapsed, setTimelineCollapsed] = useState(false);
  // После события правая панель показывает «Что изменилось», но «Сводка
  // смены» с незакрытыми заявками должна оставаться в одном щелчке.
  const [summaryTab, setSummaryTab] = useState<'diff' | 'summary'>('diff');

  // Зоны и задержка — свои у каждого региона: оставшийся от Юго-востока
  // фильтр «Кашира» на Востоке показал бы пустую ленту.
  useEffect(() => { setZone(ALL); setDelay(0); setEventOpen(false); setEventDraft(false); }, [s.region]);
  useEffect(() => { setSummaryTab('diff'); }, [s.diff]);

  const close = () => setOverlay({ kind: 'none' });

  // Выбранное не должно прятаться фильтром зоны: бригада из другой зоны,
  // найденная поиском или в сводке, иначе гасла бы на карте и пропадала с ленты.
  const clusterOfEngineer = (id: string) =>
    s.plan?.routes.find(r => r.engineer_id === id)?.cluster
    ?? s.plan?.reserve?.find(r => r.engineer_id === id)?.cluster;
  const showZoneOf = (engineerId: string | undefined) => {
    const cl = engineerId ? clusterOfEngineer(engineerId) : undefined;
    if (cl && zone !== ALL && cl !== zone) setZone(ALL);
  };

  const pickJob = (jobId: string) => {
    if (eventOpen && eventPick.current?.({ jobId })) return;
    setEventOpen(false);
    showZoneOf(b.jobOwner[jobId]?.engineerId);
    b.select({ kind: 'job', jobId });
  };
  const pickEngineer = (id: string | null) => {
    if (eventOpen && id && eventPick.current?.({ engineerId: id })) return;
    setEventOpen(false);
    if (id) showZoneOf(id);
    b.select(id ? { kind: 'engineer', engineerId: id } : { kind: 'none' });
  };
  const clearSelection = () => {
    setEventOpen(false);
    b.select({ kind: 'none' });
  };
  const showSummary = () => { clearSelection(); setSummaryTab('summary'); };

  const openEvent = () => {
    b.select({ kind: 'none' });
    b.setScreen('board');
    setEventDraft(true);
    setEventOpen(true);
  };
  const closeEvent = () => { setEventOpen(false); setEventDraft(false); };

  const applyEvent = async (body: EventBody, replan: boolean) => {
    if (!s.plan) return 'плана нет';
    // Живой счёт показывается полосой по известному лимиту; вставка обычной
    // заявки планировщик не запускает и проходит мгновенно.
    const err = await b.runEvent(s.plan, { ...body, time_limit_s: replan ? 10 : 5 }, replan);
    if (!err) closeEvent();
    return err;
  };

  const afterMove = async (m: Moved) => {
    close();
    const plan = await api.savedPlan(m.planId);
    // Сообщение привязано к новому плану и видно сразу, не дожидаясь аудита и
    // формы дорог. Километры — по таблице расстояний, а она не метрическая:
    // вставка может и сократить маршрут, и это надо сказать, а не «вырос на −0.4».
    const km = Math.abs(m.addedKm) < 0.005 ? 'не изменился'
      : `${m.addedKm > 0 ? 'вырос' : 'сократился'} на ${Math.abs(m.addedKm).toFixed(2)} км`;
    setFlash({
      planId: plan.plan_id,
      text: `Заявка ${m.jobId} передана ${m.engineerId}: маршрут ${km}, `
        + `в маршруте она ${m.position}-я. План пересчитан.`,
    });
    await b.adoptPlan(plan);
  };

  // Прогноз задержки считается один раз и нужен и ленте, и сводке. После
  // события — от его момента: выполненное до события уже не затянется.
  const risk = useMemo(() => {
    if (!s.plan || delay <= 0) return null;
    const at = s.plan.meta?.at_min;
    return delayRisk(s.plan.routes, delay, typeof at === 'number' ? at : undefined);
  }, [s.plan, delay]);

  return {
    overlay, setOverlay, close,
    eventOpen, eventDraft, openEvent, closeEvent, applyEvent,
    registerEventPick: (fn: EventPick | null) => { eventPick.current = fn; },
    flash: flash && flash.planId === s.plan?.plan_id ? flash.text : '',
    clearFlash: () => setFlash(null),
    afterMove,
    delay, setDelay, risk,
    zone, setZone, showAll: () => setZone(ALL),
    timelineCollapsed, toggleTimeline: () => setTimelineCollapsed(v => !v),
    summaryTab, setSummaryTab,
    pickJob, pickEngineer, clearSelection, showSummary,
  };
}

export type Controller = ReturnType<typeof useController>;
