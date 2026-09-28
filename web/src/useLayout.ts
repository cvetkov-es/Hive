// Раскладка по размеру окна: A — карта во весь экран, B — лента в центре.
// Порог и его обоснование — в lib/layout.ts.

import { useEffect, useState } from 'react';
import { layoutFor, type Layout, WIDE_MIN_HEIGHT, WIDE_MIN_WIDTH } from './lib/layout.ts';

const QUERY = `(min-width: ${WIDE_MIN_WIDTH}px) and (min-height: ${WIDE_MIN_HEIGHT}px)`;

export function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(() => layoutFor(window.innerWidth, window.innerHeight));
  useEffect(() => {
    const mq = window.matchMedia(QUERY);
    const update = () => setLayout(mq.matches ? 'wide' : 'compact');
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, []);
  return layout;
}
