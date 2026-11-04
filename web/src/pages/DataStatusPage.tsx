import { useEffect, useState } from 'react';
import { getDataStatus, getPipelineStatus, getOperatorWater, getFacilityObservations } from '../api';
import type { DataStatus, PipelineStatus, OperatorWaterData, FacilityObservations } from '../types';
import { coverageLabel, dateLabel, observationLabel, safeSourceUrl, sourceState, SOURCE_STATE_LABEL } from '../dataPresentation';
import { OperatorWater } from '../components/OperatorWater';
import { DisclosureExplorer } from '../components/FacilityDisclosures';
import './data-pages.css';

const COVERAGE_LABELS: Record<string, string> = {
  facility_records:'Source records', regulator_verified:'Type supported by regulator identity', unverified_records:'Type not independently verified',
  with_coordinates:'Records carrying coordinates', map_eligible_records:'Records eligible for map display', suspect_location_records:'Locations needing review',
  candidate_address_groups:'Shared-address candidate groups', candidate_group_records:'Records in those candidate groups', with_grid_binding:'Records with historical grid context',
  permit_documents:'All listed permit documents', permit_documents_parsed:'All readable permit documents',
  virginia_permit_documents:'Virginia documents listed', virginia_permit_documents_parsed:'Virginia documents readable',
  texas_permit_documents:'Selected Texas documents listed', texas_permit_documents_parsed:'Selected Texas documents readable',
  records_with_permit_conditions:'Records with quoted conditions', records_with_generator_evidence:'Records with cited generator tables',
  actual_power_records:'Records with same-boundary electricity disclosures', actual_water_records:'Records with same-boundary water disclosures', actual_carbon_records:'Records with same-boundary emissions disclosures', disclosure_locations:'Publisher-defined reporting locations', disclosure_observations:'Reported annual observations', records_with_linked_disclosures:'Inventory records with reviewed disclosure links', reported_electricity_locations:'Locations reporting electricity', reported_water_locations:'Locations reporting water', reported_emissions_locations:'Locations reporting emissions', grid_authorities:'Grid authorities with observations',
};
const COVERAGE_GROUPS = [
  ['Inventory and identity', ['facility_records','regulator_verified','unverified_records','candidate_address_groups','candidate_group_records']],
  ['Location checks', ['with_coordinates','map_eligible_records','suspect_location_records','with_grid_binding']],
  ['Permit evidence', ['permit_documents','permit_documents_parsed','virginia_permit_documents','virginia_permit_documents_parsed','texas_permit_documents','texas_permit_documents_parsed','records_with_permit_conditions','records_with_generator_evidence']],
  ['Reported observations and scope', ['disclosure_locations','disclosure_observations','reported_electricity_locations','reported_water_locations','reported_emissions_locations','records_with_linked_disclosures','actual_power_records','actual_water_records','actual_carbon_records','grid_authorities']],
] as const;

