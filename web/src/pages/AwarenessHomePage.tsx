import { useEffect, useState } from 'react';
import { getFacilityObservations, getDataStatus } from '../api';
import { dateLabel } from '../dataPresentation';
import { selectReportedExample } from '../learning';
import type { FacilityObservations, DataStatus } from '../types';

function Arrow() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 12h15M13 5l7 7-7 7"/></svg>;
}
const questions = [
  {topic:'electricity', title:'How much electricity do data centers use?', note:'Read annual reports. Understand energy and demand.'},
  {topic:'water', title:'What do the water numbers mean?', note:'Follow withdrawal, discharge and consumption.'},
  {topic:'emissions', title:'How can one campus have two emissions figures?', note:'Understand what each accounting method tells us.'},
];
export function AwarenessHomePage() {
  const [observations, setObservations] = useState<FacilityObservations | null>(null);
  const [coverage, setCoverage] = useState<DataStatus | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    getFacilityObservations().then(value => { if (active) { setObservations(value); setReady(true); } })
      .catch(() => { if (active) { setError(true); setReady(true); } });
    getDataStatus().then(value => { if (active) setCoverage(value); }).catch(() => {});
    return () => { active = false; };
  }, []);
  const locations = observations?.locations ?? [];
  const populated = locations.filter(l => l.observations.length > 0);
  const count = (family: string) => locations.filter(l => l.observations.some(o => (o.metric === 'electricity_consumption' ? 'electricity' : o.metric.startsWith('water_') ? 'water' : 'emissions') === family)).length;
  const example = selectReportedExample(observations, 'meta:altoona-ia', ['electricity_consumption']);
  const altoona = example?.location;
  const electricity = example?.rows[0];
  const years = [...new Set(locations.flatMap(l => l.observations.map(o => o.data_year)))].sort();
  return <main id="main-content" className="awareness-home">
    <section className="awareness-intro" aria-labelledby="awareness-title">
      <div className="awareness-intro__copy">
        <h1 id="awareness-title">Understand data centers through <em>public evidence.</em></h1>
        <p>Explore reported electricity and water use, permit documents, and regional grid conditions. See who reported each figure, what place and year it covers, and what remains unknown.</p>
        <div className="awareness-actions"><a className="awareness-button" href="#/reported">Explore reported data <Arrow/></a><a className="awareness-link" href="#/map">Find public records <Arrow/></a></div>
      </div>
      <div className="awareness-questions">
        <h2>Start with a question.</h2>
        {questions.map(q => <a className="awareness-question" href={`#/learn/${q.topic}`} key={q.topic}><span><strong>{q.title}</strong><small>{q.note}</small></span><Arrow/></a>)}
        <a className="awareness-all" href="#/learn">Explore all questions <Arrow/></a>
      </div>
    </section>

    <section className="awareness-reports" aria-labelledby="reports-title">
      <div className="awareness-section-heading">
        <div><h2 id="reports-title">A clearer view of reported use.</h2><p>Some companies and public agencies publish annual figures. We bring those records together, with their original scope and source.</p></div>
        <a className="awareness-link" href="#/reported">Browse the reports <Arrow/></a>
      </div>
      {error ? <div className="awareness-status" role="alert"><p>Reported data could not be loaded. The explainers and original source links remain available.</p><button className="ghostbtn" onClick={() => window.location.reload()}>Reload data</button></div>
        : !ready ? <p className="awareness-status" role="status">Loading the published reporting coverage…</p>
        : !observations ? <p className="awareness-status">Annual disclosures are not available in this data release. <a href="#/data">Check source coverage</a>.</p>
        : <div className="awareness-report-layout">
          <div className="awareness-report-index">
            <a href="#/reported?family=electricity"><span>Electricity</span><span>{count('electricity')} reporting locations</span><Arrow/></a>
            <a href="#/reported?family=water"><span>Water</span><span>{count('water')} reporting locations</span><Arrow/></a>
            <a href="#/reported?family=emissions"><span>Emissions</span><span>{count('emissions')} reporting locations</span><Arrow/></a>
            <p>{observations.observation_count.toLocaleString()} annual values across {populated.length} entries with numbers. {locations.length - populated.length > 0 ? `${locations.length - populated.length} additional entries have no numeric observation. ` : ''}Categories overlap; coverage includes international locations.</p>
            <p>Reporting years: {years.join(', ')}. These are selected reports, not an industry census.</p>
          </div>
          <div className="awareness-example">
            <h3>A campus total has a boundary.</h3>
            {electricity && altoona ? <>
              <p>{electricity.publisher} reports <strong>{electricity.value.toLocaleString('en-US')} {electricity.unit}</strong> of electricity for its {altoona.location_label} campus in {electricity.data_year}.</p>
              <p>The figure describes the reporting campus. It does not tell us the demand at a particular hour or the use of each building.</p>
              <a className="awareness-link" href={`#/reported?location=${encodeURIComponent(altoona.disclosure_location_id)}`}>Read this disclosure <Arrow/></a>
            </> : <><p>A campus can include several buildings. Its reported total cannot automatically be assigned to one building or map record.</p><a href="#/learn/electricity">Learn how to read electricity reports</a></>}
          </div>
        </div>}
    </section>

    <section className="awareness-local" aria-labelledby="local-title">
      <div><h2 id="local-title">What can we learn about a place?</h2><p>A public record is a starting point. Follow it to a permit, a related campus disclosure, or the surrounding grid.</p><a className="awareness-button" href="#/map">Explore the records map <Arrow/></a></div>
      <div className="awareness-local__notes">
        <div><h3>A map with its limits in view.</h3><p>{coverage?.coverage && typeof coverage.coverage.map_eligible_records === 'number' ? `${coverage.coverage.map_eligible_records.toLocaleString()} reported points qualify for approximate map placement in this release. ` : ''}Some records describe the same place, and facility type is unverified for many entries. An empty map does not establish that no data center exists.</p></div>
        <a href="#/learn/virginia"><span><strong>Start a local investigation</strong><small>A Virginia research guide: identity, permits, utilities and the evidence still needed.</small></span><Arrow/></a>
      </div>
    </section>

    <section className="awareness-community" aria-labelledby="community-title">
      <div><h2 id="community-title">The questions beyond the numbers.</h2><p>Electricity bills, water supply, generators and local benefits matter. Each needs its own evidence.</p></div>
      <div className="awareness-community__links">
        <a href="#/learn/bills"><span>Who pays for new power infrastructure?</span><Arrow/></a>
        <a href="#/learn/generators"><span>What can a generator permit tell us?</span><Arrow/></a>
        <a href="#/grid"><span>What is happening on the regional grid?</span><Arrow/></a>
        <a href="#/learn/community"><span>What changes for the community?</span><Arrow/></a>
      </div>
    </section>

    <section className="awareness-method" aria-labelledby="method-title">
      <h2 id="method-title">Know what kind of evidence you are reading.</h2>
      <dl><div><dt>Reported</dt><dd>A publisher's annual figure, with its method and reporting boundary.</dd></div><div><dt>Quoted from a document</dt><dd>What a particular record says. Its current legal effect may be unknown.</dd></div><div><dt>Regional context</dt><dd>Information about an electricity region, not an individual facility.</dd></div><div><dt>Illustrative calculation</dt><dd>A scenario using stated assumptions. It is not an observation.</dd></div></dl>
      <div className="awareness-method__foot"><a className="awareness-link" href="#/data">See coverage and sources <Arrow/></a>{observations && <span>WattDocket snapshot published {dateLabel(observations.built_at)}. Reporting years stay attached to every figure.</span>}</div>
    </section>
  </main>;
}

