import type { Tags } from '../types';
export const NODE_PRESETS = [
  { label: 'Светофор', tags: { highway: 'traffic_signals' } },
  { label: 'Переход', tags: { highway: 'crossing', crossing: 'marked' } },
  { label: 'Переход со светофором', tags: { highway: 'crossing', crossing: 'traffic_signals' } },
  { label: 'Остановка', tags: { highway: 'bus_stop', public_transport: 'platform', bus: 'yes' } },
  { label: 'Уступи дорогу', tags: { highway: 'give_way' } },
  { label: 'Стоп', tags: { highway: 'stop' } },
  { label: 'Обычный узел', tags: {} },
] as const;
export function presetPatch(current: Tags, preset: { tags: object }): Record<string, string | null> {
  return {
    highway: null,
    crossing: null,
    public_transport: null,
    bus: null,
    trolleybus: null,
    tram: null,
    ...(current.railway === 'tram_stop' ? { railway: null } : {}),
    ...preset.tags,
  };
}
export function isPreset(current: Tags, preset: { tags: object }) {
  if ('public_transport' in preset.tags)
    return (
      current.highway === 'bus_stop' ||
      ['platform', 'stop_position'].includes(current.public_transport) ||
      current.railway === 'tram_stop'
    );
  return Object.entries(presetPatch(current, preset)).every(([k, v]) => (current[k] ?? null) === v);
}
