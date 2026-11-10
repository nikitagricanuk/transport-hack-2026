import type { CheckGroup, OrganizerResult, SumoResult } from '../types';

const SEV_RU = { critical: 'ошибка', warning: 'проверить', info: 'к сведению' } as const;
const KIND_RU = { way: 'ребро', node: 'узел', relation: 'отношение' } as const;

/**
 * netconvert output, interpreted: groups of messages with a Russian title, what
 * to do, how many and which OSM objects (click to show on the map). The raw log
 * stays available under «Журнал netconvert» for whoever needs it.
 */
export function SumoReport({ sumo, onShow }: {
  sumo: SumoResult;
  onShow: (kind: 'way' | 'node', id: number) => void;
}) {
  const groups = sumo.report?.groups ?? [];
  const counts = sumo.report?.counts;
  return (
    <>
      <p className={sumo.ok ? 'good' : 'bad'}>
        {sumo.ok ? 'Сеть для SUMO построена без ошибок' : 'SUMO не сможет построить сеть из этого файла'}
      </p>
      {sumo.stats && (
        <p className="small">В сети SUMO: {sumo.stats.edges} рёбер, {sumo.stats.junctions} перекрёстков,{' '}
          {sumo.stats.traffic_lights} светофоров.</p>
      )}
      {counts && (
        <p className="small muted">
          Ошибок: <b className={counts.critical ? 'bad' : ''}>{counts.critical}</b> · требуют проверки: <b>{counts.warning}</b> ·
          к сведению: {counts.info}
        </p>
      )}
      <div className="check-groups">
        {groups.map((g) => <CheckGroupRow key={g.key} g={g} onShow={onShow} />)}
      </div>
      {(sumo.errors.length > 0 || sumo.warnings.length > 0) && (
        <details className="small">
          <summary className="muted">Журнал netconvert (как есть, на английском)</summary>
          <pre className="log">{[...sumo.errors, ...sumo.warnings.slice(0, 80)].join('\n')}</pre>
        </details>
      )}
    </>
  );
}

function CheckGroupRow({ g, onShow }: { g: CheckGroup; onShow: (kind: 'way' | 'node', id: number) => void }) {
  return (
    <div className={`check-group ${g.severity}`}>
      <div className="row gap8">
        <span className={`check-sev ${g.severity}`}>{SEV_RU[g.severity]}</span>
        <b className="grow">{g.title}</b>
        <span className="mono small">{g.count}</span>
      </div>
      <p className="small muted">{g.hint}</p>
      {g.key === 'error' && g.examples.length > 0 && <p className="small bad">{g.examples[0]}</p>}
      {g.refs.length > 0 && (
        <div className="check-refs small">
          {g.refs.map((r) => (r.kind === 'relation'
            ? <span key={`${r.kind}${r.id}`} className="mono muted">{KIND_RU[r.kind]} {r.id}</span>
            : (
              <button key={`${r.kind}${r.id}`} className="link mono" title="Показать на карте"
                onClick={() => onShow(r.kind as 'way' | 'node', r.id)}>{KIND_RU[r.kind]} {r.id}</button>
            )))}
        </div>
      )}
    </div>
  );
}

/** The organisers' reply as labelled fields; links open in a new tab. */
export function OrganizerReport({ result }: { result: OrganizerResult }) {
  const fields = result.fields?.length ? result.fields : result.message ? [{ label: 'Сообщение', value: result.message }] : [];
  return (
    <>
      <p className={result.ok ? 'good' : 'bad'}>
        {result.ok ? `Файл ${result.filename} принят. MatSim → SUMO → Webots поставлены в очередь.` : 'Сервер не принял файл'}
      </p>
      {fields.length > 0 && (
        <dl className="kv small">
          {fields.map((f, i) => (
            <div key={i} className="kv-row">
              <dt className="muted">{f.label}</dt>
              <dd>{/^https?:\/\//.test(f.value)
                ? <a href={f.value} target="_blank" rel="noreferrer">{f.value}</a>
                : f.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}
