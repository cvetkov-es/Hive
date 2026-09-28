// Фильтр по зонам обслуживания. Когда зона одна, его нет вовсе — lib/zones.ts.

import { zoneOptions } from '../lib/zones.ts';

export function ZoneFilter({ clusters, zone, onZone }: {
  clusters: Record<string, number> | undefined;
  zone: string;
  onZone: (zone: string) => void;
}) {
  const opts = zoneOptions(clusters ?? {});
  if (!opts.length) return null;
  return (
    <div className="seg seg-sm" role="radiogroup" aria-label="Зона обслуживания">
      {opts.map(o => (
        <button key={o.key} role="radio" aria-checked={zone === o.key}
                className={zone === o.key ? 'on' : ''} title={o.title}
                onClick={() => onZone(o.key)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
