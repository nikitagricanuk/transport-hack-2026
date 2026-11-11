import { useEffect, useState } from 'react';
import { api } from '../api';
import type { SignalPhase, SignalPlan } from '../types';
import { fmt } from '../utils/geom';
import { levelOfService } from '../utils/los';
import ResizableTable from './ResizableTable';

const MOVE_ORDER = ['main', 'main_left', 'main_right', 'cross', 'cross_left', 'cross_right'] as const;
const MOVE_SHORT: Record<string, string> = {
  main: 'Главное', main_left: '↰ гл.', main_right: '↱ гл.', cross: 'Пересекающее', cross_left: '↰ пер.', cross_right: '↱ пер.',
};
const PHASE_CLASS = (p: { moves: string[] }) => (p.moves.includes('main') ? 'main' : p.moves.includes('cross') ? 'cross' : 'arrow');

/** Signal program: phases with their green and the movements they release, arrows included. */
export function SignalPanel({ docId, nodeId, rev, onChanged }: {
  docId: string; nodeId: number; rev: number; onChanged: () => void;
}) {
  const [plan, setPlan] = useState<SignalPlan | null>(null);
  const [draft, setDraft] = useState<SignalPhase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // the cycle runs: a clock that moves the marker over the phase bar
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 250); return () => window.clearInterval(t); }, []);
  useEffect(() => {
    let alive = true;
    api.signal(docId, nodeId).then((p) => { if (alive) { setPlan(p); setDraft(null); setError(null); } })
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [docId, nodeId, rev]);

  const send = async (body: Parameters<typeof api.setSignal>[2]) => {
    try {
      setPlan(await api.setSignal(docId, nodeId, body));
      setDraft(null);
      setError(null);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!plan) return error ? <p className="bad small">{error}</p> : <p className="muted small">План светофора…</p>;
  const inter = (plan.phases[0]?.yellow_s ?? 3) + (plan.phases[0]?.all_red_s ?? 2);
  const phases: SignalPhase[] = draft ?? plan.phases.map((p) => ({ name: p.name, green_s: p.green_s, moves: p.moves }));
  const cycle = phases.reduce((s, p) => s + p.green_s + inter, 0);
  const edit = (i: number, patch: Partial<SignalPhase>) => setDraft(phases.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const toggleMove = (i: number, m: string) => {
    const has = phases[i].moves.includes(m);
    const moves = has ? phases[i].moves.filter((x) => x !== m) : [...phases[i].moves, m];
    if (moves.length) edit(i, { moves });
  };
  const served = new Set(phases.flatMap((p) => p.moves));
  const valid = served.has('main') && served.has('cross') && cycle <= 180 && phases.length >= 2;
  // where we are in the cycle
  const tc = (now / 1000) % cycle;
  let acc = 0;
  let current = 0;
  let left = 0;
  phases.forEach((p, i) => {
    const end = acc + p.green_s + inter;
    if (tc >= acc && tc < end) { current = i; left = Math.round(end - tc); }
    acc = end;
  });
  const inGreen = (() => {
    let start = 0;
    for (let i = 0; i < current; i++) start += phases[i].green_s + inter;
    return tc - start < phases[current].green_s;
  })();
  const byGroup = (g: 'main' | 'cross') => plan.approaches.filter((a) => a.group === g);
  return (
    <div className="card stack10 signal-panel">
      <div className="row between"><b>Программа светофора</b><span className="muted small">{plan.custom ? 'задано вручную' : 'по умолчанию'}</span></div>
      <div className="phase-bar live" aria-label={`Цикл ${cycle} с`}>
        <span className="phase-now" style={{ left: `${(tc / cycle) * 100}%` }} aria-hidden="true" />
        {phases.map((p, i) => [
          <span key={`g${i}`} className={`ph ${PHASE_CLASS(p)}`} style={{ flex: p.green_s }} title={`${p.name}: ${p.green_s} с`}>{p.green_s} с</span>,
          <span key={`y${i}`} className="ph yellow" style={{ flex: 3 }} title="Жёлтый 3 с" />,
          <span key={`r${i}`} className="ph allred" style={{ flex: inter - 3 }} title={`Все красные ${inter - 3} с`} />,
        ])}
      </div>
      <div className="row between small">
        <span>Сейчас: <b className={inGreen ? 'good' : 'warn-text'}>{phases[current]?.name}</b> {inGreen ? '— зелёный' : '— жёлтый / все красные'}</span>
        <span className="mono muted">ещё {left} с · цикл {cycle} с</span>
      </div>

      <ResizableTable id="signal-phases" className="cmp small phase-table">
        <thead><tr><th>Фаза</th><th title="Зелёный, с">Зел., с</th><th>Разрешённые движения и доп. секции</th><th /></tr></thead>
        <tbody>
          {phases.map((p, i) => (
            <tr key={i}>
              <td><input className="phase-name" value={p.name} maxLength={40} aria-label={`Название фазы ${i + 1}`}
                onChange={(e) => edit(i, { name: e.target.value })} /></td>
              <td><input type="number" min={3} max={120} value={p.green_s} aria-label={`Зелёный фазы ${i + 1}, с`}
                onChange={(e) => edit(i, { green_s: Math.max(3, Math.min(120, Number(e.target.value) || 3)) })} /></td>
              <td>
                <div className="move-chips">
                  {MOVE_ORDER.map((m) => (
                    <button key={m} type="button" className="chip-btn" aria-pressed={p.moves.includes(m)}
                      title={plan.moves[m]} onClick={() => toggleMove(i, m)}>{MOVE_SHORT[m]}</button>
                  ))}
                </div>
              </td>
              <td>{phases.length > 2 && <button className="icon-btn" aria-label={`Удалить фазу ${i + 1}`} onClick={() => setDraft(phases.filter((_, j) => j !== i))}>×</button>}</td>
            </tr>
          ))}
        </tbody>
      </ResizableTable>
      <div className="row gap8 wrap">
        <button className="btn small" disabled={phases.length >= 8}
          onClick={() => setDraft([...phases, { name: 'Стрелка налево', green_s: 8, moves: ['main_left'] }])}>+ Фаза со стрелкой</button>
        <button className="btn small" disabled={phases.length >= 8}
          onClick={() => setDraft(phases.map((p, i) => (i === 1 && !p.moves.includes('main_right') ? { ...p, moves: [...p.moves, 'main_right'] } : p)))}>
          + Секция направо к пересекающей фазе</button>
      </div>
      <label className="field">Цикл, с
        <input type="number" min={20} max={180} value={cycle} onChange={(e) => {
          const target = Math.max(phases.length * inter + phases.length * 3, Math.min(180, Number(e.target.value) || cycle));
          const greens = phases.reduce((s, p) => s + p.green_s, 0) || 1;
          const k = (target - phases.length * inter) / greens;
          setDraft(phases.map((p) => ({ ...p, green_s: Math.max(3, Math.round(p.green_s * k)) })));
        }} /></label>
      {!valid && <p className="bad small">{cycle > 180 ? 'Цикл больше 180 с.' : 'Нужны фазы и главного, и пересекающего направления.'}</p>}
      {error && <p className="bad small">{error}</p>}
      {draft && <div className="row gap8">
        <button className="btn small primary" disabled={!valid} onClick={() => send({ program: phases })}>Применить программу</button>
        <button className="btn small" onClick={() => setDraft(null)}>Отменить</button>
      </div>}

      <p className="small">
        Пропускная способность подхода: c = n · s · g/C. Эффективный зелёный: главное {fmt(plan.effective_green.main, 1)} с,
        пересекающее {fmt(plan.effective_green.cross, 1)} с из {plan.cycle} с. Фаза только со стрелкой даёт подходу
        зелёный × доля поворота (налево {Math.round(plan.turn_share.left * 100)} %, направо {Math.round(plan.turn_share.right * 100)} %).
      </p>
      <p className="small muted">После каждой фазы жёлтый 3 с и все красные 2 с (ГОСТ Р 52289-2019). {plan.turn_source}. Программа сохраняется в тегах uds:signal:*; SUMO строит свои фазы сам.</p>
      <div className="row between small">
        <span className="muted">Вебстер: цикл {plan.webster.cycle} с, главному {Math.round(plan.webster.main_share * 100)} %{plan.webster.saturated ? ' (перегрузка)' : ''}</span>
        <button className="btn small primary" onClick={() => send({ optimize: true })}>Подобрать</button>
      </div>
      <ResizableTable id="transit-1" className="cmp">
        <thead><tr><th>Подход регулируемой улицы</th><th>z = v/c</th></tr></thead>
        <tbody>
          {(['main', 'cross'] as const).flatMap((g) => byGroup(g).map((a, i) => (
            <tr key={`${g}${a.way_id}${i}`}>
              <td className="ellipsis">{a.name || a.highway} <span className="muted small">{g === 'main' ? 'главное' : 'пересекающее'} · {a.bearing}°</span></td>
              <td className="mono">{a.vc !== null ? `${fmt(a.vc, 2)} (${levelOfService(a.vc)})` : '—'}</td>
            </tr>
          )))}
        </tbody>
      </ResizableTable>
      {plan.custom && <button className="link small" onClick={() => send({ reset: true })}>сбросить к двум фазам поровну</button>}
    </div>
  );
}
