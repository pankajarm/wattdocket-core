import type { ResolveResult } from '../types';
import { publicHref } from '../router';
import { dateLabel } from '../dataPresentation';
import { ReferenceCalculator } from './ReferenceCalculator';

export function AddressFallback({ result }: { result: ResolveResult }) {
  const { geocode, reference: factor, table, subregion, nearest, at_address: candidate } = result;
  return <section className="afb" aria-label="Address fallback">
    <h2 className="afb__addr">{geocode.matched_address}</h2>
    <p className="afb__where">US Census address lookup · {subregion} {factor.name}, from approximate EPA regional boundaries.</p>
    <p>{candidate ? <>A source record has a matching address: <a href={publicHref(candidate.entry.id)}>{candidate.entry.name}</a>. Address agreement does not independently verify a physical facility.</> : 'No matching source record was found. This address lookup does not establish that a data center exists here.'} Geographic context does not establish a serving utility.</p>
    <details className="grid-factor-details"><summary>Factor and snapshot dates</summary><dl className="source-row__facts"><div><dt>Observation period</dt><dd>{factor.data_vintage || 'Not supplied'}</dd></div><div><dt>Publisher release</dt><dd>{dateLabel(factor.published_at)}</dd></div><div><dt>Source retrieved</dt><dd>{dateLabel(factor.retrieved_at, true)}</dd></div><div><dt>WattDocket snapshot published</dt><dd>{dateLabel(table.built_at, true)}</dd></div></dl></details>
    <ReferenceCalculator key={`${geocode.lat},${geocode.lon}`} rate={factor.rate_lb_per_mwh} year={factor.data_vintage} region={factor.name} sourceUrl={factor.source_url || table.factor_set.url} evidenceUrl={factor.evidence_url || factor.citation.url} />
    {nearest.length > 0 && <div className="afb__near"><h3>Nearby source records</h3><p>Distance is geographic proximity, not evidence of a shared site.</p>{nearest.map(({entry, km}) => <a className="rrow" key={entry.id} href={publicHref(entry.id)}><span className="rrow__name">{entry.name}</span><span className="rrow__addr">{entry.address_line}</span><span className="rrow__city">{entry.city}, {entry.state}</span><span className="rrow__id">{(km / 1.609344).toLocaleString('en-US', {maximumFractionDigits:1})} mi away</span></a>)}</div>}
  </section>;
}
