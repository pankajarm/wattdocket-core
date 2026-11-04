import { useState } from 'react';
import type { DisclosureLocation, DisclosureRecordLink, FacilityObservations, LinkedDisclosure, ReportedObservation } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';
import { publicHref } from '../router';

const scopeName = {campus:'Campus',building:'Building',regulatory_facility:'Regulatory reporting facility',operator_location:'Operator reporting location'};
export const disclosedFamily = (metric: string) => metric === 'electricity_consumption' ? 'electricity' : metric.startsWith('water_') ? 'water' : 'emissions';
function Observation({ row }: {row: ReportedObservation}) {
  const url = safeSourceUrl(row.source_url);
  return <tr><th scope="row">{row.label}<span>{row.data_year} · {row.reporting_basis.replace(/_/g,' ')}</span></th>
    <td>{row.value.toLocaleString('en-US',{maximumFractionDigits:3})}<span>{row.unit}</span></td>
    <td><a href={url ? `${url}${typeof row.source_page === 'number' ? `#page=${row.source_page}` : ''}` : undefined} target="_blank" rel="noreferrer">{row.publisher}{typeof row.source_page === 'number' ? ` · p. ${row.source_page}` : ''}</a>
      <details><summary>Method and receipt</summary><p>{row.methodology_note}</p><p>Reporting period: {dateLabel(row.period_start)} to {dateLabel(row.period_end)} (end exclusive).</p><p>Retrieved {dateLabel(row.retrieved_at,true)}{row.source_status === 'stale' ? ' · retained after a delayed collection' : ''}.</p><p>Source SHA-256: <code>{row.source_sha256}</code></p>{row.source_field && <p>Field: {row.source_field}</p>}</details></td></tr>;
}
function LinkEvidence({ link }: {link: DisclosureRecordLink}) {
  return <details className="disclosure-link"><summary>Why this record is linked</summary>
    <p>{link.relationship === 'same_physical_scope' ? 'The reviewed evidence supports the same reporting boundary.' : 'The reviewed evidence links this record to the reporting location. The location can include other buildings or records; its totals are not this record’s individual measurements.'}</p>
    <p>{link.reason || link.note}</p>
    {(link.evidence ?? []).map((e,index) => {const url = safeSourceUrl(e.url || e.source_url);return <p key={index}>{e.excerpt || e.quote || e.excerpts?.join(' ')} {url && <a href={url} target="_blank" rel="noreferrer">{e.title || e.publisher || 'Identity evidence'}</a>}</p>;})}
  </details>;
}
function LocationEvidence({ location, link }: {location: DisclosureLocation; link?: DisclosureRecordLink}) {
  return <div className="disclosure-location">
    <p>{scopeName[location.reporting_scope]} · {location.operator || 'Publisher-identified entity'}. Values cover the reporting location named by the publisher.</p>{location.scope_note && <details className="disclosure-link"><summary>Reporting boundary</summary><p>{location.scope_note}</p></details>}
    {link && <><p className="disclosure-scope-note">{link.inventory_allocation ? 'Matched reporting boundary. These remain reported annual values, with the source’s methods and qualifications.' : 'Related location totals — no allocation to this individual record.'}</p><LinkEvidence link={link}/></>}
    <div className="disclosure-table-scroll" tabIndex={0} role="region" aria-label={`${location.location_label} annual disclosures`}><table className="disclosure-table"><caption>{location.location_label} · reported annual observations</caption><thead><tr><th scope="col">Measure and period</th><th scope="col">Reported value</th><th scope="col">Source and method</th></tr></thead><tbody>{location.observations.map(row => <Observation key={row.observation_id} row={row}/>)}</tbody></table></div>
    {location.observations.length === 0 && <p>No published numeric observation is available for the selected reporting period. A missing value is not zero.</p>}
    {!link && location.record_links.filter(l => l.verification_status === 'verified').map(l => <p key={l.record_id}><a href={publicHref(l.record_id)}>Open linked source record</a> · {l.inventory_allocation ? 'same reporting boundary' : 'related location; totals not allocated'}</p>)}
  </div>;
}
export function LinkedFacilityDisclosures({ groups }: {groups?: LinkedDisclosure[]}) {
  if (!groups?.length) return null;
  return <section className="facility-disclosures" aria-labelledby="facility-disclosures-title"><h2 id="facility-disclosures-title">Reported annual disclosures</h2>
    <p>Electricity use is annual energy, not peak demand. Water withdrawal and consumption differ. Emissions methods and reporting boundaries remain separate.</p>
    {groups.map(({location,link}) => <div key={location.disclosure_location_id}><h3>{location.location_label}</h3><LocationEvidence location={location} link={link}/></div>)}
  </section>;
}
export function DisclosureExplorer({ data }: {data: FacilityObservations}) {
  const [query,setQuery] = useState('');
  const [family,setFamily] = useState('all');
  const rows = data.locations.filter(l => `${l.location_label} ${l.operator || ''}`.toLowerCase().includes(query.trim().toLowerCase()))
    .filter(l => family === 'all' || l.observations.some(o => disclosedFamily(o.metric) === family));
  return <section className="disclosure-explorer" aria-labelledby="disclosure-explorer-title"><h2 id="disclosure-explorer-title">Reported electricity, water and emissions</h2>
    <p>{data.observation_count.toLocaleString()} annual observations across {data.locations.length.toLocaleString()} publisher-defined locations. Buildings, campuses and regulatory facilities have different boundaries. Reporting years appear beside every value.</p>
    <div className="disclosure-filters"><label>Find a reporting location<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Operator or location" type="search"/></label><label>Measure<select value={family} onChange={e=>setFamily(e.target.value)}><option value="all">All measures</option><option value="electricity">Electricity</option><option value="water">Water</option><option value="emissions">Emissions</option></select></label></div>
    <p role="status">{rows.length} reporting {rows.length === 1 ? 'location' : 'locations'} shown. Overlapping sources and aggregates must not be added together.</p>
    {rows.map(location => <details className="disclosure-result" key={location.disclosure_location_id}><summary><span>{location.location_label}</span><span>{location.operator || 'Operator not specified'} · {scopeName[location.reporting_scope]} · {[...new Set(location.observations.map(o=>o.data_year))].join(', ') || 'No numeric data'}</span></summary><LocationEvidence location={location}/></details>)}
    {rows.length === 0 && <p>No location matches these filters. Try another name or choose all measures.</p>}
  </section>;
}
