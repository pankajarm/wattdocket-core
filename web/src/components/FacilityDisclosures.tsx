import { useEffect, useId, useMemo, useState } from 'react';
import type { DisclosureLocation, DisclosureRecordLink, FacilityObservations, LinkedDisclosure, ReportedObservation } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';
import { publicHref } from '../router';
import { disclosureSummary, filterDisclosureLocations, normalizeDisclosureFamily, reportedLocationHref, reportingBasisLabel, scopeName } from '../disclosureData';
import { ExplainPanel } from './ExplainPanel';

export { disclosedFamily } from '../disclosureData';

function Observation({ row }: {row: ReportedObservation}) {
  const url = safeSourceUrl(row.source_url);
  return <tr>
    <th scope="row">{row.label}<span>{row.data_year} · {reportingBasisLabel(row)}</span></th>
    <td>{row.value.toLocaleString('en-US', {maximumFractionDigits: 3})}<span>{row.unit}</span></td>
    <td>
      {url ? <a href={`${url}${typeof row.source_page === 'number' ? `#page=${row.source_page}` : ''}`} target="_blank" rel="noreferrer">{row.publisher}{typeof row.source_page === 'number' ? ` · p. ${row.source_page}` : ''}</a> : <span>{row.publisher} · source link unavailable</span>}
      <details><summary>Method and source details</summary>
        <p>{row.methodology_note}</p>
        <p>Reporting period: {dateLabel(row.period_start)} to {dateLabel(row.period_end)} (end exclusive).</p>
        <p>Retrieved {dateLabel(row.retrieved_at, true)}{row.source_status === 'stale' ? ' · retained after a delayed collection' : ''}.</p>
        <p>Source SHA-256: <code>{row.source_sha256}</code></p>
        {row.source_field && <p>Field: {row.source_field}</p>}
        {typeof row.source_page === 'string' && <p>Source locator: {row.source_page}</p>}
      </details>
    </td>
  </tr>;
}

function LinkEvidence({ link }: {link: DisclosureRecordLink}) {
  return <details className="disclosure-link"><summary>Why this record is linked</summary>
    <p>{link.relationship === 'same_physical_scope' ? 'The reviewed evidence supports the same reporting boundary.' : 'The reviewed evidence links this record to the reporting location. The location can include other buildings or records; its totals are not this record’s individual measurements.'}</p>
    <p>{link.reason || link.note}</p>
    {(link.evidence ?? []).map((e, index) => {
      const url = safeSourceUrl(e.url || e.source_url);
      return <p key={index}>{e.excerpt || e.quote || e.excerpts?.join(' ')} {url && <a href={url} target="_blank" rel="noreferrer">{e.title || e.publisher || 'Identity evidence'}</a>}</p>;
    })}
  </details>;
}

function LocationEvidence({ location, link, observations = location.observations, releaseId }: {location: DisclosureLocation; link?: DisclosureRecordLink; observations?: ReportedObservation[]; releaseId?: string}) {
  const subset = observations.length !== location.observations.length;
  return <div className="disclosure-location">
    {location.address && <p>Publisher address: {[location.address.street, location.address.city, location.address.state, location.address.postal_code].filter(Boolean).join(', ')}</p>}
    <p className="disclosure-boundary">{scopeName[location.reporting_scope]} · {location.operator || 'Publisher-identified entity'}. Values cover the reporting location named by the publisher.</p>
    {location.scope_note && <details className="disclosure-link"><summary>What this reporting boundary covers</summary><p>{location.scope_note}</p></details>}
    {link && <><p className="disclosure-scope-note">{link.inventory_allocation ? 'Matched reporting boundary. These remain reported annual values, with the source’s methods and qualifications.' : 'Related location totals — no allocation to this individual record.'}</p><LinkEvidence link={link}/></>}
    <div className="disclosure-table-scroll" tabIndex={0} role="region" aria-label={`${location.location_label} annual disclosures`}>
      <table className="disclosure-table"><caption>{location.location_label} · reported annual observations{subset ? ' matching your filters' : ''}</caption>
        <thead><tr><th scope="col">Measure and period</th><th scope="col">Reported value</th><th scope="col">Source and method</th></tr></thead>
        <tbody>{observations.map(row => <Observation key={row.observation_id} row={row}/>)}</tbody>
      </table>
    </div>
    {location.observations.length === 0 && <p>No numeric observation is available in our sources for the selected reporting period. A missing value is not zero.</p>}
    {!!location.missing_metrics?.length && <p>Missing or withheld: {[...new Set(location.missing_metrics.map(metric => metric === 'electricity_consumption' ? 'electricity' : metric.startsWith('water_') ? 'water' : 'emissions'))].join(', ')}. Some publisher values may fail completeness checks; missing values are not zero.</p>}
    {releaseId && <ExplainPanel releaseId={releaseId} subject={{kind: 'disclosure_location', id: location.disclosure_location_id}}
      title="Understand this reporting location" actionLabel="Explain this reporting location"
      template={location.observations.length
        ? `${location.location_label} has published annual disclosures with their own years, units and reporting boundary. A link to another record does not by itself allocate these totals.`
        : `${location.location_label} has no accepted numeric observations in the collected sources. Missing information is not zero.`}
      scopeNote="Explains all published annual observations for this reporting location. The table’s measure and year filters do not change the explanation."/>}
    <div className="disclosure-location-links">
      <a href={reportedLocationHref(location.disclosure_location_id)}>Link to this reporting location</a>
      {!link && location.record_links.filter(l => l.verification_status === 'verified').map(l => <p key={l.record_id}><a href={publicHref(l.record_id)}>Open linked source record{l.expected_record?.name ? `: ${l.expected_record.name}` : ''}</a> · {l.inventory_allocation ? 'same reporting boundary' : 'related location; totals not allocated'}</p>)}
    </div>
  </div>;
}

export function LinkedFacilityDisclosures({ groups, releaseId }: {groups?: LinkedDisclosure[]; releaseId?: string}) {
  if (!groups?.length) return null;
  return <section className="facility-disclosures" aria-labelledby="facility-disclosures-title">
    <h2 id="facility-disclosures-title">Reported annual disclosures</h2>
    <p>Electricity use is annual energy, not peak demand. Water withdrawal and consumption differ. Emissions methods and reporting boundaries remain separate.</p>
    {groups.map(({location, link}) => <div key={location.disclosure_location_id}><h3>{location.location_label}</h3><LocationEvidence location={location} link={link} releaseId={releaseId}/></div>)}
  </section>;
}

function DisclosureResult({ location, observations, initiallyOpen, releaseId }: {location: DisclosureLocation; observations: ReportedObservation[]; initiallyOpen: boolean; releaseId?: string}) {
  const [open, setOpen] = useState(initiallyOpen);
  useEffect(() => { setOpen(initiallyOpen); }, [initiallyOpen]);
  const years = [...new Set(observations.map(row => row.data_year))].sort((a, b) => b - a);
  return <details className="disclosure-result" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <svg className="disclosure-result__toggle" width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 8 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
      <span className="disclosure-result__name">{location.location_label}</span>
      <span className="disclosure-result__meta">{location.operator || 'Operator not specified'} · {scopeName[location.reporting_scope]} · {years.join(', ') || 'No numeric data'}</span>
    </summary>
    <LocationEvidence location={location} observations={observations} releaseId={releaseId}/>
  </details>;
}

interface ExplorerProps {
  data: FacilityObservations;
  advanced?: boolean;
  initialQuery?: string;
  initialFamily?: string;
  initialLocationId?: string;
}

export function DisclosureExplorer({ data, advanced = false, initialQuery = '', initialFamily = 'all', initialLocationId = '' }: ExplorerProps) {
  const [query, setQuery] = useState(initialQuery);
  const [family, setFamily] = useState(normalizeDisclosureFamily(initialFamily));
  const [year, setYear] = useState('all');
  const [publisher, setPublisher] = useState('all');
  const [scope, setScope] = useState('all');
  const [locationId, setLocationId] = useState(initialLocationId);
  const sectionId = useId();
  const summary = useMemo(() => disclosureSummary(data), [data]);
  useEffect(() => {
    setQuery(initialQuery); setFamily(normalizeDisclosureFamily(initialFamily)); setLocationId(initialLocationId);
    setYear('all'); setPublisher('all'); setScope('all');
  }, [initialQuery, initialFamily, initialLocationId]);
  const rows = useMemo(() => filterDisclosureLocations(data.locations, {query, family, year, publisher, scope, locationId}), [data, query, family, year, publisher, scope, locationId]);
  const filteredValues = rows.reduce((count, row) => count + row.observations.length, 0);
  const activeFilters = Boolean(query || family !== 'all' || year !== 'all' || publisher !== 'all' || scope !== 'all' || locationId);
  const reset = () => { setQuery(''); setFamily('all'); setYear('all'); setPublisher('all'); setScope('all'); setLocationId(''); };
  const selectedLocation = locationId ? data.locations.find(location => location.disclosure_location_id === locationId) : null;
  return <section className={`disclosure-explorer${advanced ? ' disclosure-explorer--advanced' : ''}`} aria-labelledby={sectionId}>
    <div className="disclosure-introduction">
      <h2 id={sectionId}>{advanced ? 'Find reported values' : 'Reported electricity, water and emissions'}</h2>
      <p><strong>{summary.observations.toLocaleString()} annual values</strong> across {summary.populated.toLocaleString()} reporting locations with numeric data. Our collection has {summary.locations.toLocaleString()} reporting entries{summary.missing > 0 ? `, including ${summary.missing} with no numeric observations` : ''}.</p>
      <p>Buildings, campuses and regulatory facilities have different boundaries. These sources include international locations and do not form a complete census.</p>
      {advanced && <p className="disclosure-coverage-line">Electricity: {summary.electricity} locations <span aria-hidden="true">·</span> Water: {summary.water} <span aria-hidden="true">·</span> Emissions: {summary.emissions}. Categories overlap.</p>}
    </div>
    <div className="disclosure-workspace">
      <div className="disclosure-filters">
        <label>Find a reporting location<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Operator, place or publisher" type="search"/></label>
        <label>Measure<select value={family} onChange={event => setFamily(normalizeDisclosureFamily(event.target.value))}><option value="all">All measures</option><option value="electricity">Electricity</option><option value="water">Water</option><option value="emissions">Emissions</option></select></label>
        {advanced && <>
          <label>Reporting year<select value={year} onChange={event => setYear(event.target.value)}><option value="all">All years</option>{summary.years.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <label>Publisher<select value={publisher} onChange={event => setPublisher(event.target.value)}><option value="all">All publishers</option>{summary.publishers.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <label>Reporting boundary<select value={scope} onChange={event => setScope(event.target.value)}><option value="all">All boundaries</option>{summary.scopes.map(value => <option key={value} value={value}>{scopeName[value]}</option>)}</select></label>
        </>}
        {activeFilters && <button className="disclosure-reset" type="button" onClick={reset}>Clear all filters</button>}
      </div>
      <div className="disclosure-results">
        {locationId && <p className="disclosure-location-filter">Showing {selectedLocation?.location_label || 'the linked reporting location'}. <a href="#/reported">Browse all locations</a></p>}
        <p className="disclosure-results-status" role="status">{rows.length} reporting {rows.length === 1 ? 'location' : 'locations'} shown · {filteredValues} annual {filteredValues === 1 ? 'value' : 'values'} matching your filters.</p>
        {advanced && <p className="disclosure-comparison-note">Compare the year, unit and reporting boundary before comparing numbers. Related records and overlapping emissions methods must not be added together.</p>}
        {rows.map(({location, observations}) => <DisclosureResult key={location.disclosure_location_id} location={location} observations={observations} initiallyOpen={location.disclosure_location_id === locationId} releaseId={data.release_id}/>)}
        {rows.length === 0 && <div className="disclosure-empty"><h3>No location matches these filters.</h3><p>Try another name or choose all measures. An empty result means no match in our collection; it does not establish that no data center or public report exists.</p><button className="disclosure-reset" type="button" onClick={reset}>Clear all filters</button></div>}
      </div>
    </div>
  </section>;
}
