// Пульт целиком. Две раскладки одного и того же пульта:
//   A — карта во весь экран, панели плавают поверх (экран от 1440×820);
//   B — карта сверху, бригады слиты с лентой смены (ноутбук и меньше).
// Панели и поведение у них общие (controller.ts), различается только то, где
// что стоит. Окна поверх пульта — одни на обе раскладки.

import { useController } from './controller';
import { ErrorBoundary } from './panels/ErrorBoundary';
import { CompactShell } from './shells/CompactShell';
import { Overlays } from './shells/common';
import { WideShell } from './shells/WideShell';
import { useBoard } from './store';
import { useLayout } from './useLayout';

export function App() {
  const b = useBoard();
  const c = useController(b);
  const layout = useLayout();
  return (
    <>
      {layout === 'wide' ? <WideShell b={b} c={c} /> : <CompactShell b={b} c={c} />}
      <ErrorBoundary label="Окно" float resetKey={c.overlay.kind} onReset={c.close}>
        <Overlays b={b} c={c} />
      </ErrorBoundary>
    </>
  );
}