export function DataStatusPage() {
  const [data, setData] = useState<DataStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [disclosures, setDisclosures] = useState<FacilityObservations | null>(null);
  const [disclosureError, setDisclosureError] = useState<string | null>(null);
  useEffect(() => { let active = true; getFacilityObservations().then(value => { if (active) setDisclosures(value); }).catch(() => { if (active) setDisclosureError('Reported disclosures could not be loaded. Reload to retry.'); }); return () => { active = false; }; }, []);
  const [water, setWater] = useState<OperatorWaterData | null>(null);
  const [waterError, setWaterError] = useState<string | null>(null);
  useEffect(() => { let active = true; getOperatorWater().then((value) => { if (active) setWater(value); }).catch(() => { if (active) setWaterError('Operator location water evidence is unavailable in this snapshot. Reload to retry.'); }); return () => { active = false; }; }, []);
  useEffect(() => { let active = true; getDataStatus().then((value) => { if (active) setData(value); }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Source status is unavailable.'); }); return () => { active = false; }; }, []);
  useEffect(() => {
    let active = true;
    const refresh = () => getPipelineStatus().then((value) => { if (active) { setPipeline(value); setHealthError(null); } }).catch(() => { if (active) setHealthError('Live collection status is unavailable. The published source dates below remain valid.'); });
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 60000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  return <main className="page data-page">
    <header className="data-page__header"><h1>Data coverage &amp; updates</h1><p>See where the records come from, what period they describe, and whether collection is up to date.</p></header>
    {error ? <div className="data-message" role="alert"><h2>Source status is unavailable</h2><p>{error}</p><button className="ghostbtn" onClick={() => window.location.reload()}>Reload data</button></div>
      : !data ? <p className="data-message" role="status">Loading source status…</p>
      : <>
        <div className="data-release"><span>WattDocket snapshot published {dateLabel(data.built_at, true)}</span><span>Release <code>{data.release_id}</code></span></div>
        {pipeline && <section className="pipeline-health" aria-label="Latest collection attempt"><h2>Latest collection attempt <span className="source-state" data-state={pipeline.status === 'failed' ? 'failed' : pipeline.status === 'degraded' ? 'stale' : 'current'}>{pipeline.status === 'degraded' ? 'Some sources incomplete' : pipeline.status === 'failed' ? 'Publication failed' : pipeline.status === 'running' ? 'Collection in progress' : 'Collection completed'}</span></h2><p>{dateLabel(pipeline.completed_at ?? pipeline.started_at, true)}. {pipeline.promoted ? 'A data release was published.' : 'The last successful data release remains available.'}</p>{pipeline.promoted && pipeline.release_id && pipeline.release_id !== data.release_id && <p>This page is using release {data.release_id}. Reload to use the latest published release.</p>}{pipeline.error && <p role="status">{pipeline.error}</p>}{(pipeline.sources ?? []).filter((source) => source.status === 'failed' || source.status === 'stale' || source.partial).map((source) => <div key={source.id} className="pipeline-health__source"><strong>{source.label || source.id}</strong><span>{source.partial ? `${source.documents_failed ?? 'Some'} linked documents could not be read` : SOURCE_STATE_LABEL[sourceState(source)]}. Last successful collection: {dateLabel(source.last_success_at ?? source.retrieved_at, true)}.</span>{source.error && <details><summary>Read collection error</summary><p>{source.error}</p></details>}</div>)}</section>}
        {healthError && <p className="data-message" role="status">{healthError}</p>}
        {disclosures && <DisclosureExplorer data={disclosures} />}
        {disclosureError && <p className="data-message" role="status">{disclosureError}</p>}
        {data.coverage && <section aria-label="Coverage and evidence quality"><p className="data-page__note">Counts describe the collected records. They are not a nationwide completeness estimate, verified distinct-site count, or proof that uncollected measurements do not exist. Shared addresses are review candidates, not confirmed duplicates.</p>{COVERAGE_GROUPS.map(([title, keys]) => <div className="coverage-group" key={title}><h2>{title}</h2><dl className="data-coverage-facts">{keys.filter((key) => typeof data.coverage?.[key] === 'number').map((key) => <div key={key}><dt>{(key === 'permit_documents' || key === 'permit_documents_parsed') && data.coverage?.virginia_permit_documents == null ? (key === 'permit_documents' ? 'Virginia documents listed (older release)' : 'Virginia documents readable (older release)') : COVERAGE_LABELS[key]}</dt><dd>{(data.coverage![key] as number).toLocaleString('en-US')}</dd></div>)}</dl></div>)}</section>}
        <p className="data-page__note">“Collection current” describes retrieval health within the source’s collection window; it does not verify the age, identity or current effectiveness of every underlying fact. The reporting period can be older: annual grid rates, permit documents and hourly observations update on different schedules.</p>
        {water && !disclosures && <OperatorWater data={water} />}
        {waterError && <p className="data-message" role="status">{waterError}</p>}
        <div className="source-list">{data.sources.map((source) => {
          const state = sourceState(source);
          const href = safeSourceUrl(source.url);
          return <section className="source-row" key={source.id}>
            <div className="source-row__title"><h2>{href ? <a href={href} target="_blank" rel="noreferrer">{source.label} <span aria-hidden="true">↗</span></a> : source.label}</h2><span className="source-state" data-state={state}>{SOURCE_STATE_LABEL[state]}</span></div>
            <dl className="source-row__facts"><div><dt>Reporting period</dt><dd>{observationLabel(source)}</dd></div><div><dt>Last successful collection</dt><dd>{dateLabel(source.last_success_at ?? source.retrieved_at, true)}</dd></div><div><dt>Records collected</dt><dd>{Number.isFinite(source.record_count) ? source.record_count.toLocaleString('en-US') : 'Not supplied'}</dd></div></dl>
            {coverageLabel(source.coverage) && <p>{coverageLabel(source.coverage)}</p>}
            {source.note && <p>{source.note}</p>}
            {source.error && <details><summary>Read failed attempt · {dateLabel(source.last_attempt_at, true)}</summary><p className="source-row__error">{source.error}</p></details>}
            {(source.published_at || source.stale_after_seconds != null) && <details><summary>Publication &amp; collection details</summary><dl><div><dt>Publisher release date</dt><dd>{dateLabel(source.published_at)}</dd></div><div><dt>Collection window</dt><dd>{source.stale_after_seconds == null ? 'Not supplied' : `${Math.round(source.stale_after_seconds / 3600 * 10) / 10} hours`}</dd></div><div><dt>Source identifier</dt><dd><code>{source.id}</code></dd></div></dl></details>}
          </section>;
        })}</div>
        {data.sources.length === 0 && <p className="data-message">This release does not contain a source report.</p>}
        {data.pipeline && <section className="data-pipeline"><h2>Publication pipeline</h2><p>{coverageLabel(data.pipeline) || 'Pipeline details are not included in this release.'}</p></section>}
        <section className="data-limitations"><h2>How to read the evidence</h2><ul><li>A facility listing identifies a public record. Linked annual disclosures have their own reviewed reporting boundaries; a campus total is not an individual building’s use.</li><li>Quoted permit conditions describe the cited document. Current effectiveness and supersession may be unverified; they do not report how equipment operated.</li><li>Reference scenarios are calculations with stated assumptions, separate from facility observations.</li><li>Missing values remain unavailable. A failed update keeps its last successful retrieval date.</li></ul></section>
      </>}
  </main>;
}
