import { LinkedFacilityDisclosures } from '../components/FacilityDisclosures';
import { useEffect, useState } from 'react';
import type { OperatorPayload, ScenarioInputs } from '../types';
import { getOperatorPayload } from '../api';
import { publicHref } from '../router';
import { ReferenceCalculator } from '../components/ReferenceCalculator';
import { calculateScenario, defaultScenario } from '../scenario';
import { MethodBars } from '../components/MethodBars';
import { ComplianceStrip } from '../components/ComplianceStrip';
import { LeverList } from '../components/LeverList';
import { MatchedHoursHeatmap } from '../components/MatchedHoursHeatmap';
import { ExportBlock } from '../components/ExportBlock';
import { PermitEnvelope } from '../components/PermitEnvelope';
import { DataProvenance } from '../components/DataProvenance';
import { dateLabel } from '../dataPresentation';
import './operator-bench.css';

type Load = { status: 'loading' } | { status: 'ready'; payload: OperatorPayload; fixture: boolean } | { status: 'error'; message: string };

export function OperatorBenchPage({ facilityId }: { facilityId: string }) {
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [constraints, setConstraints] = useState(false);
  const [calculatorInputs, setCalculatorInputs] = useState<ScenarioInputs | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoad({ status: 'loading' });
    setCalculatorInputs(null);
    getOperatorPayload(facilityId).then((res) => {
      if (!cancelled) {
        setLoad({ status: 'ready', payload: res.data, fixture: res.source === 'fixture' });
        const first = res.data.levers.find((lever) => lever.tonnes > 0);
        setSelected(new Set(first ? [first.rank] : []));
      }
    }).catch((err: unknown) => { if (!cancelled) setLoad({ status: 'error', message: err instanceof Error ? err.message : String(err) }); });
    return () => { cancelled = true; };
  }, [facilityId]);

  if (load.status === 'loading') return <main className="page page--operator"><div className="skel" role="status" aria-label="Loading operator bench"><div className="skel__line skel__line--h1" /><div className="skel__chips">{[0, 1, 2].map((i) => <div className="skel__chip" key={i} />)}</div></div></main>;
  if (load.status === 'error') return <main className="page page--operator"><div className="state"><h1 className="state__h">The operator bench is unavailable.</h1><p className="state__p">We could not load a usable scenario for this facility. The service or underlying record may be unavailable. Try again or return to the public card.</p><p className="state__p state__p--mono" title={load.message}>Scenario unavailable</p><div className="state__links"><button className="ghostbtn" onClick={() => window.location.reload()}>Try again</button><a className="ghostbtn" href={publicHref(facilityId)}>← Public card</a></div></div></main>;

  const { payload: original, fixture } = load;
  const inputs = calculatorInputs ?? original.scenario_inputs ?? original.method?.inputs ?? defaultScenario(original.method?.data_vintage);
  const calculation = calculateScenario(inputs, original.method?.rate_lb_per_mwh);
  const production = Boolean(original.release_id);
  const payload: OperatorPayload = production ? { ...original, scope:'scenario', scenario_inputs:inputs,
    methods: original.methods.map((method) => method.key === 'location' ? { ...method, inputs, scope:'scenario', label:'User-input historical grid scenario', tonnes:'error' in calculation ? null : calculation.carbon_t.base, note:`Hypothetical grid-side load ${inputs.load_mw} MW, ${inputs.utilization.base * 100}% base utilization, ${inputs.factor_year || original.method?.data_vintage || 'historical'} annual regional factor. This is not measured facility emissions.` } : method),
    peak: { ...original.peak, label:'Calculator grid-side load', value:String(inputs.load_mw), sub:'User-controlled scenario input; no facility operating measurement.' },
    method: original.method ? { ...original.method, inputs, scope:'scenario', calc_id:'interactive-grid-scenario-v1', chain:[`${inputs.load_mw} MW grid-side load × 8760 hours × ${inputs.utilization.base * 100}% utilization`, `Annual MWh × ${original.method.rate_lb_per_mwh} lb CO2e/MWh ÷ 2204.622621848776 lb/tonne`, 'Low/base/high results use the explicit user inputs; they are not percentiles.'] } : undefined,
  } : original;
  const base = payload.methods.find((method) => method.key === 'location')?.tonnes ?? null;
  const shift = payload.levers.find((lever) => lever.tonnes > 0);
  const unquantified = payload.levers.filter((lever) => lever.tonnes <= 0);
  const withheld = payload.methods.filter((method) => method.tonnes == null || !Number.isFinite(method.tonnes) || /^withheld\b/i.test(method.note.trim())).length;
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  return <main className="page page--operator operator-workspace">
    <div className="operator-breadcrumb"><a href={publicHref(payload.facility_id)}>← Public card</a><span aria-hidden="true">/</span><span>Operator bench</span></div>
    {(payload.synthetic_meter_data || fixture) && <span className="demo">{payload.synthetic_meter_data ? 'SYNTHETIC METER DATA' : 'FIXTURE DATA'}</span>}
    <header className="operator-intro"><div><h1>{payload.facility_label}</h1><p>Public evidence workspace <span aria-hidden="true">·</span> Snapshot published {dateLabel(payload.data_status?.built_at ?? payload.as_of)}</p></div><button className="operator-intro__action" type="button" onClick={() => scrollTo('operator-export')}>Export evidence <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 4v16m-6-6 6 6 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></button></header>
    <DataProvenance data={payload.data_status} />
    <LinkedFacilityDisclosures groups={payload.disclosures} />
    <div className="operator-notice"><strong>A reference, not a facility measurement.</strong><span>{payload.synthetic_meter_data ? 'This scenario includes synthetic meter data for illustration. It is not a facility operating record.' : 'This bench uses public records and a reference load. Any linked annual disclosures appear above with their reporting scope. Hourly meter data and procurement are not included.'}</span></div>
    <nav className="operator-tabs" aria-label="On this page"><button type="button" onClick={() => scrollTo('operator-accounting')}>Grid calculator</button><button type="button" onClick={() => scrollTo('operator-records')}>Operating evidence</button><button type="button" onClick={() => scrollTo('operator-options')}>Additional inputs</button><button type="button" onClick={() => scrollTo('operator-evidence')}>Method &amp; evidence</button></nav>
    <div className="operator-layout"><div className="operator-main">
      <section id="operator-accounting" className="operator-section"><div className="operator-section__head"><div><h2>Historical grid calculator</h2><p>Illustrative annual energy and emissions from your inputs. Source records do not establish this facility’s operating load.</p></div><span className="operator-status">{withheld} of {payload.methods.length} methods withheld</span></div>{production ? <ReferenceCalculator rate={payload.method?.rate_lb_per_mwh ?? null} year={payload.method?.data_vintage} region={payload.method?.subregion_name || payload.method?.subregion || 'the selected region'} sourceUrl={payload.method?.source_url} evidenceUrl={payload.method?.evidence_url} value={inputs} onChange={setCalculatorInputs} /> : <MethodBars methods={payload.methods} note={payload.matching_note} />}</section>
      <section id="operator-records" className="operator-section"><div className="operator-section__head"><div><h2>Available operating evidence</h2><p>Permit limits and reference sizing are not operating measurements.</p></div></div><ComplianceStrip items={payload.compliance} peak={payload.peak} /><PermitEnvelope payload={payload} /><MatchedHoursHeatmap months={payload.heatmap.months} hours={payload.heatmap.hours} cells={payload.heatmap.cells} note={payload.heatmap.note} /></section>
      <section id="operator-options" className="operator-section"><div className="operator-section__head"><div><h2>Inputs needed for further analysis</h2><p>Facility load, procurement and operating records are needed before a reduction comparison can be quantified.</p></div></div><LeverList levers={payload.levers} baseTonnes={base} selected={selected} onToggle={(rank) => setSelected((prev) => { const next = new Set(prev); if (next.has(rank)) next.delete(rank); else next.add(rank); return next; })} />
        <button type="button" className="operator-disclosure" aria-expanded={constraints} aria-controls="scenario-assumptions" onClick={() => setConstraints((v) => !v)}>{constraints ? 'Hide scenario assumptions' : 'View scenario assumptions'} <span aria-hidden="true">{constraints ? '−' : '+'}</span></button>
        {constraints && <div className="scenario" id="scenario-assumptions">{shift ? <div className="scenario__row"><span>{shift.title}</span><b>{shift.tonnes.toLocaleString('en-US')} t estimated avoided</b></div> : <p className="scenario__none">No option for this facility could be quantified from the available public data.</p>}{unquantified.length > 0 && <div className="scenario__held"><span className="scenario__held-k">NOT QUANTIFIED</span><ul>{unquantified.map((lever) => <li key={lever.rank}><b>{lever.title}</b> — {lever.detail}</li>)}</ul></div>}{shift && <div className="scenario__out"><span>This published comparison uses the available grid-intensity snapshot and a reference load. A different shift needs a new calculation and an actual load profile.</span></div>}</div>}
      </section>
    </div><aside id="operator-evidence" className="operator-aside"><h2>Method &amp; evidence</h2><p>Export the displayed calculator inputs and results together with the public source evidence and its dates.</p>
      {payload.method && <dl className="operator-facts"><div><dt>Grid region</dt><dd>{payload.method.subregion_name || payload.method.subregion || 'Not available'}{payload.method.subregion_name && payload.method.subregion ? ` (${payload.method.subregion})` : ''}</dd></div><div><dt>Grid factor</dt><dd>{payload.method.rate_lb_per_mwh != null ? `${payload.method.rate_lb_per_mwh.toLocaleString('en-US', { maximumFractionDigits: 3 })} lb CO₂e/MWh` : 'Not available'}</dd></div><div><dt>Factor set</dt><dd>{payload.method.factor_set_id || 'Not available'}</dd></div></dl>}
      {payload.method?.chain && payload.method.chain.length > 0 && <details className="operator-method"><summary>View calculation</summary><ol>{payload.method.chain.map((step, index) => <li key={index}>{step}</li>)}</ol></details>}
      <div id="operator-export"><ExportBlock payload={payload} fixture={fixture} disabled={production && 'error' in calculation} /></div><p className="operator-aside__foot">{payload.footer}</p>
    </aside></div>
  </main>;
}
