import type { PtResult, PtRoute } from './components/PtTools';
import type { AnalysisResult } from './utils/networkAnalysis';
import type {
  ChangesResult, Comparison, DocState, FixPreview, Geo, GeoPatch, LoadState, Op, OrganizerResult, ReportData, RouteDetail,
  SignalPlan, StopInfo, SumoResult, OdMatrix, Task, TaskCriterion, TaskResult,
  ValidationResult,
} from './types';

/** One id per browser tab: the server does not echo this tab's own edits back over the socket. */
export const CLIENT_ID = (() => {
  try {
    const saved = sessionStorage.getItem('uds-client-id');
    if (saved) return saved;
    const id = Math.random().toString(36).slice(2, 10);
    sessionStorage.setItem('uds-client-id', id);
    return id;
  } catch {
    return Math.random().toString(36).slice(2, 10);
  }
})();

/** Author name sent with edits, for the change history of a shared document. */
export const USER_NAME: { current: string } = { current: '' };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('X-Client-Id', CLIENT_ID);
  if (USER_NAME.current) headers.set('X-User-Name', encodeURIComponent(USER_NAME.current));
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** Edits answer with a patch of the touched map features instead of the whole map. */
export type OpResponse = Omit<DocState, 'geo'> & {
  geo?: Geo;
  patch?: GeoPatch | null;
  result?: { id?: number; new_id?: number; warnings?: string[] };
  results?: { id?: number; warnings?: string[] }[];
};

/**
 * Download a file through fetch and save it only when the server answered with it.
 * A plain <a href download> saves whatever comes back, so an error JSON ended up
 * on disk as a tiny ".osm".
 */
export async function downloadFile(url: string, filename: string, expectOsm = false): Promise<number> {
  const res = await fetch(url, { headers: { 'X-Client-Id': CLIENT_ID } });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new Error(`Файл не сохранён: ${msg}`);
  }
  const blob = await res.blob();
  if (expectOsm) {
    const head = (await blob.slice(0, 200).text()).trimStart();
    if (!head.startsWith('<?xml') && !head.startsWith('<osm')) {
      throw new Error('Файл не сохранён: сервер вернул не OSM. Обновите страницу и попробуйте снова.');
    }
  }
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
  return blob.size;
}

export interface AdapterEstimate {
  speed_kmh: number;
  lanes: [number, number];
  capacity: [number, number] | null;
}

export interface AdapterComparison {
  matsim: AdapterEstimate | null;
  sumo: AdapterEstimate | null;
  differs: boolean;
  sources: Record<'matsim' | 'sumo', string>;
}

