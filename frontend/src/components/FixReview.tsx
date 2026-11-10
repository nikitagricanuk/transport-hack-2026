import { useState } from 'react';
import type { Fix, FixPreview, Issue } from '../types';

/** What the user is reviewing: an issue, one of its suggested fixes and its dry run. */
export interface FixReviewState {
  issue: Issue;
  fix: Fix;
  preview: FixPreview | null;
  error: string | null;
}

const fmtM = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} км` : `${Math.round(m)} м`);

/**
 * Review card shown before a suggested fix is applied. The fix is drawn on the
 * map (green — added, red dashed — removed, blue — changed); nothing changes in
 * the document until the user presses «Применить». A fix that removes road asks
 * the user to confirm that the road does not exist in reality.
 */
export function FixReview({ review, onApply, onCancel }: {
  review: FixReviewState;
  onApply: () => void;
  onCancel: () => void;
}) {
  const { issue, fix, preview, error } = review;
  const removes = (preview?.removed_road_m ?? 0) > 0;
  const [checked, setChecked] = useState(false);
  const canApply = !!preview && !error && !preview.empty && (!removes || checked);
  return (
    <div className="fix-review" role="dialog" aria-label="Проверка исправления">
      <div className="fix-review-head">
        <b>{fix.label}</b>
        <span className="muted small">{issue.title}</span>
      </div>
      {error ? <p className="bad small">Исправление не применимо: {error}</p>
        : !preview ? <p className="muted small">Считаю, что изменится…</p>
          : (
            <>
              {preview.probe && <p className="small">После исправления: {preview.probe.found ? `путь найден, ${((preview.probe.stats?.time_s ?? 0) / 60).toFixed(1)} мин` : 'путь всё ещё не найден'}</p>}
              <ul className="fix-review-list small">
                {preview.counts.added > 0 && (
                  <li><span className="swatch added" />Добавит: {preview.counts.added} объект(ов)
                    {preview.added_road_m > 0 && `, ${fmtM(preview.added_road_m)} дороги`}</li>
                )}
                {preview.counts.deleted > 0 && (
                  <li><span className="swatch deleted" />Удалит: {preview.counts.deleted} объект(ов)
                    {removes && `, ${fmtM(preview.removed_road_m)} дороги`}</li>
                )}
                {preview.counts.modified > 0 && (
                  <li><span className="swatch modified" />Изменит: {preview.counts.modified} объект(ов)</li>
                )}
                {preview.empty && <li>Ничего не изменит</li>}
              </ul>
              {fix.op === 'replace_restriction_way' && (
                <p className="fix-review-note small">На карте выделено предлагаемое ребро.
                  Сверьте направление и знаки на перекрёстке перед применением.</p>
              )}
              {preview.features.some((f) => f.properties.fringe) && (
                <div className="fix-review-note small">
                  <p>Фиктивное ребро на границе выгрузки: в реальности этой дороги нет. Оно соединяет
                    проезжие части у края фрагмента, чтобы MatSim мог развернуть машины (иначе граф не сильно связан).
                    Ребро будет видно и в видео SUMO и Webots. Можно отказаться — тогда останется предупреждение.</p>
                </div>
              )}
              {removes && (
                <div className="fix-review-warn small">
                  <p>Удаляется {fmtM(preview.removed_road_m)} дороги. Сбой в данных может означать и лишнюю дорогу,
                    и недостающую связь: сверьтесь со спутником или панорамами, прежде чем удалять.
                    Если дорога есть в реальности — выберите «Соединить» или «Присоединить».</p>
                  <label className="check">
                    <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                    <span>Проверил: этой дороги нет в реальности</span>
                  </label>
                </div>
              )}
            </>
          )}
      <div className="row gap8">
        <span className="muted small">На карте: <span className="legend added">добавится</span>{' '}
          <span className="legend deleted">удалится</span> <span className="legend modified">изменится</span></span>
        <span className="grow" />
        <button className="btn small" onClick={onCancel}>Отмена</button>
        <button className={`btn small ${removes ? 'danger' : 'primary'}`} disabled={!canApply} onClick={onApply}>
          Применить
        </button>
      </div>
    </div>
  );
}
