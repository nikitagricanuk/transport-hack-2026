import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Op } from '../types';
import { useEnterKey } from '../utils/useEnterKey';

interface PlanItem {
  issue_id: string;
  title: string;
  severity: 'critical' | 'warning' | 'info';
  description: string;
  refs: { kind: string; id: number }[];
  location: [number, number] | null;
}
interface SafeItem extends PlanItem { label: string; op: string; args: Record<string, unknown> }
interface DisputedItem extends PlanItem { reason: string; options: number }
interface Plan { safe: SafeItem[]; disputed: DisputedItem[] }

const SEV = { critical: 'критично', warning: 'предупреждение', info: 'замечание' } as const;

/**
 * «Править всё». Every safe fix (one suggestion that only sets tags, smooths Z or
 * removes a broken relation) is listed with a checkbox; the user confirms the list
 * and it is applied as one undo step. Disputed issues — several options, removals,
 * new geometry — stay with the user, each with the reason and a link to the map.
 */
export function FixAllDialog({ docId, onApply, onShow, onClose }: {
  docId: string;
  onApply: (ops: Op[], label: string) => void;
  onShow: (location: [number, number] | null, ref?: { kind: string; id: number }) => void;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [off, setOff] = useState<Set<number>>(new Set());
  useEffect(() => {
    let alive = true;
    api.fixAllPlan(docId).then((p) => alive && setPlan(p as Plan)).catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [docId]);
  const chosen = plan ? plan.safe.filter((_, i) => !off.has(i)) : [];
  const apply = () => {
    if (!chosen.length) return;
    onApply(chosen.map((s) => ({ op: s.op, args: s.args })), `Править всё: ${chosen.length} исправлений`);
    onClose();
  };
  useEnterKey(chosen.length > 0, apply);
  const toggle = (i: number) => setOff((prev) => {
    const next = new Set(prev);
    if (next.has(i)) next.delete(i); else next.add(i);
    return next;
  });
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal fix-all" role="dialog" aria-label="Править всё" onClick={(e) => e.stopPropagation()}>
        <h2>Править всё</h2>
        {error ? <p className="bad">План исправлений не загрузился: {error}</p> : !plan ? <p className="muted">Подбираю исправления…</p> : (
          <>
            <p className="small">
              Ниже — все изменения, которые будут внесены. Применяются только отмеченные, одним шагом отмены
              (Ctrl+Z или Esc), и попадают в журнал правок.
            </p>
            <div className="row between">
              <b>Безопасные исправления · {chosen.length} из {plan.safe.length}</b>
              <span className="row gap8">
                <button className="btn small" onClick={() => setOff(new Set())}>Отметить все</button>
                <button className="btn small" onClick={() => setOff(new Set(plan.safe.map((_, i) => i)))}>Снять все</button>
              </span>
            </div>
            <div className="fix-all-list">
              {plan.safe.length === 0 && <p className="muted small">Безопасных исправлений нет.</p>}
              {plan.safe.map((s, i) => (
                <label key={`${s.issue_id}:${i}`} className="fix-all-row">
                  <input type="checkbox" checked={!off.has(i)} onChange={() => toggle(i)} />
                  <span className="grow">
                    <b>{s.label}</b> <span className="muted">— {s.title}</span><br />
                    <span className="small muted">{s.description}</span>
                  </span>
                  <button type="button" className="link small" onClick={(e) => { e.preventDefault(); onShow(s.location, s.refs[0]); }}>на карте</button>
                </label>
              ))}
            </div>
            <b>Спорные — решите сами · {plan.disputed.length}</b>
            <div className="fix-all-list">
              {plan.disputed.length === 0 && <p className="muted small">Спорных нет.</p>}
              {plan.disputed.map((d, i) => (
                <div key={`${d.issue_id}:${i}`} className="fix-all-row disputed">
                  <span className={`sev ${d.severity}`} title={SEV[d.severity]}>{d.severity === 'info' ? 'i' : '!'}</span>
                  <span className="grow"><b>{d.title}</b><br /><span className="small muted">{d.reason}</span></span>
                  <button type="button" className="link small" onClick={() => { onShow(d.location, d.refs[0]); onClose(); }}>разобрать</button>
                </div>
              ))}
            </div>
          </>
        )}
        <div className="row gap8">
          <span className="grow" />
          <button className="btn" onClick={onClose}>Отмена</button>
          <button className="btn primary" disabled={!chosen.length} onClick={apply}>
            Применить {chosen.length ? `(${chosen.length})` : ''}
          </button>
        </div>
      </div>
    </div>
  );
}
