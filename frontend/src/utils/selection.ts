import type { MultiSelection, Selection } from '../types';
export function toggleSelection(prev: MultiSelection, item: NonNullable<Selection>): MultiSelection {
  return prev.some((p) => p.kind === item.kind && p.id === item.id)
    ? prev.filter((p) => p.kind !== item.kind || p.id !== item.id)
    : [...prev, item];
}
export function boxSelection(items: MultiSelection): MultiSelection {
  return [...new Map(items.map((p) => [`${p.kind}/${p.id}`, p])).values()];
}
