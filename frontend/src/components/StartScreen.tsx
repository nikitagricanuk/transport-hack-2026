import { useEffect, useState } from 'react';
import { api } from '../api';
import type { DocState } from '../types';

export default function StartScreen({ onLoaded }: { onLoaded: (d: DocState) => void }) {
  const [samples, setSamples] = useState<{ name: string; size: number }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);

  useEffect(() => {
    api.samples().then(setSamples).catch(() => setSamples([]));
  }, []);

  const run = async (p: Promise<DocState>) => {
    setBusy(true);
    setError(null);
    try {
      onLoaded(await p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="start">
      <div className="start-card">
        <h1>УДС·Редактор</h1>
        <p className="muted">Откройте .osm, исправьте граф и сохраните файл, который проходит MatSim → SUMO → Webots.</p>
        <label className={`drop ${over ? 'over' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) run(api.upload(f)); }}>
          <input type="file" accept=".osm,.xml" onChange={(e) => { const f = e.target.files?.[0]; if (f) run(api.upload(f)); }} />
          <b>{busy ? 'Загрузка…' : 'Перетащите .osm сюда или нажмите, чтобы выбрать'}</b>
          <span className="muted small">например, example.osm из задания</span>
        </label>
        {samples.length > 0 && (
          <div className="stack6">
            <span className="muted small">Файлы в папке data/</span>
            {samples.map((s) => (
              <button key={s.name} className="btn" disabled={busy} onClick={() => run(api.openSample(s.name))}>
                {s.name} <span className="muted mono">{(s.size / 1024 / 1024).toFixed(1)} МБ</span>
              </button>
            ))}
          </div>
        )}
        {error && <p className="bad" role="alert">{error}</p>}
      </div>
    </div>
  );
}
