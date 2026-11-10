import { useEffect, useState } from 'react';
import { api, type AdapterComparison } from '../api';

export default function AdapterPanel({ docId, wayId }: { docId: string; wayId: number }) {
  const [data, setData] = useState<AdapterComparison>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api.adapters(docId, wayId, controller.signal).then(setData, () => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [docId, wayId]);

  if (error) return <p role="alert">Не удалось загрузить параметры адаптеров.</p>;
  if (!data) return <p role="status">Загрузка параметров адаптеров…</p>;
  const format = (values: number[]) => values.join(' / ');
  return <div className="stack10">
    <p className="small muted">Оценка по исходным настройкам адаптеров. Параметры запуска у организаторов не проверены.</p>
    {data.differs && <p className="warn">Адаптеры импортируют это ребро по-разному.</p>}
    {(['matsim', 'sumo'] as const).map((adapter) => {
      const result = data[adapter];
      const name = adapter === 'matsim' ? 'MATSim' : 'SUMO';
      return <section className="card stack6" key={adapter} aria-label={name}>
        <b>{name}</b>
        {result ? <>
          <span>Скорость {result.speed_kmh} км/ч</span>
          <span>Полосы по / против направления линии {format(result.lanes)}</span>
          <span>{result.capacity ? `Пропускная способность по направлениям ${format(result.capacity)} авт./ч`
            : 'Пропускная способность не задана в типовой таблице'}</span>
        </> : <span>Для этого класса дороги в стандартной таблице нет правила импорта.</span>}
        <a href={data.sources[adapter]} target="_blank" rel="noreferrer">Исходная таблица {name}</a>
      </section>;
    })}
  </div>;
}
