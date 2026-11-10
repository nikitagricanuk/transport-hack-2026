import { useEffect, useRef, type TableHTMLAttributes } from 'react';
/** Keep native table markup and semantics; add persistent column handles. */
export default function ResizableTable({ id, children, ...props }: TableHTMLAttributes<HTMLTableElement>) {
  const ref = useRef<HTMLTableElement>(null);
  useEffect(() => {
    const table = ref.current;
    if (!table) return;
    const key = `uds-table-${id}`;
    let sizes: number[] = [];
    try {
      sizes = JSON.parse(localStorage.getItem(key) ?? '[]');
    } catch {
      /* ignored */
    }
    const cleanup: (() => void)[] = [];
    table.querySelectorAll<HTMLElement>('thead tr:first-child th').forEach((th, i) => {
      if (sizes[i]) th.style.width = `${sizes[i]}px`;
      const handle = document.createElement('span');
      handle.className = 'column-resize';
      handle.role = 'separator';
      handle.setAttribute('aria-label', `Ширина ${th.textContent}`);
      handle.tabIndex = 0;
      const save = (n: number) => {
        const width = Math.max(48, Math.round(n));
        th.style.width = `${width}px`;
        th.style.minWidth = `${width}px`;
        sizes[i] = width;
        try {
          localStorage.setItem(key, JSON.stringify(sizes));
        } catch {
          /* blocked */
        }
      };
      const down = (e: PointerEvent) => {
        e.preventDefault();
        const start = e.clientX,
          width = th.getBoundingClientRect().width;
        handle.setPointerCapture?.(e.pointerId);
        const move = (ev: PointerEvent) => save(width + ev.clientX - start);
        const up = () => {
          handle.removeEventListener('pointermove', move);
          handle.removeEventListener('pointerup', up);
        };
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', up);
        cleanup.push(up);
      };
      const auto = () => {
        th.style.width = '';
        th.style.minWidth = '';
        sizes[i] = 0;
        try {
          localStorage.setItem(key, JSON.stringify(sizes));
        } catch {
          /* blocked */
        }
      };
      const keys = (e: KeyboardEvent) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
          e.preventDefault();
          save(th.getBoundingClientRect().width + (e.key === 'ArrowRight' ? 10 : -10));
        }
      };
      handle.addEventListener('pointerdown', down);
      handle.addEventListener('dblclick', auto);
      handle.addEventListener('keydown', keys);
      th.append(handle);
      cleanup.push(() => handle.remove());
    });
    return () => cleanup.forEach((fn) => fn());
  }, [id, children]);
  return (
    <table {...props} id={id} ref={ref}>
      {children}
    </table>
  );
}
