// Карта: точки заявок, маршруты по геометрии дорог, стартовые точки бригад.
//
// Маршруты рисуются по форме дорог из офлайнового кэша, а не прямыми между
// адресами: бригада не летит через дома, и это видно сразу. Участок,
// формы которого в кэше нет (появился после перепланирования), рисуется
// прямой и пунктиром — так честнее, чем выдать прямую за дорогу.
//
// Цвет точки — бригада, но опознание держится не на цвете. Пятнадцати
// различимых оттенков не существует: на карте видны все пары сразу, и по всем
// парам проходят три слота палитры, не пятнадцать (проверено валидатором).
// Поэтому у бригады есть ещё и форма метки, а выбор гасит всех остальных —
// один маршрут на экране читается всегда.
//
// На общем виде региона точки мельче, а линии толще и с белой подложкой:
// иначе московская группа из полусотни точек закрывает маршруты целиком, и
// карта выглядит набором точек без единого пути.
//
// Тайлы — единственное, за чем интерфейс ходит в сеть. Их отсутствие не должно
// ломать демонстрацию: маршруты и точки лежат локально и рисуются на пустом
// поле, а в легенде появляется предупреждение.
//
// Карта знает, какая её часть свободна. В раскладке A она лежит под всем
// экраном, а поверх плавают панели: подгонка масштаба, кнопки и легенда
// ставятся в свободную область между ними, иначе маршруты оказывались бы под
// списком бригад. В раскладке B свободна вся карта.

import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Depot, Geometry, Legs, Plan } from '../api';
import { STATE, type Mark } from '../colors';
import { ALL } from '../lib/zones.ts';
import { count, plural } from '../text';

/** Свободная от панелей часть карты, в пикселях от краёв. */
export type Inset = { top: number; right: number; bottom: number; left: number };

// Подложка — стандартный OSM: единственный источник тайлов без ключа. CARTO и
// Stadia без него печатают «API KEY REQUIRED» поперёк карты, а ключ в открытом
// репозитории хакатона хранить негде. Стандартный OSM пёстрый, поэтому он
// обесцвечивается и осветляется фильтром: подложка обязана оставаться фоном,
// иначе она спорит с маршрутами за внимание.
const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

// Отступ подгонки внутри свободной области: метка с кольцом занимает 15 px, и
// точка на самом краю иначе режется пополам. Справа больше — там кнопки
// масштаба, снизу — легенда: её высота меряется, иначе южные точки региона
// оказались бы под ней.
const EDGE = 26;
const pads = (i: Inset, legend: number) => ({
  paddingTopLeft: [i.left + EDGE, i.top + EDGE] as [number, number],
  paddingBottomRight: [i.right + EDGE + 44, i.bottom + EDGE + legend] as [number, number],
});

function markerSize(zoom: number): number {
  return zoom >= 12 ? 11 : zoom >= 10 ? 9 : 7;
}

function markerHtml(mark: Mark, size: number, dimmed: boolean, emergency: boolean,
                    selected: boolean) {
  const ring = selected ? STATE.emergency : emergency ? STATE.emergency : '#ffffff';
  const ringW = selected ? 3 : size >= 9 ? 2 : 1.5;
  const shape =
    mark.shape === 'sq' ? 'border-radius:1px'
    : mark.shape === 'di' ? 'border-radius:1px;transform:rotate(45deg)'
    : mark.shape === 'tr' ? 'clip-path:polygon(50% 0,100% 100%,0 100%)'
    : 'border-radius:50%';
  return `<span style="display:block;width:${size}px;height:${size}px;
    background:${mark.color};box-shadow:0 0 0 ${ringW}px ${ring},0 0 0 ${ringW + 1}px rgba(22,32,42,.35);
    opacity:${dimmed ? 0.3 : 1};${shape}"></span>`;
}

/** Стартовая точка бригады из резерва: база её зоны, в Москве — офис. */
export function depotOf(plan: Plan, cluster: string): Depot | undefined {
  const same = plan.depots.filter(d => d.cluster === cluster);
  return same.find(d => d.is_office) ?? same[0];
}

/** Зоны обслуживания на общем виде. На Юго-востоке Москва и Кашира в 90 км
 *  друг от друга, и на масштабе всего региона город — комок в двадцать
 *  пикселей: ни маршрута, ни точки в нём не разобрать и не попасть мышью.
 *  Подпись зоны говорит, что там, и по щелчку приближает её. */
