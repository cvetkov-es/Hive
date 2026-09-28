// Поиск по заявке, адресу и бригаде. Что и как ищется — lib/search.ts.
// Выбор найденного делает то же, что щелчок по нему на карте: правая панель
// объясняет, карта и лента показывают.

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Plan } from '../api';
import { searchPlan, type SearchHit } from '../lib/search.ts';

export function Search({ plan, onPickJob, onPickEngineer }: {
  plan: Plan | null;
  onPickJob: (jobId: string) => void;
  onPickEngineer: (engineerId: string) => void;
}) {
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => (plan ? searchPlan(plan, q) : []), [plan, q]);
  const shown = open && q.trim().length >= 2;

  // Активная строка при ↑ ↓ не уезжает за край списка.
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`#search-opt-${active}`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const pick = (h: SearchHit) => {
    if (h.kind === 'engineer') onPickEngineer(h.id);
    else onPickJob(h.id);
    setQ(''); setOpen(false); input.current?.blur();
  };

  return (
    <div className="search">
      <input
        ref={input} type="search" value={q} placeholder="Заявка, адрес, бригада"
        aria-label="Поиск по заявке, адресу или бригаде" autoComplete="off"
        role="combobox" aria-expanded={shown} aria-controls="search-list" aria-autocomplete="list"
        aria-activedescendant={shown && hits.length ? `search-opt-${active}` : undefined}
        onChange={e => { setQ(e.target.value); setActive(0); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={e => {
          if (e.key === 'Escape') { setQ(''); setOpen(false); }
          if (!hits.length) return;
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(hits.length - 1, i + 1)); }
          if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)); }
          if (e.key === 'Enter') { e.preventDefault(); pick(hits[active] ?? hits[0]); }
        }}
      />
      {shown && (
        <div className="search-list" id="search-list" role="listbox" ref={list}>
          {hits.map((h, i) => (
            <button key={`${h.kind}:${h.id}`} id={`search-opt-${i}`} role="option" aria-selected={i === active}
                    className={i === active ? 'on' : ''}
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={e => e.preventDefault()}
                    onClick={() => pick(h)}>
              <span className="hit-title"><b>{h.title}</b> <span>{h.meta}</span></span>
              {h.kind === 'job' && <span className="hit-addr">{h.address}</span>}
            </button>
          ))}
          {!hits.length && <div className="search-empty">Ничего не найдено</div>}
        </div>
      )}
    </div>
  );
}