export const api = {
  ptParams: (docId: string) => request<{routes: PtRoute[]}>(`/api/documents/${docId}/analysis/pt-params`),
  analysisPt: (docId: string, body: unknown) => request<PtResult>(`/api/documents/${docId}/analysis/pt-isochrone`, json(body)),
  analysisRoute: (docId: string, body: unknown) => request<AnalysisResult>(`/api/documents/${docId}/analysis/route`, json(body)),
  analysisCatchment: (docId: string, body: unknown) => request<AnalysisResult>(`/api/documents/${docId}/analysis/catchment`, json(body)),
  analysisIsochrone: (docId: string, body: unknown) => request<AnalysisResult>(`/api/documents/${docId}/analysis/isochrone`, json(body)),
  adapters: (docId: string, wayId: number, signal: AbortSignal) =>
    request<{ adapters: AdapterComparison }>(`/api/documents/${docId}/elements/way/${wayId}`, { signal })
      .then(({ adapters }) => adapters),
  health: () => request<{ ok: boolean; version: string; netconvert: boolean }>('/api/health'),
  samples: () => request<{ name: string; size: number }[]>('/api/samples'),
  openSample: (name: string) => request<DocState>(`/api/samples/${encodeURIComponent(name)}/open`, { method: 'POST' }),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<DocState>('/api/documents', { method: 'POST', body: fd });
  },
  op: (docId: string, op: Op, label?: string) =>
    request<OpResponse>(`/api/documents/${docId}/ops`, json({ ...op, label })),
  fixPreview: (docId: string, op: Op & { probe?: unknown }) =>
    request<FixPreview>(`/api/documents/${docId}/fix-preview`, json(op)),
  batch: (docId: string, ops: Op[], label?: string) =>
    request<OpResponse>(`/api/documents/${docId}/batch`, json({ ops, label })),
  undo: (docId: string) => request<OpResponse>(`/api/documents/${docId}/undo`, { method: 'POST' }),
  redo: (docId: string) => request<OpResponse>(`/api/documents/${docId}/redo`, { method: 'POST' }),
  validate: (docId: string) => request<ValidationResult>(`/api/documents/${docId}/validate`),
  sumo: (docId: string) => request<SumoResult>(`/api/documents/${docId}/check/sumo`, { method: 'POST' }),
  organizer: (docId: string) =>
    request<OrganizerResult>(`/api/documents/${docId}/check/organizer`, { method: 'POST' }),
  restore: async (docId: string) => {
    try {
      return await request<DocState>(`/api/documents/${docId}`);
    } catch {
      // An unscoped autosave may belong to another document. Ask for this id only.
      return request<DocState>(`/api/draft?doc_id=${encodeURIComponent(docId)}`);
    }
  },
  discardDraft: (docId: string) =>
    request<{ ok: boolean }>(`/api/draft?doc_id=${encodeURIComponent(docId)}`, { method: 'DELETE' }),
  load: (docId: string) => request<LoadState>(`/api/documents/${docId}/load`),
  setLoad: (docId: string, body: { way_id?: number; volume?: number | null; multiplier?: number; reset_all?: boolean }) =>
    request<LoadState>(`/api/documents/${docId}/load`, json(body)),
  addSnapshot: (docId: string, name: string) =>
    request<LoadState>(`/api/documents/${docId}/load/snapshots`, json({ name })),
  deleteSnapshot: (docId: string, sid: string) =>
    request<LoadState>(`/api/documents/${docId}/load/snapshots/${encodeURIComponent(sid)}`, { method: 'DELETE' }),
  compare: (docId: string, a: string, b = 'current') =>
    request<Comparison>(`/api/documents/${docId}/load/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  changes: (docId: string) => request<ChangesResult>(`/api/documents/${docId}/changes`),
  report: (docId: string) => request<ReportData>(`/api/documents/${docId}/report`),
  reportUrl: (docId: string, kind: 'xlsx' | 'docx' | 'pdf') => `/api/documents/${docId}/report.${kind}`,
  objectReportUrl: (docId: string, kind: string, id: number, fmt: 'xlsx' | 'pdf') =>
    `/api/documents/${docId}/objects/${kind}/${id}/report.${fmt}`,
  setClosure: (docId: string, wayId: number, closed: boolean) =>
    request<LoadState>(`/api/documents/${docId}/scenario/closures`, json({ way_id: wayId, closed })),
  od: (docId: string, grid = 3) => request<OdMatrix>(`/api/documents/${docId}/od?grid=${grid}`),
  tasks: (docId: string) => request<{ tasks: Task[] }>(`/api/documents/${docId}/tasks`),
  createTask: (docId: string, body: { title: string; description: string; criteria: TaskCriterion[] }) =>
    request<{ tasks: Task[] }>(`/api/documents/${docId}/tasks`, json(body)),
  deleteTask: (docId: string, id: string) =>
    request<{ tasks: Task[] }>(`/api/documents/${docId}/tasks/${id}`, { method: 'DELETE' }),
  checkTask: (docId: string, id: string) =>
    request<TaskResult>(`/api/documents/${docId}/tasks/${id}/check`, { method: 'POST' }),
  stop: (docId: string, nodeId: number) => request<StopInfo>(`/api/documents/${docId}/transit/stops/${nodeId}`),
  route: (docId: string, routeId: number) => request<RouteDetail>(`/api/documents/${docId}/transit/routes/${routeId}`),
  setRouteFrequency: (docId: string, routeId: number, vehiclesPerHour: number | null) =>
    request<RouteDetail>(`/api/documents/${docId}/transit/routes/${routeId}`, json({ vehicles_per_hour: vehiclesPerHour })),
  signal: (docId: string, nodeId: number) => request<SignalPlan>(`/api/documents/${docId}/signals/${nodeId}`),
  setSignal: (docId: string, nodeId: number, body: { cycle?: number; main_share?: number; optimize?: boolean; reset?: boolean }) =>
    request<SignalPlan>(`/api/documents/${docId}/signals/${nodeId}`, json(body)),
  exportUrl: (docId: string, filename: string) =>
    `/api/documents/${docId}/export?filename=${encodeURIComponent(filename)}`,
  /** applied edits as a Markdown table, the base for docs/fix-log.md */
  editLogUrl: (docId: string) => `/api/documents/${docId}/edit-log`,
};
