import { useEffect, useRef } from 'react';
import { CLIENT_ID } from '../api';
import type { Peer, Selection } from '../types';

export type CollabMessage =
  | { type: 'hello'; you: Peer; peers: Peer[]; rev: number }
  | { type: 'join'; peer: Peer }
  | { type: 'leave'; id: string }
  | { type: 'presence'; peer: Peer }
  | { type: 'patch'; rev: number; by: string | null; [k: string]: unknown }
  | { type: 'rename'; filename: string }
  | { type: 'load' };

export interface Presence {
  cursor?: [number, number] | null;
  selection?: Selection;
  tool?: string;
  name?: string;
}

const NAME_KEY = 'uds-user-name';

export function savedName(): string {
  try {
    const n = localStorage.getItem(NAME_KEY);
    if (n) return n;
    const fresh = `Участник ${Math.floor(Math.random() * 90 + 10)}`;
    localStorage.setItem(NAME_KEY, fresh);
    return fresh;
  } catch {
    return 'Участник';
  }
}

export function saveName(name: string) {
  try {
    localStorage.setItem(NAME_KEY, name);
  } catch {
    /* private mode */
  }
}

/**
 * Live connection to a document: receives peers' presence and edits, sends ours.
 * Reconnects with backoff. Presence is throttled to ~20 messages per second.
 */
export function useCollab(docId: string | undefined, name: string, onMessage: (m: CollabMessage) => void) {
  const handler = useRef(onMessage);
  handler.current = onMessage;
  const socket = useRef<WebSocket | null>(null);
  const pending = useRef<Presence>({});
  const timer = useRef<number>(0);
  const last = useRef<Presence>({});

  useEffect(() => {
    if (!docId) return;
    let closed = false;
    let retry = 0;
    let reconnect = 0;
    const open = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/api/documents/${encodeURIComponent(docId)}/ws`
        + `?client=${encodeURIComponent(CLIENT_ID)}&name=${encodeURIComponent(name)}`;
      const ws = new WebSocket(url);
      socket.current = ws;
      ws.onopen = () => {
        retry = 0;
        // re-announce where we are after a reconnect
        if (Object.keys(last.current).length) ws.send(JSON.stringify({ type: 'presence', ...last.current }));
      };
      ws.onmessage = (e) => {
        try {
          handler.current(JSON.parse(e.data) as CollabMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = (e) => {
        socket.current = null;
        if (closed || e.code === 4404) return;
        retry = Math.min(retry + 1, 6);
        reconnect = window.setTimeout(open, 500 * 2 ** retry);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(reconnect);
      window.clearTimeout(timer.current);
      socket.current?.close();
      socket.current = null;
    };
    // the name is sent as presence when it changes; no reconnect needed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  const flush = () => {
    timer.current = 0;
    const ws = socket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'presence', ...pending.current }));
    pending.current = {};
  };

  return useRef({
    send(p: Presence) {
      Object.assign(pending.current, p);
      Object.assign(last.current, p);
      if (!timer.current) timer.current = window.setTimeout(flush, 50);
    },
  }).current;
}