type Zone = { name: string; jobs: number; brigades: number; bounds: L.LatLngBounds };

function zonesOf(plan: Plan): Zone[] {
  const acc: Record<string, { jobs: number; brigades: number; pts: [number, number][] }> = {};
  const zone = (name: string) => (acc[name] ??= { jobs: 0, brigades: 0, pts: [] });
  for (const r of plan.routes) {
    if (!r.stops.length) continue;
    const z = zone(r.cluster);
    z.brigades += 1;
    for (const s of r.stops) { z.jobs += 1; z.pts.push([s.lat, s.lon]); }
  }
  for (const u of plan.unassigned) {
    if (!u.lat) continue;
    const d = [...plan.depots].sort((a, b) =>
      Math.hypot(a.lat - u.lat, a.lon - u.lon) - Math.hypot(b.lat - u.lat, b.lon - u.lon))[0];
    if (!d) continue;
    const z = zone(d.cluster);
    z.jobs += 1; z.pts.push([u.lat, u.lon]);
  }
  return Object.entries(acc).filter(([, z]) => z.pts.length)
    .map(([name, z]) => ({ name, jobs: z.jobs, brigades: z.brigades, bounds: L.latLngBounds(z.pts) }));
}

function regionBounds(plan: Plan): L.LatLngBounds | null {
  const pts: [number, number][] = [
    ...plan.routes.flatMap(r => r.stops.map(s => [s.lat, s.lon] as [number, number])),
    ...plan.depots.map(d => [d.lat, d.lon] as [number, number]),
    ...plan.unassigned.filter(u => u.lat).map(u => [u.lat, u.lon] as [number, number]),
  ];
  return pts.length ? L.latLngBounds(pts) : null;
}

