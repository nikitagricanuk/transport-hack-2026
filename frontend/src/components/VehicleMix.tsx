import { useEffect, useRef, useState } from 'react';
import type { VehicleMixInput, VehicleType } from '../types';
import { fmt } from '../utils/geom';

/** Traffic composition: share of every vehicle type and its passenger-car equivalent. */
export function VehicleMix({ mix, onChange }: {
  mix: VehicleType[];
  onChange: (mix: VehicleMixInput) => void;
}) {
  const [draft, setDraftState] = useState(mix);
  // blur can fire before the re-render that follows the last keystroke
  const latest = useRef(mix);
  const setDraft = (f: (d: VehicleType[]) => VehicleType[]) => { latest.current = f(latest.current); setDraftState(latest.current); };
  useEffect(() => { latest.current = mix; setDraftState(mix); }, [mix]);
  const others = draft.filter((v) => v.key !== 'car').reduce((s, v) => s + v.share, 0);
  const over = others > 1 + 1e-9;
  const rows = draft.map((v) => (v.key === 'car' ? { ...v, share: Math.max(0, 1 - others) } : v));
  const p = rows.reduce((s, v) => s + v.share * v.pcu, 0);
  const commit = (next: VehicleType[]) => {
    const rest = next.filter((v) => v.key !== 'car').reduce((s, v) => s + v.share, 0);
    if (rest > 1 + 1e-9) return;
    onChange(Object.fromEntries(next.filter((v) => (v.key !== 'car' && v.share > 0) || v.pcu !== v.default_pcu)
      .map((v) => [v.key, v.key === 'car' ? { share: 0, pcu: v.pcu } : { share: v.share, pcu: v.pcu }])));
  };
  const edit = (key: string, patch: Partial<VehicleType>) => setDraft((d) => d.map((v) => (v.key === key ? { ...v, ...patch } : v)));
  return (
    <div className="stack6 vehicle-mix">
      <b className="small">Состав потока</b>
      <table className="cmp small">
        <thead><tr><th>Тип транспорта</th><th title="Доля в потоке">Доля, %</th><th title="Коэффициент приведения к легковому автомобилю">K</th></tr></thead>
        <tbody>
          {rows.map((v) => (
            <tr key={v.key}>
              <td title={`K по умолчанию ${fmt(v.default_pcu, 1)} — ${v.source}`}>{v.label}</td>
              <td>{v.key === 'car'
                ? <span className="mono" title="Легковые — остаток до 100 %">{fmt(v.share * 100, 0)}</span>
                : <input type="number" min={0} max={100} step={1} aria-label={`Доля: ${v.label}, %`}
                    value={Math.round(v.share * 1000) / 10}
                    onChange={(e) => edit(v.key, { share: Math.max(0, Math.min(100, +e.target.value)) / 100 })}
                    onBlur={() => commit(latest.current)} />}</td>
              <td><input type="number" min={0.1} max={6} step={0.1} aria-label={`Коэффициент приведения: ${v.label}`}
                value={v.pcu} className={v.pcu !== v.default_pcu ? 'custom' : undefined}
                onChange={(e) => edit(v.key, { pcu: Math.max(0.1, Math.min(6, +e.target.value || v.default_pcu)) })}
                onBlur={() => commit(latest.current)} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {over
        ? <p className="bad small">Сумма долей без легковых больше 100 %.</p>
        : <p className="small">Приведённый поток: p = Σ доля · K = <b className="mono">{fmt(p, 3)}</b> — 1 авт./ч смешанного потока ≈ {fmt(p, 2)} прив. авт./ч.</p>}
      <p className="small muted">K для мототранспорта и грузовых — СП 34.13330.2021, табл. 5.3; велосипед 0,3 — допущение для движения по проезжей части. Коэффициенты можно изменить.</p>
      <button className="link small" onClick={() => onChange({})}>сбросить состав</button>
    </div>
  );
}
