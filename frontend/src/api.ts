import type { DocState, Op, OrganizerResult, SumoResult, ValidationResult } from './types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
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

export type OpResponse = DocState & { result?: { id?: number; new_id?: number }; results?: { id?: number }[] };

export const api = {
  samples: () => request<{ name: string; size: number }[]>('/api/samples'),
  openSample: (name: string) => request<DocState>(`/api/samples/${encodeURIComponent(name)}/open`, { method: 'POST' }),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<DocState>('/api/documents', { method: 'POST', body: fd });
  },
  op: (docId: string, op: Op, label?: string) =>
    request<OpResponse>(`/api/documents/${docId}/ops`, json({ ...op, label })),
  batch: (docId: string, ops: Op[], label?: string) =>
    request<OpResponse>(`/api/documents/${docId}/batch`, json({ ops, label })),
  undo: (docId: string) => request<DocState>(`/api/documents/${docId}/undo`, { method: 'POST' }),
  redo: (docId: string) => request<DocState>(`/api/documents/${docId}/redo`, { method: 'POST' }),
  validate: (docId: string) => request<ValidationResult>(`/api/documents/${docId}/validate`),
  sumo: (docId: string) => request<SumoResult>(`/api/documents/${docId}/check/sumo`, { method: 'POST' }),
  organizer: (docId: string) =>
    request<OrganizerResult>(`/api/documents/${docId}/check/organizer`, { method: 'POST' }),
  restore: async (docId: string) => {
    try {
      return await request<DocState>(`/api/documents/${docId}`);
    } catch {
      return request<DocState>('/api/draft');
    }
  },
  discardDraft: () => request<{ ok: boolean }>('/api/draft', { method: 'DELETE' }),
  exportUrl: (docId: string, filename: string) =>
    `/api/documents/${docId}/export?filename=${encodeURIComponent(filename)}`,
};
