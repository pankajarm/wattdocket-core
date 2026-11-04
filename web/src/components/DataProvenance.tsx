import type { DataStatus } from '../types';
import { dateLabel, observationLabel, safeSourceUrl, sourceState, SOURCE_STATE_LABEL } from '../dataPresentation';

export function DataProvenance({ data, compact = false }: { data?: DataStatus; compact?: boolean }) {
  if (!data) return <p className="data-provenance__legacy">Source dates are unavailable for this snapshot. <a href="#/data">View data coverage</a></p>;
  return <div className={`data-provenance${compact ? ' data-provenance--compact' : ''}`}>
    <div className="data-provenance__heading"><span>WattDocket snapshot published {dateLabel(data.built_at, true)}</span><a href="#/data">Coverage &amp; collection health</a></div>
    <details><summary>Sources and dates for this record</summary><p>Publication is the date this snapshot was assembled. Each source describes its own observation period.</p><ul>{data.sources.map((source) => {
      const state = sourceState(source), url = safeSourceUrl(source.url);
      return <li key={source.id}><strong>{url ? <a href={url} target="_blank" rel="noreferrer">{source.label}</a> : source.label}</strong><span>Observed: {observationLabel(source)}</span><span>Publisher release: {dateLabel(source.published_at)}</span><span>Collection checked: {dateLabel(source.last_success_at ?? source.retrieved_at, !compact)}</span>{state !== 'current' && <span className="source-state" data-state={state}>{SOURCE_STATE_LABEL[state]}</span>}</li>;
    })}</ul></details>
  </div>;
}
