import { useEffect, useState } from 'react';
import { api, type DocumentSummary } from '../api';
import type { DocState } from '../types';

export default function StartScreen({ onLoaded, initialError }: { onLoaded: (d: DocState) => void; initialError?: string | null }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [samples, setSamples] = useState<{ name: string; size: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [notice, setNotice] = useState('');
  const [over, setOver] = useState(false);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<'all' | 'recent'>('all');
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [recent] = useState<string[]>(() => {
    try { const ids = JSON.parse(localStorage.getItem('uds-recent-documents') || '[]'); return Array.isArray(ids) ? ids : []; }
    catch { return []; }
  });

  useEffect(() => {
    let active = true;
    const refresh = () => {
      api.documents().then(rows => { if (active) setDocuments(rows); })
        .catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    };
    refresh();
    api.samples().then(rows => { if (active) setSamples(rows); }).catch(e => { if (active) setError(e.message); });
    const timer = window.setInterval(refresh, 10000);
    window.addEventListener('focus', refresh);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, []);

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError(null); setNotice('');
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const open = (action: () => Promise<DocState>) => run(async () => onLoaded(await action()));
  const upload = (file?: File) => { if (file) open(() => api.upload(file)); };
  const rows = documents.filter(d => d.filename.toLocaleLowerCase().includes(query.toLocaleLowerCase()) && (tab === 'all' || recent.includes(d.doc_id)));
  if (tab === 'recent') rows.sort((a, b) => recent.indexOf(a.doc_id) - recent.indexOf(b.doc_id));

  return <main className="workspace">
    <header className="workspace-header"><a className="brand" href="/">◈ <b>Редактор УДС</b></a><span className="workspace-badge">Общее пространство</span></header>
    <div className="workspace-content">
      <section className="workspace-intro">
        <div><span className="workspace-eyebrow">РАБОТАЙТЕ ВМЕСТЕ</span><h1>Ваши документы</h1><p className="muted">Откройте карту, присоединитесь к команде или начните новый проект.</p></div>
        <button className="btn primary" disabled={busy} onClick={() => { setEditing('new'); setName('Новая карта.osm'); }}>＋ Новый документ</button>
      </section>
      {error && <div className="card bad" role="alert">{error}</div>}
      {notice && <p role="status" className="good">{notice}</p>}
      {busy && <p role="status" className="muted">Сохраняем или открываем документ…</p>}
      {editing && <form className="workspace-name card" onSubmit={e => { e.preventDefault(); if (!name.trim()) return;
        if (editing === 'new') open(async () => { const d = await api.newDocument(name.trim()); setEditing(null); return d; });
        else run(async () => { const renamed = await api.renameDocument(editing, name.trim()); setDocuments(ds => ds.map(d => d.doc_id === editing ? { ...d, filename: renamed.filename } : d)); setEditing(null); });
      }}>
        <label htmlFor="document-name">{editing === 'new' ? 'Название нового документа' : 'Переименовать документ'}</label>
        <input id="document-name" autoFocus maxLength={160} value={name} onChange={e => setName(e.target.value)} disabled={busy} required />
        <button className="btn primary" disabled={busy || !name.trim()}>{editing === 'new' ? 'Создать' : 'Сохранить'}</button>
        <button type="button" className="btn" disabled={busy} onClick={() => setEditing(null)}>Отмена</button>
      </form>}
      <section className="workspace-starters" aria-labelledby="starter-title">
        <h2 id="starter-title">Быстрый старт</h2><p className="muted">Готовые карты для хакатона. Участники открывают один общий документ и видят правки друг друга.</p>
        <div className="workspace-sample-grid">
          {samples.map(s => <button className="workspace-sample" disabled={busy} key={s.name} onClick={() => open(() => api.openSharedSample(s.name))}>
            <span className="workspace-map-icon" aria-hidden="true">⌘</span><strong>{s.name}</strong><span className="muted small">Общая карта · {(s.size / 1024 / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ</span><span className="workspace-sample-link">Открыть с командой ↗</span>
          </button>)}
          <label className={`workspace-sample workspace-upload ${over ? 'over' : ''}`} onDragOver={e => { e.preventDefault(); if (!busy) setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={e => { e.preventDefault(); setOver(false); if (!busy) upload(e.dataTransfer.files[0]); }}>
            <span className="workspace-map-icon" aria-hidden="true">↑</span><strong>Загрузить свою карту</strong><span className="muted small">Перетащите .osm / .xml или выберите файл</span>
            <input aria-label="Загрузить карту" type="file" accept=".osm,.xml" disabled={busy} onChange={e => { upload(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
        </div>
      </section>
      <section aria-label="Сохранённые документы">
        <div className="workspace-toolbar"><div className="seg" role="group" aria-label="Фильтр документов"><button aria-pressed={tab === 'all'} onClick={() => setTab('all')}>Все документы · {documents.length}</button><button aria-pressed={tab === 'recent'} onClick={() => setTab('recent')}>Недавно открытые</button></div><input type="search" aria-label="Поиск документов" placeholder="Поиск по названию…" value={query} onChange={e => setQuery(e.target.value)} /></div>
        <p className="muted small">Документы доступны всем участникам. Изменения сохраняются автоматически.</p>
        <div className="workspace-documents">
          {rows.map(d => <article className="workspace-document" key={d.doc_id}>
            <span className="workspace-file-icon" aria-hidden="true">▤</span><div className="workspace-document-info"><button className="link" disabled={busy} onClick={() => open(() => api.restore(d.doc_id))}><strong>{d.filename}</strong></button><span className="muted small">{d.updated_at ? `Изменён ${new Date(d.updated_at * 1000).toLocaleString('ru-RU')}` : 'Сохранённый документ'} · {d.doc_id.slice(0, 12)}</span></div>
            <span className={`small ${d.peers.length ? 'good' : 'muted'}`} title={d.peers.map(p => p.name).join(', ')}>{d.peers.length ? `● В сети: ${d.peers.length}` : 'Готов к работе'}</span>
            <div className="workspace-actions"><button className="btn ghost" disabled={busy} onClick={() => { setEditing(d.doc_id); setName(d.filename); }}>Переименовать</button><button className="btn ghost" disabled={busy} onClick={() => run(async () => { const url = new URL(window.location.href); url.search = ''; url.hash = ''; url.searchParams.set('doc', d.doc_id); await navigator.clipboard.writeText(url.toString()); setNotice('Ссылка скопирована — отправьте её команде.'); })}>Поделиться</button><button className="btn" disabled={busy} onClick={() => open(() => api.restore(d.doc_id))}>Открыть ↗</button></div>
          </article>)}
          {!rows.length && <div className="workspace-empty"><h3>{loading ? 'Загрузка документов…' : query ? 'Ничего не найдено' : tab === 'recent' ? 'Здесь появятся недавно открытые карты' : 'Начните с первой карты'}</h3><p className="muted">{query ? 'Попробуйте другое название.' : 'Откройте готовую карту выше, загрузите .osm или создайте документ.'}</p></div>}
        </div>
      </section>
      <footer className="workspace-footer muted small">Редактирование дорожной сети · Аналитика · Совместная работа</footer>
    </div>
  </main>;
}
