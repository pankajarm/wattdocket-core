import { useEffect, useState } from 'react';
import { getFacilityObservations } from '../api';
import type { FacilityObservations } from '../types';
import { DisclosureExplorer } from '../components/FacilityDisclosures';
import { dateLabel } from '../dataPresentation';
import './reported-data.css';

interface Props { query?: string; family?: string; locationId?: string; }
type State = { status: 'loading' } | { status: 'ready'; data: FacilityObservations | null } | { status: 'error' };

export function ReportedDataPage({ query = '', family = 'all', locationId = '' }: Props) {
  const [state, setState] = useState<State>({status: 'loading'});
  useEffect(() => {
    let active = true;
    setState({status: 'loading'});
    getFacilityObservations().then(data => { if (active) setState({status: 'ready', data}); })
      .catch(() => { if (active) setState({status: 'error'}); });
    return () => { active = false; };
  }, []);
  return <main className="page reported-page">
    <header className="reported-page__header">
      <div><h1>Reported electricity, water &amp; emissions</h1><p>Read what companies and public agencies report for named campuses, buildings and other reporting locations. Every value keeps its year, unit and original source.</p></div>
      <aside aria-label="Understanding reported data"><h2>Start with the boundary.</h2><p>A campus can contain many buildings. Its annual total is not a reading for each building, and reported values can include estimates.</p><a href="#/learn/electricity">Understand electricity figures</a><a href="#/learn/water">Understand water figures</a><a href="#/learn/emissions">Understand emissions accounting</a></aside>
    </header>
    {state.status === 'loading' && <div className="reported-page__state" role="status"><p>Loading reported annual data and its source references…</p></div>}
    {state.status === 'error' && <div className="reported-page__state" role="alert"><h2>Reported data could not be loaded.</h2><p>Reload to try the published dataset again. Source coverage is available separately.</p><button type="button" onClick={() => window.location.reload()}>Reload data</button><a href="#/data">Check data coverage</a></div>}
    {state.status === 'ready' && !state.data && <div className="reported-page__state"><h2>No annual disclosure collection is available in this release.</h2><p>This describes the selected WattDocket release, not whether companies publish data elsewhere.</p><a href="#/data">Review this release’s sources</a></div>}
    {state.status === 'ready' && state.data && <>
      <DisclosureExplorer data={state.data} advanced initialQuery={query} initialFamily={family} initialLocationId={locationId}/>
      <footer className="reported-page__provenance"><p>WattDocket snapshot published {dateLabel(state.data.built_at, true)}. The reporting year beside each value describes when the activity occurred.</p><a href="#/data">Data coverage, sources and collection status</a></footer>
    </>}
  </main>;
}
