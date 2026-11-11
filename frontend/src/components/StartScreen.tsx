import { useEffect, useMemo, useState } from 'react';
import { api, loadRecentDocuments, type DocumentSummary, type RecentDocument } from '../api';
import type { DocState } from '../types';

function isSharedId(id: string) {
  return id.startsWith('sample-');
}

export default function StartScreen({ onLoaded, initialError }: { onLoaded: (d: DocState) => void; initialError?: string | null }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [samples, setSamples] = useState<{ name: string; size: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [notice, setNotice] = useState('');
  const [over, setOver] = useState(false);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<'shared' | 'private'>('shared');
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [recent, setRecent] = useState<RecentDocument[]>(() => loadRecentDocuments());

  useEffect(() => {
    let active = true;
    const refresh = () => {
      api.documents().then((rows) => { if (active) setDocuments(rows); })
        .catch((e) => { if (active) setError(e.message); })
        .finally(() => { if (active) setLoading(false); });
      if (active) setRecent(loadRecentDocuments());
    };
    refresh();
    api.samples().then((rows) => { if (active) setSamples(rows); }).catch((e) => { if (active) setError(e.message); });
    const timer = window.setInterval(refresh, 10000);
    window.addEventListener('focus', refresh);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, []);

  const privateRows = useMemo(() => {
    const sharedIds = new Set(documents.map((d) => d.doc_id));
    return recent
      .filter((r) => !isSharedId(r.doc_id) && !sharedIds.has(r.doc_id))
      .map((r) => ({
        doc_id: r.doc_id,
        filename: r.filename,
        updated_at: 0,
        peers: [] as DocumentSummary['peers'],
        shared: false,
      }));
  }, [documents, recent]);

  const rows = useMemo(() => {
    const source = tab === 'shared' ? documents : privateRows;
    const q = query.toLocaleLowerCase();
    return source.filter((d) => d.filename.toLocaleLowerCase().includes(q));
  }, [documents, privateRows, query, tab]);

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError(null); setNotice('');
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const open = (action: () => Promise<DocState>) => run(async () => onLoaded(await action()));
  const upload = (file?: File) => { if (file) open(() => api.upload(file)); };

  const copyLink = (docId: string) => run(async () => {
    const url = new URL(window.location.href);
    url.search = '';
    url.hash = '';
    url.searchParams.set('doc', docId);
    await navigator.clipboard.writeText(url.toString());
    setNotice(tab === 'private'
      ? 'Ссылка на частный проект скопирована — её видят только те, кому вы отправите.'
      : 'Ссылка скопирована — отправьте её команде.');
  });

  return (
    <main className="workspace">
      <header className="workspace-header">
        <a className="brand" href="/">◈ <b>Редактор УДС</b></a>
        <span className="workspace-badge">Хакатон · общие комнаты и частные проекты</span>
      </header>
      <div className="workspace-content">
        <section className="workspace-intro">
          <div>
            <span className="workspace-eyebrow">РАБОТАЙТЕ ВМЕСТЕ</span>
            <h1>Проекты</h1>
            <p className="muted">Подключитесь к общей карте для теста или создайте частный проект — он не появится в общем списке.</p>
          </div>
          <button className="btn primary" disabled={busy} onClick={() => { setEditing('new'); setName('Новая карта.osm'); setTab('private'); }}>
            ＋ Новый частный проект
          </button>
        </section>

        {error && <div className="card bad" role="alert">{error}</div>}
        {notice && <p role="status" className="good">{notice}</p>}
        {busy && <p role="status" className="muted">Сохраняем или открываем документ…</p>}

        {editing && (
          <form
            className="workspace-name card"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) return;
              if (editing === 'new') {
                open(async () => {
                  const d = await api.newDocument(name.trim());
                  setEditing(null);
                  return d;
                });
              } else {
                run(async () => {
                  const renamed = await api.renameDocument(editing, name.trim());
                  setDocuments((ds) => ds.map((d) => (d.doc_id === editing ? { ...d, filename: renamed.filename } : d)));
                  setRecent((rs) => rs.map((r) => (r.doc_id === editing ? { ...r, filename: renamed.filename } : r)));
                  setEditing(null);
                });
              }
            }}
          >
            <label htmlFor="document-name">{editing === 'new' ? 'Название частного проекта' : 'Переименовать документ'}</label>
            <input id="document-name" autoFocus maxLength={160} value={name} onChange={(e) => setName(e.target.value)} disabled={busy} required />
            <button className="btn primary" disabled={busy || !name.trim()}>{editing === 'new' ? 'Создать' : 'Сохранить'}</button>
            <button type="button" className="btn" disabled={busy} onClick={() => setEditing(null)}>Отмена</button>
          </form>
        )}

        <section className="workspace-starters" aria-labelledby="starter-title">
          <h2 id="starter-title">Общие карты для теста</h2>
          <p className="muted">Несколько заранее открытых комнат. Участники заходят в один документ и видят правки друг друга.</p>
          <div className="workspace-sample-grid">
            {samples.map((s) => (
              <button className="workspace-sample" disabled={busy} key={s.name} onClick={() => open(() => api.openSharedSample(s.name))}>
                <span className="workspace-map-icon" aria-hidden="true">⌘</span>
                <strong>{s.name}</strong>
                <span className="muted small">Общая карта · {(s.size / 1024 / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ</span>
                <span className="workspace-sample-link">Подключиться ↗</span>
              </button>
            ))}
            <label
              className={`workspace-sample workspace-upload ${over ? 'over' : ''}`}
              onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) upload(e.dataTransfer.files[0]); }}
            >
              <span className="workspace-map-icon" aria-hidden="true">↑</span>
              <strong>Загрузить частную карту</strong>
              <span className="muted small">Перетащите .osm / .xml — проект останется только у вас и по ссылке</span>
              <input aria-label="Загрузить карту" type="file" accept=".osm,.xml" disabled={busy} onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }} />
            </label>
          </div>
        </section>

        <section aria-label="Список проектов">
          <div className="workspace-toolbar">
            <div className="seg" role="group" aria-label="Фильтр проектов">
              <button aria-pressed={tab === 'shared'} onClick={() => setTab('shared')}>
                Общие · {documents.length}
              </button>
              <button aria-pressed={tab === 'private'} onClick={() => setTab('private')}>
                Мои частные · {privateRows.length}
              </button>
            </div>
            <input type="search" aria-label="Поиск проектов" placeholder="Поиск по названию…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <p className="muted small">
            {tab === 'shared'
              ? 'Только предзагруженные общие комнаты. Частные загрузки сюда не попадают.'
              : 'Частные проекты этого браузера. Поделитесь ссылкой, если нужен доступ команде.'}
          </p>
          <div className="workspace-documents">
            {rows.map((d) => (
              <article className="workspace-document" key={d.doc_id}>
                <span className="workspace-file-icon" aria-hidden="true">▤</span>
                <div className="workspace-document-info">
                  <button className="link" disabled={busy} onClick={() => open(() => api.restore(d.doc_id))}>
                    <strong>{d.filename}</strong>
                  </button>
                  <span className="muted small">
                    {tab === 'shared' ? 'Общая комната' : 'Частный проект'}
                    {d.updated_at ? ` · изменён ${new Date(d.updated_at * 1000).toLocaleString('ru-RU')}` : ''}
                    {' · '}{d.doc_id.slice(0, 12)}
                  </span>
                </div>
                <span className={`small ${d.peers.length ? 'good' : 'muted'}`} title={d.peers.map((p) => p.name).join(', ')}>
                  {d.peers.length ? `● В сети: ${d.peers.length}` : tab === 'shared' ? 'Готов к подключению' : 'Только по ссылке'}
                </span>
                <div className="workspace-actions">
                  <button className="btn ghost" disabled={busy} onClick={() => { setEditing(d.doc_id); setName(d.filename); }}>Переименовать</button>
                  <button className="btn ghost" disabled={busy} onClick={() => copyLink(d.doc_id)}>Поделиться</button>
                  <button className="btn" disabled={busy} onClick={() => open(() => api.restore(d.doc_id))}>
                    Открыть ↗
                  </button>
                </div>
              </article>
            ))}
            {!rows.length && (
              <div className="workspace-empty">
                <h3>
                  {loading && tab === 'shared'
                    ? 'Загрузка общих проектов…'
                    : query
                      ? 'Ничего не найдено'
                      : tab === 'private'
                        ? 'Частных проектов пока нет'
                        : 'Общие карты ещё не готовы'}
                </h3>
                <p className="muted">
                  {query
                    ? 'Попробуйте другое название.'
                    : tab === 'private'
                      ? 'Создайте частный проект или загрузите свой .osm — он появится здесь.'
                      : 'Откройте общую карту выше, чтобы участники могли подключиться.'}
                </p>
              </div>
            )}
          </div>
        </section>

        <footer className="workspace-footer muted small">
          <a href="/mcp/connect" target="_blank" rel="noreferrer">Подключить ИИ через MCP</a>
          {' · '}Редактирование дорожной сети · Аналитика · Совместная работа
        </footer>
      </div>
    </main>
  );
}
