import { useEffect, useState } from 'react';
import { getFacilityPower, getPowerReadings } from '../api';
import { dateLabel } from '../dataPresentation';
import { powerPaths, powerWindow } from '../facilityPowerData';
import type { FacilityPower as PowerData, PowerSeries, PowerReadings, PowerSite } from '../facilityPowerData';
import './facility-power.css';
import { ExplainPanel } from './ExplainPanel';

const number = (n: number) => n.toLocaleString('en-US', {maximumFractionDigits: 2});
const address = (site?: PowerSite) => site ? [site.address.street, site.address.city, [site.address.state, site.address.postal_code].filter(Boolean).join(' '), site.address.country].filter(Boolean).join(', ') : '';
const siteName = (site?: PowerSite) => site?.operator && !site.name.includes(site.operator) ? `${site.operator} · ${site.name}` : site?.name;

function SiteProof({site}: {site?: PowerSite}) {
  if (!site) return null;
  return <details className="power-proof"><summary>Verify the reporting site</summary>
    <p>{site.reporting_boundary}</p>
    {site.identity_evidence.map((proof, i) => <p key={i}><a href={proof.url} target="_blank" rel="noreferrer">Identity source {i + 1}</a> · {proof.locator}<br/>{proof.quote || Object.entries(proof.source_fields || {}).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>)}
    <p>This reporting site has not been allocated to individual EPA or OpenStreetMap records.</p>
  </details>;
}

function MeterChart({series, releaseId}: {series: PowerSeries; releaseId: string}) {
  const [data, setData] = useState<PowerReadings | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [endDay, setEndDay] = useState(series.observed_end.slice(0, 10));
  async function load() {
    setStatus('loading');
    try { setData(await getPowerReadings(series.readings_path)); setStatus('ready'); }
    catch { setStatus('error'); }
  }
  const rows = data ? powerWindow(data.readings, endDay) : [];
  const values = rows.flatMap(r => r[1] === null ? [] : [r[1]]);
  const paths = powerPaths(rows, 880, 220);
  return <div className="power-chart">
    {(status === 'idle' || status === 'error') && <>
      {status === 'error' && <p role="alert">These readings could not be loaded. Try again or open the publisher’s dataset.</p>}
      <button type="button" onClick={load}>{status === 'error' ? 'Retry readings' : 'Explore hourly readings'}</button>
    </>}
    {status === 'loading' && <p role="status">Loading the published meter history…</p>}
    {status === 'ready' && <>
      <label className="power-date">Week ending (UTC)<input type="date" value={endDay} min={series.observed_start.slice(0, 10)} max={series.observed_end.slice(0, 10)} onChange={e => setEndDay(e.target.value)}/></label>
      <p className="power-window-description" aria-live="polite">{values.length ? `${number(values.length)} available readings · ${number(Math.min(...values))}–${number(Math.max(...values))} kW in this window.` : 'No numeric readings in this window.'} Missing readings remain gaps.</p>
      {values.length > 0 && <figure>
        <div className="power-axis"><span>{number(Math.max(...values))} kW</span><span>0 kW</span></div>
        <svg viewBox="-4 -8 888 236" role="img" aria-label={`Historical reported power in kW for the seven-day window ending ${endDay}. Missing readings are gaps. The vertical axis begins at zero.`}>
          <line x1="0" y1="220" x2="880" y2="220" className="power-baseline"/>
          {paths.map((d, i) => <path key={i} d={d} className="power-line"/>)}
        </svg>
        <figcaption><span>{rows[0]?.[0].slice(0, 10)}</span><span>{rows[rows.length - 1]?.[0].slice(0, 10)} · UTC</span></figcaption>
      </figure>}
      <details className="power-readings"><summary>Read the last 12 entries in this window</summary>
        <table><caption>{series.measurement_basis === 'derived_hourly_arithmetic_mean_of_metered_samples' ? 'UTC hour bins; arithmetic means of metered IT samples (kW)' : 'UTC timestamps; publisher values in kW'}</caption><thead><tr><th scope="col">Time</th><th scope="col">Power</th></tr></thead><tbody>{rows.slice(-12).map(row => <tr key={row[0]}><th scope="row">{row[0].replace('T', ' ').replace('Z', '')}</th><td>{row[1] === null ? 'Missing' : `${number(row[1])} kW`}</td></tr>)}</tbody></table>
      </details>
      <a href={`/data/releases/${releaseId}/${series.readings_path}`} target="_blank" rel="noreferrer">Open all published readings (JSON)</a>
    </>}
  </div>;
}

export function FacilityPower() {
  const [data, setData] = useState<PowerData | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  useEffect(() => { let active = true; getFacilityPower().then(value => {if (active) {setData(value); setStatus('ready');}}).catch(() => {if (active) setStatus('error');}); return () => {active = false;}; }, []);
  if (status === 'loading') return <p role="status">Loading facility power disclosures…</p>;
  if (status === 'error') return <div role="alert"><p>Facility power data could not be loaded.</p><button type="button" onClick={() => window.location.reload()}>Reload data</button></div>;
  if (!data) return <p>Facility power data is not available in this release. <a href="#/data">Review source coverage.</a></p>;
  const sites = new Map(data.sites.map(s => [s.site_id, s]));
  return <div className="facility-power">
    <section aria-labelledby="power-demand-title"><h2 id="power-demand-title">Reported peak demand</h2>
      <p>These reports describe the highest demand during their reporting year. Meter intervals and site boundaries vary; they are shown with each value.</p>
      {data.demands.length === 0 && <p>No verified peak-demand disclosures are available in this release.</p>}
      {data.demands.map(demand => <article key={demand.demand_id} className="power-entry">
        <div className="power-entry-heading"><div><h3>{siteName(sites.get(demand.site_id))}</h3><p>{address(sites.get(demand.site_id))}</p></div><p className="power-value">{number(demand.value)} {demand.unit}<span>{demand.reporting_year} reported maximum</span></p></div>
        {sites.get(demand.site_id)?.facility_type === 'cryptocurrency_mining' && <p>Cryptocurrency-mining facility (ASIC equipment)</p>}
        <p>{demand.whole_facility ? 'Reporting-campus maximum. The report does not specify the measurement interval.' : 'Largest individual meter entry. Complete building demand is not established.'}</p>
        <details className="power-proof"><summary>Read the measurement scope and source</summary><p>{demand.note}</p><p>{demand.source_locator}</p><p>Captured {dateLabel(demand.retrieved_at)} · Source checked {dateLabel(demand.checked_at)} · Collection status: {demand.collection_status}.</p></details>
        <p className="power-provenance"><a href={demand.source_url} target="_blank" rel="noreferrer">Read the original report</a></p>
        <SiteProof site={sites.get(demand.site_id)}/>
        <ExplainPanel releaseId={data.release_id} subject={{kind: 'power_site', id: demand.site_id}}
          title="Understand this power disclosure" actionLabel="Explain this power dataset"
          template="This disclosure reports a dated maximum demand. Its reporting boundary and measurement interval determine what can be compared. It does not establish a live operating load."
          scopeNote="Explains the published power evidence for this reporting site, including the reporting year, unit and measurement boundary."/>
      </article>)}
    </section>
    <section aria-labelledby="power-hourly-title"><h2 id="power-hourly-title">Historical hourly power</h2>
      <p>Explore dated research-facility meter data. These archives do not provide live readings. The meter boundary is stated for each series.</p>
      {data.series.map(series => <article key={series.series_id} className="power-entry">
        <h3>{sites.get(series.site_id)?.name} · {series.label}</h3><p>{address(sites.get(series.site_id))}</p>
        <p className="power-period">{dateLabel(series.observed_start)}–{dateLabel(series.observed_end)} (UTC) · {number(series.measured_count)} numeric hourly entries · {number(series.missing_count)} missing</p>
        <p>{series.measurement_boundary}</p>
        <MeterChart series={series} releaseId={data.release_id}/>
        <div className="power-provenance"><a href={series.source_url} target="_blank" rel="noreferrer">Publisher’s original dataset</a><p>Captured {dateLabel(series.retrieved_at)} · Source checked {dateLabel(series.checked_at)} · Collection status: {series.collection_status}. Capture dates do not change the measurement period.</p></div>
        <SiteProof site={sites.get(series.site_id)}/>
        <ExplainPanel releaseId={data.release_id} subject={{kind: 'power_site', id: series.site_id}}
          title="Understand this historical power dataset" actionLabel="Explain this power dataset"
          template="This historical power dataset preserves its measurement period, meter boundary and missing readings. It does not provide live readings or establish a whole-campus footprint."
          scopeNote="Explains this site’s published power dataset and measurement boundary. The selected chart week does not change the explanation."/>
        <details className="power-proof"><summary>Measurement method, time basis and reuse terms</summary><p>{series.methodology_note}</p><p>{series.time_basis}</p><p>{series.source_locator}</p><p className="power-license">{typeof series.license === 'string' ? series.license : [series.license.name, series.license.attribution, series.license.notice].filter(v => typeof v === 'string').join('\n\n')}</p></details>
      </article>)}
      {data.series.length === 0 && <p>No verified hourly series are available in this release.</p>}
    </section>
    <footer className="reported-page__provenance"><p>Published {dateLabel(data.built_at, true)}. {data.coverage.power_reporting_sites} verified reporting sites; no live facility meter feeds. Values are not assigned to other buildings or campuses.</p><a href="#/data">Review collection status and coverage</a></footer>
  </div>;
}
