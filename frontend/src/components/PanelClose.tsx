import { createContext, useContext } from 'react';
import type { PanelKey } from './WindowLayout';
export const PanelToggle = createContext<(k: PanelKey) => void>(() => undefined);
export function PanelClose({ panel }: { panel: PanelKey }) {
  const toggle = useContext(PanelToggle);
  return (
    <button
      className="icon-btn panel-close"
      aria-label={`Скрыть панель ${panel}`}
      title="Вернуть через меню «Окно»"
      onClick={() => toggle(panel)}
    >
      ×
    </button>
  );
}
