import type { Fix, Issue } from '../types';

const SEV_RU = { critical: 'Критично', warning: 'Предупреждение', info: 'Замечание' } as const;
const CAT_RU: Record<string, string> = {
  connectivity: 'Связность', direction: 'Направление движения', junction: 'Перекрёстки', lanes: 'Полосы',
  geometry: 'Геометрия и высоты', data: 'Данные и теги', pedestrian: 'Пешеходная сеть',
};
const LEVEL_RU: Record<string, string> = { matsim: 'MatSim', sumo: 'SUMO', webots: 'Webots' };
const KIND_RU: Record<string, string> = { way: 'ребро', node: 'узел', relation: 'отношение' };

/**
 * Properties of the issue the user clicked: what it is, why it matters (which simulator
 * level it breaks), the objects involved, the suggested fixes, and «Это не ошибка» to
 * hide a false alarm from the list (it can be brought back; the file is not changed).
 */
export function IssueDetails({ issue, onFix, onShowRef, onDismiss, onClose }: {
  issue: Issue;
  onFix: (fix: Fix) => void;
  onShowRef: (kind: 'way' | 'node', id: number) => void;
  onDismiss: (dismissed: boolean) => void;
  onClose: () => void;
}) {
  const fixes = issue.fixes?.length ? issue.fixes : issue.fix ? [issue.fix] : [];
  return (
    <div className="issue-details" role="dialog" aria-label="Свойства ошибки">
      <div className="row between">
        <span className={`check-sev ${issue.severity}`}>{SEV_RU[issue.severity]}</span>
        <button className="icon-btn" aria-label="Закрыть" onClick={onClose}>×</button>
      </div>
      <b>{issue.title}</b>
      <p className="small">{issue.description}</p>
      <dl className="kv small">
        <div className="kv-row"><dt className="muted">Категория</dt><dd>{CAT_RU[issue.category] ?? issue.category}</dd></div>
        <div className="kv-row"><dt className="muted">Влияет на</dt>
          <dd>{issue.levels.length ? issue.levels.map((l) => LEVEL_RU[l] ?? l).join(', ') : 'только качество данных'}</dd></div>
        <div className="kv-row"><dt className="muted">Объекты</dt>
          <dd className="row gap8" style={{ flexWrap: 'wrap' }}>
            {issue.refs.map((r) => (r.kind === 'relation'
              ? <span key={`${r.kind}${r.id}`} className="mono">{KIND_RU[r.kind]} {r.id}</span>
              : <button key={`${r.kind}${r.id}`} className="link mono" onClick={() => onShowRef(r.kind as 'way' | 'node', r.id)}>
                  {KIND_RU[r.kind]} {r.id}</button>))}
          </dd></div>
        {issue.location && (
          <div className="kv-row"><dt className="muted">Место</dt>
            <dd className="mono">{issue.location[1].toFixed(6).replace('.', ',')}° с. ш., {issue.location[0].toFixed(6).replace('.', ',')}° в. д.</dd></div>
        )}
      </dl>
      {fixes.length > 0 && (
        <div className="stack4">
          <span className="small muted">Предложенные исправления (сначала превью на карте):</span>
          <div className="row gap8" style={{ flexWrap: 'wrap' }}>
            {fixes.map((f) => <button key={`${f.op}:${f.label}`} className="btn small" onClick={() => onFix(f)}>{f.label}</button>)}
          </div>
        </div>
      )}
      <div className="row gap8">
        <span className="grow" />
        {issue.dismissed
          ? <button className="btn small" onClick={() => onDismiss(false)}>Вернуть в список</button>
          : <button className="btn small" title="Убрать из списка как ложное срабатывание; файл не меняется, вернуть можно в «Скрытые»"
              onClick={() => onDismiss(true)}>Это не ошибка — скрыть</button>}
      </div>
    </div>
  );
}