export function MapView({ plan, previous, marks, geometry, previousGeometry,
                          selectedEngineer, selectedJob, highlightJobs, fitSeq,
                          inset, zone, compactLegend, onShowAll, onPickJob, onPickEngineer }: {
  plan: Plan | null;
  previous: Plan | null;
  marks: Record<string, Mark>;
  geometry: Geometry | null;
  previousGeometry: Geometry | null;
  selectedEngineer: string | null;
  selectedJob: string | null;
  highlightJobs: string[];
  fitSeq: number;
  inset: Inset;
  zone: string;
  /** «Все» на карте — весь регион, в том числе без фильтра зоны. */
  onShowAll?: () => void;
  /** Маленькая карта (ноутбук): легенда за кнопкой, иначе она закрывает
   *  четверть карты. */
  compactLegend?: boolean;
  onPickJob: (jobId: string) => void;
  onPickEngineer: (engineerId: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const insetNow = useRef(inset);
  insetNow.current = inset;
  const legendBox = useRef<HTMLDivElement>(null);
  const [legendOpen, setLegendOpen] = useState(!compactLegend);
  const [legendH, setLegendH] = useState(0);
  const legendNow = useRef(0);
  legendNow.current = legendH;
  const padNow = () => pads(insetNow.current, legendNow.current);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const [tilesOk, setTilesOk] = useState(true);
  const [zoom, setZoom] = useState(10);
  const bounds = useRef<L.LatLngBounds | null>(null);
  const fittedSeq = useRef(-1);
  const moved = useRef(false);        // диспетчер сам двигал карту — не мешаем
  const ours = useRef(false);         // наше собственное движение, не его
  const pick = useRef({ onPickJob, onPickEngineer });
  pick.current = { onPickJob, onPickEngineer };

  // Подогнать вид под регион. Без анимации — и это не вкус, а условие
  // корректности. Leaflet не различает, кто подвинул карту, поэтому наше
  // движение помечается флагом. С анимацией событие zoomstart приходит в
  // следующем кадре, ПОСЛЕ сброса флага, и собственная подгонка засчитывается
  // как движение диспетчера: дальше карта перестаёт подгоняться и застревает
  // на масштабе всей области (Восток — от Калуги до Рязани).
  // Без анимации все события приходят синхронно, пока флаг поднят.
  const fitTo = (m: L.Map, b: L.LatLngBounds) => {
    ours.current = true;
    try {
      m.fitBounds(b, { ...padNow(), animate: false, maxZoom: 15 });
    } finally {
      ours.current = false;
    }
  };

  const fitRegion = useRef(() => {});
  fitRegion.current = () => {
    const m = map.current;
    if (!m || !bounds.current) return;
    moved.current = false;
    fitTo(m, bounds.current);
  };

  useEffect(() => {
    if (!host.current || map.current) return;
    // Дробный масштаб: с целыми уровнями регион, который чуть не влезает в
    // рамку на уровне 9, показывается на уровне 8 — вдвое мельче, от Калуги до
    // Рязани. Так бывает после события: легенда и заголовок ленты становятся
    // выше, и карта теряет два десятка пикселей.
    // Кнопки масштаба и подпись источника тайлов — свои, в свободной области
    // (см. разметку ниже): стандартные Leaflet в раскладке A оказались бы под
    // панелями.
    const m = L.map(host.current, {
      zoomControl: false, attributionControl: false, preferCanvas: true,
      zoomSnap: 0.25, zoomDelta: 1, wheelPxPerZoomLevel: 90,
    }).setView([55.7, 37.6], 10);
    const tiles = L.tileLayer(TILES, { maxZoom: 18, className: 'base-tiles' });
    let failures = 0;
    tiles.on('tileerror', () => { if (++failures > 3) setTilesOk(false); });
    tiles.on('tileload', () => setTilesOk(true));
    tiles.addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;

    m.on('dragstart zoomstart', () => { if (!ours.current) moved.current = true; });
    m.on('zoomend', () => setZoom(m.getZoom()));

    // Контейнер карты получает окончательную высоту не сразу: лента смены
    // раздвигает разметку уже после первой подгонки. Leaflet при изменении
    // размера держит центр и масштаб, поэтому подгонять нужно заново — но
    // только пока диспетчер не подвинул карту сам.
    const ro = new ResizeObserver(() => {
      m.invalidateSize();
      if (bounds.current && !moved.current) fitTo(m, bounds.current);
    });
    ro.observe(host.current);

    return () => { ro.disconnect(); m.remove(); map.current = null; };
  }, []);

  // Новый регион, урезанный парк, пересчёт, событие — показать весь регион.
  // Ручной перенос сюда не относится: диспетчер смотрит на конкретное место.
  useEffect(() => {
    const m = map.current;
    if (!m || !plan) return;
    bounds.current = regionBounds(plan);
    if (fittedSeq.current !== fitSeq && bounds.current) {
      fittedSeq.current = fitSeq;
      moved.current = false;
      fitTo(m, bounds.current);
    }
  }, [plan, fitSeq]);

  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g || !plan) return;
    g.clearLayers();

    const clusterOf: Record<string, string> = {};
    for (const r of plan.routes) clusterOf[r.engineer_id] = r.cluster;
    // Гаснут чужие для выбранной бригады и для выбранной зоны.
    const dimmed = (id: string) => (!!selectedEngineer && selectedEngineer !== id)
      || (zone !== ALL && clusterOf[id] !== undefined && clusterOf[id] !== zone);
    const size = markerSize(zoom);
    const overview = zoom <= 11;

    // Стартовые точки — шестиугольник: офис или база, откуда бригада выезжает.
    // Под заявками, а не над ними: иначе на общем виде щелчок по заявке у
    // базы попадал бы в базу.
    const hex = overview ? 10 : 14;
    for (const d of plan.depots) {
      L.marker([d.lat, d.lon], {
        icon: L.divIcon({
          className: '', iconSize: [hex, hex], iconAnchor: [hex / 2, hex / 2],
          html: `<span style="display:block;width:${hex}px;height:${hex}px;background:#16202a;
            clip-path:polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%);"></span>`,
        }),
        zIndexOffset: -1000,
      }).bindTooltip(`${d.name}: отсюда выезжают бригады зоны «${d.cluster}»`,
                     { direction: 'top' }).addTo(g);
    }

    // Подписи зон — только на общем виде и только если зон несколько. На
    // маленькой карте ноутбука соседние подписи налезают друг на друга
    // («Москва» прячется под «Домодедово»), поэтому подпись, которой нет
    // места, не ставится: остаётся зона с большим числом заявок.
    const zones = zonesOf(plan).sort((a, b) => b.jobs - a.jobs);
    const placed: [number, number, number, number][] = [];
    const LABEL_W = 220, LABEL_H = 44;
    if (zoom <= 10 && zones.length > 1) {
      for (const z of zones) {
        const c = z.bounds.getCenter();
        const pt = m.latLngToContainerPoint([c.lat, z.bounds.getEast()]);
        const box: [number, number, number, number] = [pt.x + 12, pt.y - 11, pt.x + 12 + LABEL_W, pt.y - 11 + LABEL_H];
        if (placed.some(o => box[0] < o[2] && o[0] < box[2] && box[1] < o[3] && o[1] < box[3])) continue;
        placed.push(box);
        L.marker([c.lat, z.bounds.getEast()], {
          icon: L.divIcon({
            className: 'map-zone', iconSize: undefined, iconAnchor: [-12, 11],
            html: `<button type="button">${z.name}<span>${count(z.jobs, 'заявка', 'заявки', 'заявок')} · `
              + `${count(z.brigades, 'бригада', 'бригады', 'бригад')} — приблизить</span></button>`,
          }),
          zIndexOffset: 2000,
        })
          .on('click', () => {
            const mm = map.current;
            if (!mm) return;
            moved.current = true;
            ours.current = true;
            try {
              mm.fitBounds(z.bounds, { ...padNow(), animate: false, maxZoom: 15 });
            } finally { ours.current = false; }
          })
          .addTo(g);
      }
    }

    for (const r of plan.routes) {
      if (!r.stops.length) continue;
      const mark = marks[r.engineer_id] ?? { color: '#566673', shape: 'ci' as const };
      const off = dimmed(r.engineer_id);
      const on = selectedEngineer === r.engineer_id;
      const legs: Legs = geometry?.[r.engineer_id] ?? [];
      const paths = legs.length
        ? legs.filter(l => l.path.length > 1).map(l => ({ path: l.path, estimated: l.estimated }))
        // Геометрия ещё не пришла — соединяем точки прямыми, чтобы карта не
        // была пустой, и помечаем это пунктиром.
        : [{ path: r.stops.map(s => [s.lat, s.lon] as [number, number]), estimated: true }];
      const weight = off ? 1.5 : on ? 4.5 : overview ? 3 : 3.5;

      for (const p of paths) {
        // Подложка — только под линией по дорогам: под пунктиром оценочного
        // участка белые разрывы превращали бы его в двойной «железнодорожный» штрих.
        if (!off && !p.estimated) {
          // Белая подложка отделяет линию от пёстрой подложки и от соседних
          // маршрутов: без неё трёхпиксельная линия тонет в улицах.
          L.polyline(p.path, {
            color: '#ffffff', weight: weight + 3, opacity: 0.85, interactive: false,
          }).addTo(g);
        }
        L.polyline(p.path, {
          color: mark.color, weight, opacity: off ? 0.2 : 0.95,
          dashArray: p.estimated ? '5 5' : undefined,
        })
          .bindTooltip(`${r.engineer_id} · ${r.transport}: ${count(r.jobs, 'заявка', 'заявки', 'заявок')}, `
                       + `${r.km.toFixed(1)} км — щёлкните, чтобы увидеть объяснение маршрута`,
                       { sticky: true, opacity: 0.96 })
          .on('click', () => pick.current.onPickEngineer(r.engineer_id))
          .addTo(g);
      }

      for (const s of r.stops) {
        const isSel = selectedJob === s.job_id;
        const sz = isSel ? size + 4 : size;
        L.marker([s.lat, s.lon], {
          icon: L.divIcon({
            className: '', iconSize: [sz, sz], iconAnchor: [sz / 2, sz / 2],
            html: markerHtml(mark, sz, off && !isSel, s.skill === 'Аварийные работы', isSel),
          }),
          zIndexOffset: isSel ? 1000 : off ? -100 : 0,
          riseOnHover: true,
        })
          .bindTooltip(
            `<b>${s.job_id}</b> · ${r.engineer_id}<br>${s.address}<br>` +
            `окно ${s.window}, приезд ${s.arrive}`,
            { direction: 'top', opacity: 0.96 })
          .on('click', () => pick.current.onPickJob(s.job_id))
          .addTo(g);
      }
    }

    // Призрак прежнего плана: серый пунктир тех участков, которых после
    // события больше нет. Форма берётся из геометрии ПРЕЖНЕГО плана: по
    // текущей серая линия легла бы ровно под новую цветную, и разницы на
    // карте не было бы видно. Неизменные участки не рисуются вовсе, а
    // исчезнувшие — поверх маршрутов, иначе в плотной Москве их закрывали бы
    // белые подложки соседних линий.
    if (previous) {
      const kept = new Set<string>();
      for (const r of plan.routes) {
        r.stops.forEach((s, i) => kept.add(`${r.engineer_id}|${i ? r.stops[i - 1].job_id : '·'}|${s.job_id}`));
      }
      for (const r of previous.routes) {
        const legs = previousGeometry?.[r.engineer_id];
        r.stops.forEach((s, i) => {
          if (kept.has(`${r.engineer_id}|${i ? r.stops[i - 1].job_id : '·'}|${s.job_id}`)) return;
          const leg = legs?.find(l => l.job_id === s.job_id);
          const path: [number, number][] = leg?.path?.length && leg.path.length > 1 ? leg.path
            : i ? [[r.stops[i - 1].lat, r.stops[i - 1].lon], [s.lat, s.lon]] : [];
          if (path.length < 2) return;
          L.polyline(path, {
            color: '#3d4852', weight: 3.5, opacity: dimmed(r.engineer_id) ? 0.25 : 0.85,
            dashArray: '1 6', lineCap: 'round', interactive: false,
          }).addTo(g);
        });
      }
    }

    // Неназначенные: пунктирное красное кольцо. Их не видно среди цветных
    // точек, если не выделить отдельно, а именно они — работа диспетчера.
    for (const u of plan.unassigned) {
      if (!u.lat) continue;
      const ring = size + 2;
      L.marker([u.lat, u.lon], {
        icon: L.divIcon({
          className: '', iconSize: [ring, ring], iconAnchor: [ring / 2, ring / 2],
          html: `<span style="display:block;width:${ring}px;height:${ring}px;border-radius:50%;
            border:2px dashed ${STATE.unassigned};background:rgba(255,255,255,.85)"></span>`,
        }),
        zIndexOffset: 500,
      })
        .bindTooltip(`<b>${u.job_id}</b> не назначена<br>${u.reason}`,
                     { direction: 'top' })
        .on('click', () => pick.current.onPickJob(u.job_id))
        .addTo(g);
    }

    // Новая заявка события: кольцо и подпись, которые видно на общем виде.
    // Иначе после «Перепланировать» глазу не за что зацепиться: одна точка
    // среди сотни.
    for (const id of highlightJobs) {
      const st = plan.routes.flatMap(r => r.stops).find(x => x.job_id === id);
      const un = plan.unassigned.find(x => x.job_id === id);
      const lat = st?.lat ?? un?.lat, lon = st?.lon ?? un?.lon;
      if (!lat || !lon) continue;
      L.marker([lat, lon], {
        icon: L.divIcon({
          className: '', iconSize: [34, 34], iconAnchor: [17, 17],
          html: '<span class="map-new-ring"></span>',
        }),
        zIndexOffset: 1500, interactive: true,
      })
        .bindTooltip(`новая заявка ${id}${un ? ' — не принята' : ''}`,
                     { permanent: true, direction: 'left', offset: [-16, 0],
                       className: 'map-new-label' })
        .on('click', () => pick.current.onPickJob(id))
        .addTo(g);
    }
  }, [plan, previous, marks, geometry, previousGeometry, selectedEngineer, selectedJob,
      highlightJobs, zoom, zone]);

  // Выбор бригады — приблизить её маршрут: на Юго-востоке Кашира в 90 км от
  // Москвы, и без этого выбранная подмосковная бригада остаётся точкой в углу.
  // Бригада из резерва маршрута не имеет — показываем её стартовую точку.
  useEffect(() => {
    const m = map.current;
    if (!m || !plan || !selectedEngineer) return;
    const r = plan.routes.find(x => x.engineer_id === selectedEngineer);
    let b: L.LatLngBounds | null = null;
    if (r?.stops.length) {
      // Рамка — весь путь бригады, а не только её адреса: дорога от базы к
      // первой заявке может уходить в сторону (из Каширы в Ступино — через
      // юг), и без неё линия пряталась бы под лентой смены.
      const pts: [number, number][] = r.stops.map(s => [s.lat, s.lon]);
      for (const leg of geometry?.[r.engineer_id] ?? []) pts.push(...leg.path);
      b = L.latLngBounds(pts);
    } else {
      const res = plan.reserve?.find(x => x.engineer_id === selectedEngineer);
      const d = res ? depotOf(plan, res.cluster) : undefined;
      if (d) b = L.latLngBounds([[d.lat - 0.025, d.lon - 0.04], [d.lat + 0.025, d.lon + 0.04]]);
    }
    if (!b) return;
    moved.current = true;   // вид теперь про выбранную бригаду, сами его не сбрасываем
    ours.current = true;
    m.flyToBounds(b.pad(0.08), { ...padNow(), duration: 0.4, maxZoom: 15 });
    window.setTimeout(() => { ours.current = false; }, 600);
  }, [selectedEngineer]);

  // Неназначенная заявка маршрута не имеет — показываем её точку. Иначе
  // щелчок по карточке в «Не назначено» ничего не менял бы на карте.
  useEffect(() => {
    const m = map.current;
    if (!m || !plan || !selectedJob) return;
    const u = plan.unassigned.find(x => x.job_id === selectedJob);
    if (!u?.lat) return;
    moved.current = true;
    ours.current = true;
    const box = L.latLngBounds([[u.lat - 0.012, u.lon - 0.02], [u.lat + 0.012, u.lon + 0.02]]);
    m.flyToBounds(box, { ...padNow(), duration: 0.4, maxZoom: Math.max(m.getZoom(), 13) });
    window.setTimeout(() => { ours.current = false; }, 600);
  }, [selectedJob]);

  // Выбор зоны — показать зону целиком; «Все» — снова весь регион.
  const zoneSeen = useRef(zone);
  useEffect(() => {
    const m = map.current;
    if (!m || !plan || zoneSeen.current === zone) return;
    zoneSeen.current = zone;
    if (zone === ALL) { fitRegion.current(); return; }
    const z = zonesOf(plan).find(x => x.name === zone);
    if (!z) return;
    moved.current = true;
    fitTo(m, z.bounds);
  }, [zone, plan]);

  // Высота легенды меняется: после события в ней появляются строки про
  // прежний план и новую заявку.
  useEffect(() => {
    const el = legendBox.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setLegendH(Math.round(el.getBoundingClientRect().height)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Свободная область поменялась (свернули ленту, изменилось окно, выросла
  // легенда) — карта заново показывает регион, если диспетчер её не двигал сам.
  const insetKey = `${inset.top}|${inset.right}|${inset.bottom}|${inset.left}|${legendH}`;
  useEffect(() => {
    const m = map.current;
    if (m && bounds.current && !moved.current) fitTo(m, bounds.current);
  }, [insetKey]);

  const estimated = geometry
    ? Object.values(geometry).flat().filter(l => l.estimated).length : 0;

  return (
    <div className="map-host">
      <div className="map" ref={host} />
      <div className="map-ctl" style={{ top: inset.top, right: inset.right }}>
        <button aria-label="Приблизить" title="Приблизить" onClick={() => map.current?.zoomIn()}>+</button>
        <button aria-label="Отдалить" title="Отдалить" onClick={() => map.current?.zoomOut()}>−</button>
        <button className="all" title="Показать все маршруты и заявки региона"
                onClick={() => { onShowAll?.(); fitRegion.current(); }}>Все</button>
      </div>
      <div className="map-legend" ref={legendBox} style={{ left: inset.left, bottom: inset.bottom }}>
        {compactLegend && (
          <button className="linkish legend-toggle" aria-expanded={legendOpen}
                  onClick={() => setLegendOpen(v => !v)}>
            Обозначения {legendOpen ? '▴' : '▾'}
          </button>
        )}
        {legendOpen && (<>
        <span className="k"><i className="swatch ci" style={{ background: '#141c24' }} />заявка · цвет и форма — бригада</span>
        <span className="k"><i className="swatch ci" style={{ background: 'transparent', border: `2px dashed ${STATE.unassigned}`, boxShadow: 'none' }} />не назначена</span>
        <span className="k"><i className="swatch ci" style={{ background: '#fff', boxShadow: `0 0 0 2px ${STATE.emergency}` }} />авария</span>
        <span className="k"><i className="swatch hex" />офис или база бригад</span>
        {previous && (
          <span className="k"><i className="legend-ghost" />участки, которых после события нет</span>
        )}
        {highlightJobs.length > 0 && (
          <span className="k"><i className="swatch ci" style={{ background: 'transparent', boxShadow: `0 0 0 2px ${STATE.emergency}` }} />новая заявка события</span>
        )}
        {estimated > 0 && (
          <span className="k warn">
            пунктир — {count(estimated, 'участок', 'участка', 'участков')} без формы
            {' '}дороги, {plural(estimated, 'показан', 'показаны', 'показаны')} прямой
          </span>
        )}
        </>)}
        {!tilesOk && (
          <span className="k warn">подложка недоступна — маршруты и точки показаны без карты</span>
        )}
        <span className="k attr">
          <a href="https://leafletjs.com" target="_blank" rel="noreferrer">Leaflet</a>
          {' · © '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">участники OpenStreetMap</a>
        </span>
      </div>
    </div>
  );
}
