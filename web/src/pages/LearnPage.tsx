import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getFacilityObservations } from '../api';
import { dateLabel, safeSourceUrl } from '../dataPresentation';
import { learningTopics, selectReportedExample, waterShares } from '../learning';
import type { LearningTopic, ReportedExample } from '../learning';
import type { FacilityObservations, ReportedObservation } from '../types';
import './learn.css';

const sources = {
  lbnl: 'https://www.energy.gov/documents/united-states-data-center-energy-usage-report-2025-update',
  ghg: 'https://ghgprotocol.org/scope-2-guidance',
  aep: 'https://www.aepohio.com/company/about/rates/data-center-tariff/',
  scc: 'https://rga.lis.virginia.gov/Published/2026/RD637/PDF',
  nc: 'https://www.deq.nc.gov/news/press-releases/2026/08/28/ncdeqs-division-air-quality-requires-additional-monitoring-testing-final-air-quality-permits-amazon',
  loudoun: 'https://www.loudoun.gov/6408/Data-Centers-The-Loudoun-Story',
  zoning: 'https://www.loudoun.gov/5990/Data-Center-Standards-Locations',
  va: 'https://www.deq.virginia.gov/news-info/shortcuts/permits/air/issued-air-permits-for-data-centers',
};
const formatValue = (row: ReportedObservation) => row.value.toLocaleString('en-US', { maximumFractionDigits: 3 });
const scopeLabels = { campus: 'Campus total', building: 'Building total', regulatory_facility: 'Regulatory reporting entity', operator_location: 'Operator-defined location' };

function External({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer">{children}<svg className="learn-external" aria-hidden="true" viewBox="0 0 16 16"><path d="M6 3H3v10h10v-3M8 3h5v5M7 9l6-6" /></svg></a>;
}

function ObservationSource({ row }: { row: ReportedObservation }) {
  const href = safeSourceUrl(row.source_url);
  if (!href) return <span>Source link unavailable in this release.</span>;
  const url = typeof row.source_page === 'number' ? `${href.split('#')[0]}#page=${row.source_page}` : href;
  return <External href={url}>{row.publisher}{typeof row.source_page === 'number' ? `, PDF page ${row.source_page}` : ' source record'}</External>;
}

function ExampleNotes({ example }: { example: ReportedExample }) {
  return <div className="learn-example-notes">
    <p><strong>{scopeLabels[example.location.reporting_scope]}.</strong> These reported figures cover the location named by the publisher. They are not allocated to individual inventory records.</p>
    {example.rows.some(row => row.source_status === 'stale') && <p className="learn-retained">Retained source data: a later collection was delayed. The reporting year has not changed.</p>}
    <details><summary>Read the reporting methods and sources</summary>
      {example.rows.map(row => <div key={row.observation_id} className="learn-method"><h4>{row.label}</h4><p>{row.methodology_note}</p><p>Reporting period: {dateLabel(row.period_start)} to {dateLabel(row.period_end)} (end exclusive).</p><p><ObservationSource row={row} /></p></div>)}
    </details>
    <a className="learn-text-link" href={`#/reported?location=${encodeURIComponent(example.location.disclosure_location_id)}`}>Explore this reporting location<Arrow /></a>
  </div>;
}

function Arrow() { return <svg className="learn-arrow" aria-hidden="true" viewBox="0 0 20 20"><path d="M3 10h13m-5-5 5 5-5 5" /></svg>; }

function ReadingNote({ title, children }: { title: string; children: ReactNode }) {
  return <aside className="learn-reading-note"><h3>{title}</h3>{children}</aside>;
}

function ElectricityExample({ example }: { example: ReportedExample }) {
  const row = example.rows[0]!;
  return <section className="learn-example" aria-labelledby="example-title"><h2 id="example-title">A reported example: {example.location.location_label}</h2>
    <p>{row.publisher} reports this annual electricity quantity for {example.year}. It describes a {example.location.reporting_scope === 'campus' ? 'campus, which can include several buildings' : 'publisher-defined reporting location'}.</p>
    <dl className="learn-energy-row"><div><dt>Reported annual electricity</dt><dd>{formatValue(row)} <span>{row.unit}</span></dd></div><div><dt>Reporting year</dt><dd>{example.year}</dd></div></dl>
    <p className="learn-example-source"><ObservationSource row={row} /></p>
    <ExampleNotes example={example} />
  </section>;
}

function WaterExample({ example }: { example: ReportedExample }) {
  const shares = waterShares(example);
  const labels: Record<string, { title: string; description: string }> = {
    water_withdrawal: { title: 'Withdrawn', description: 'Water taken into the reporting location.' },
    water_discharge: { title: 'Discharged', description: 'Water leaving the location as reported discharge.' },
    water_consumption: { title: 'Consumed', description: 'Withdrawal minus discharge under this report’s method.' },
  };
  return <section className="learn-example" aria-labelledby="example-title"><h2 id="example-title">Follow the water: {example.location.location_label}</h2>
    <p>{example.rows[0]!.publisher} reports three water measures for this location in {example.year}. The figures retain the publisher’s unit: {example.rows[0]!.unit}.</p>
    <figure className="learn-water"><figcaption>One reporting location, three distinct measures</figcaption>
      <dl className="learn-water-values">{example.rows.map(row => <div key={row.metric} data-water={row.metric}><dt>{labels[row.metric]?.title ?? row.label}</dt><dd>{formatValue(row)} <span>{row.unit}</span></dd><p>{labels[row.metric]?.description}</p></div>)}</dl>
      {shares && <div className="learn-water-bar" aria-hidden="true"><span className="learn-water-bar__discharged" style={{ width: `${shares.discharged}%` }} /><span className="learn-water-bar__consumed" style={{ width: `${shares.consumed}%` }} /></div>}
      <p className="learn-water-key">{shares ? 'The bar divides reported withdrawal into discharge and consumption. The figures may include estimates.' : 'The figures remain separate. A proportional breakdown is unavailable for these values.'}</p>
    </figure>
    <p className="learn-example-source"><ObservationSource row={example.rows[0]!} /></p><ExampleNotes example={example} />
  </section>;
}

function EmissionsExample({ example }: { example: ReportedExample }) {
  const location = example.rows.find(row => row.metric === 'emissions_scope2_location')!;
  const market = example.rows.find(row => row.metric === 'emissions_scope2_market')!;
  return <section className="learn-example" aria-labelledby="example-title"><h2 id="example-title">Two methods, the same reported campus</h2>
    <p>{location.publisher} reports both figures for {example.location.location_label} in {example.year}. These are two accounting views of purchased-electricity emissions.</p>
    <div className="learn-accounting"><section><h3>Location-based Scope 2</h3><p className="learn-accounting-number">{formatValue(location)} <span>{location.unit}</span></p><p>Uses grid-average emissions factors for the location.</p></section><section><h3>Market-based Scope 2</h3><p className="learn-accounting-number">{formatValue(market)} <span>{market.unit}</span></p><p>Reflects qualifying energy contracts and other contractual instruments.</p></section></div>
    <p className="learn-example-source"><ObservationSource row={location} /></p>
    <p>Read the figures together. Do not add them. A reported market-based zero does not mean the campus used no electricity or that its physical grid supply had no emissions.</p>
    <ExampleNotes example={example} />
  </section>;
}

function useExamples(enabled: boolean) {
  const [data, setData] = useState<FacilityObservations | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setLoading(true); setFailed(false);
    getFacilityObservations().then(value => { if (active) setData(value); })
      .catch(() => { if (active) { setData(null); setFailed(true); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [enabled]);
  return { data, loading, failed, retry: () => window.location.reload() };
}

type ExampleState = ReturnType<typeof useExamples>;
function LiveExample({ state, kind }: { state: ExampleState; kind: 'electricity' | 'water' | 'emissions' | 'virginia' }) {
  if (state.loading) return <p className="learn-load" role="status">Loading the reported example from this data release…</p>;
  if (state.failed) return <div className="learn-load" role="alert"><p>The reported example could not be loaded. The explanation remains available.</p><button className="ghostbtn" onClick={state.retry}>Retry reported data</button></div>;
  const metrics = kind === 'water' ? ['water_withdrawal', 'water_discharge', 'water_consumption'] : kind === 'emissions' ? ['emissions_scope2_location', 'emissions_scope2_market'] : ['electricity_consumption'];
  const id = kind === 'water' ? 'google:douglas-county-ga' : kind === 'virginia' ? 'meta:henrico-va' : 'meta:altoona-ia';
  const example = selectReportedExample(state.data, id, metrics, kind === 'water' || kind === 'emissions');
  if (!example) return <div className="learn-load"><p>This release does not contain a complete, comparable set of values for the example. No number has been substituted.</p><a href="#/reported">Browse available reported data</a></div>;
  return kind === 'water' ? <WaterExample example={example} /> : kind === 'emissions' ? <EmissionsExample example={example} /> : <ElectricityExample example={example} />;
}

function ArticleBody({ topic, state }: { topic: LearningTopic; state: ExampleState }) {
  if (topic === 'electricity') return <>
    <p className="learn-answer">Annual electricity use tells us how much energy was used over a period. It does not tell us the highest demand on the grid, or what a facility is using right now.</p>
    <section className="learn-section"><h2>Start with the unit</h2><dl className="learn-definitions"><div><dt>MW · power</dt><dd>A rate of electricity use at a point in time. Peak demand is the highest rate over a stated interval.</dd></div><div><dt>MWh · energy</dt><dd>Electricity accumulated over time. One MW used steadily for one hour equals one MWh.</dd></div></dl><p>Capacity requested from a utility, contracted capacity and measured demand are also different. A request is not proof of actual use.</p></section>
    <LiveExample state={state} kind="electricity" />
    <ReadingNote title="A national estimate has a different scope"><p>Berkeley Lab estimates U.S. data centers used 192 TWh in 2024, about 4.7% of U.S. electricity. Its June 2026 update excludes cryptocurrency mining. This is a national model estimate, not a total of WattDocket’s collected records.</p><p><External href={sources.lbnl}>Read the Berkeley Lab report, page 8</External></p></ReadingNote>
    <section className="learn-section"><h2>What the quantity cannot answer</h2><p>A larger annual total does not establish lower efficiency. Buildings and campuses differ in size, equipment, workload and operating stage. The available records do not identify how much electricity was used for AI.</p><a className="learn-text-link" href="#/reported?family=electricity">Browse reported electricity<Arrow /></a></section>
  </>;
  if (topic === 'water') return <>
    <p className="learn-answer">A single “water use” number can hide three different questions: how much came in, how much left, and how much was consumed.</p>
    <LiveExample state={state} kind="water" />
    <section className="learn-section"><h2>Keep the measure attached to the number</h2><p>Withdrawal and consumption are not interchangeable. Our collected Meta campus figures report withdrawal; Google’s location disclosures include withdrawal, discharge and consumption. They cannot be put into one unlabeled ranking.</p><p>These disclosures may include estimates. The source does not identify each location’s metered share. We preserve its published units and do not assume which gallon convention it uses.</p></section>
    <ReadingNote title="Volume alone does not establish local impact"><p>To understand local water availability, we would need the supply source, water provider, seasonal demand and relevant watershed conditions. Those relationships are not established by the reported volume or a nearby map point.</p></ReadingNote>
    <section className="learn-section"><h2>Useful questions to ask next</h2><ul><li>Which water provider or withdrawal source supplies this location?</li><li>Does the figure cover cooling, other operations or construction?</li><li>What reporting period and campus boundary does it cover?</li></ul><a className="learn-text-link" href="#/reported?family=water">Browse reported water data<Arrow /></a></section>
  </>;
  if (topic === 'emissions') return <>
    <p className="learn-answer">Companies can report electricity emissions using two accounting methods. Understanding both matters more than selecting the smaller or larger number.</p>
    <LiveExample state={state} kind="emissions" />
    <section className="learn-section"><h2>What each method describes</h2><p>Location-based accounting uses the average emissions intensity of the relevant grid. Market-based accounting uses qualifying contractual information, such as energy-attribute certificates and supplier-specific factors.</p><p><External href={sources.ghg}>Read the GHG Protocol Scope 2 Guidance</External></p></section>
    <ReadingNote title="Neither is an hourly physical measurement"><p>These annual accounting results do not identify which power plants supplied a particular building at each hour. They also do not include a complete lifecycle footprint. The difference between the methods alone does not establish misleading reporting.</p></ReadingNote>
    <section className="learn-section"><h2>Compare like with like</h2><p>Keep the year, reporting boundary and accounting method together. Direct fuel-combustion emissions, electricity Scope 2 and greenhouse gases calculated from building-energy reports answer different questions.</p><a className="learn-text-link" href="#/reported?family=emissions">Explore the separate emissions measures<Arrow /></a></section>
  </>;
  if (topic === 'generators') return <>
    <p className="learn-answer">A permit document can describe equipment and operating conditions. It does not establish how much the equipment actually ran.</p>
    <section className="learn-section"><h2>Read the document in four parts</h2><ol className="learn-steps"><li><strong>Identify the place.</strong> Match the regulatory identifiers and locality. A similar name is not enough.</li><li><strong>Check the document.</strong> Read its date, program and status. A draft is a proposal; an older issued document may have been superseded.</li><li><strong>Read the equipment and conditions.</strong> A table can cover only part of a campus. Nameplate capacity is not actual generation or facility demand.</li><li><strong>Look for operating evidence.</strong> Hours, fuel records and emissions reports are different evidence from permit limits.</li></ol></section>
    <ReadingNote title="Emergency backup and bridge power differ"><p>North Carolina’s August 2026 announcement distinguishes Amazon’s emergency backup engines from Duke Energy’s temporary non-emergency engines intended to supply the campus before grid connection. Those are separate permitted activities.</p><p><External href={sources.nc}>Read North Carolina DEQ’s announcement</External></p><p>This external example is not part of WattDocket’s current Virginia and selected Texas permit collection.</p></ReadingNote>
    <section className="learn-section"><h2>What our permit evidence can show</h2><p>WattDocket preserves quoted conditions, source documents and scoped equipment tables where available. Current legal effectiveness and actual operating hours may remain unknown. These records do not establish local air exposure or a compliance verdict.</p><a className="learn-text-link" href="#/map">Open the records map<Arrow /></a><p>Choose VA in the state filter to browse Virginia records.</p><p><External href={sources.va}>Open Virginia DEQ’s data center permit index</External></p></section>
  </>;
  if (topic === 'grid') return <>
    <p className="learn-answer">Regional electricity observations show a system’s demand and generation. They do not isolate a data center’s consumption or establish its utility connection.</p>
    <section className="learn-section"><h2>Three different kinds of evidence</h2><dl className="learn-definitions"><div><dt>Regional operations</dt><dd>EIA’s hourly observations and ERCOT’s five-minute generation data describe a regional electricity system. Missing readings remain missing.</dd></div><div><dt>Historical grid factors</dt><dd>Annual regional emissions rates give dated accounting context. They do not describe the current hour.</dd></div><div><dt>Facility consumption</dt><dd>A facility meter or properly scoped disclosure is needed to establish the electricity quantity for that reporting location.</dd></div></dl></section>
    <ReadingNote title="Nearby does not mean connected"><p>A geographic grid-region assignment is not proof of the retail utility serving a site. A nearby power plant is not proof of its electricity supplier.</p></ReadingNote>
    <section className="learn-section"><h2>Read time and scope beside the chart</h2><p>The observation time tells you when an event was reported. Collection time tells you when WattDocket retrieved it. Publishing a new snapshot does not turn a historical annual factor into current-year data.</p><a className="learn-text-link" href="#/grid">Explore regional demand and generation<Arrow /></a></section>
  </>;
  if (topic === 'bills') return <>
    <p className="learn-answer">The answer depends on the utility, its rate rules and who is assigned the costs. A regional demand chart cannot tell us how much one data center changes a household bill.</p>
    <section className="learn-section"><h2>Find the rules that apply to the project</h2><p>Relevant evidence includes the serving utility, customer class, electricity-service agreement, applicable tariff and regulator orders. These can explain commitments, infrastructure costs and what happens if a project is delayed or cancelled.</p><p>WattDocket has not collected a site-specific bill-attribution dataset. We cannot calculate a resident’s bill impact from the current records.</p></section>
    <ReadingNote title="An example of a utility’s approach"><p>AEP Ohio’s data-center tariff became effective July 23, 2025. Its published process includes load studies and provisions addressing buildout costs when projects are cancelled or delayed.</p><p>The tariff describes minimum billing demand. That is not a universal requirement to consume 85% of an electricity allocation.</p><p><External href={sources.aep}>Read AEP Ohio’s tariff and process</External></p><p>This is external context for one utility, not a rule applied to every record on our map. Confirm the effective tariff and project applicability before using it.</p></ReadingNote>
    <section className="learn-section"><h2>Start a local investigation</h2><p>Ask which utility serves the project, which tariff applies and where the regulator’s cost-allocation decision can be read. Separate proposed rules from effective ones and modeled bill effects from observed bills.</p><a className="learn-text-link" href="#/learn/virginia">Use the Virginia research guide<Arrow /></a></section>
  </>;
  if (topic === 'community') return <>
    <p className="learn-answer">A useful local account includes documented benefits, resource needs and public decisions. Each needs its own evidence and geographic scope.</p>
    <section className="learn-section"><h2>Ask for both benefits and obligations</h2><p>Tax revenue, permanent jobs, construction jobs, incentives and infrastructure obligations are different quantities. Preserve whether each is projected, contracted or realized, and whether it covers one site or an entire county.</p><p>Loudoun County reports data-center tax revenue and separates existing or under-construction buildings from approved and proposed development. Its countywide figures do not establish a particular site’s net benefit.</p><p><External href={sources.loudoun}>Read Loudoun County’s account</External></p></section>
    <ReadingNote title="What we cannot establish from our current records"><p>Our inventory does not measure neighborhood noise, health outcomes, property-value effects or site-specific tax benefits. A map pin or generator permit is not a substitute for that evidence.</p></ReadingNote>
    <section className="learn-section"><h2>Find the relevant public process</h2><p>Look for zoning applications, planning decisions, incentive agreements and operating reports. Check the project stage and document date before treating a proposal as an approved or operating facility.</p><p><External href={sources.zoning}>Explore Loudoun’s standards and locations project</External></p><a className="learn-text-link" href="#/learn/virginia">Build a local evidence record<Arrow /></a></section>
  </>;
  return <>
    <p className="learn-answer">Use this research guide to build a documented account of one place. It is a checklist of evidence to establish, not a completed assessment of a Virginia facility.</p>
    <section className="learn-section"><h2>Begin with a place and a reporting boundary</h2><p>Keep the parcel, physical site, regulatory records and company reporting location distinct. A campus may contain several buildings and several inventory records. Start with identifiers and primary documents before attaching a quantity to a place.</p></section>
    <LiveExample state={state} kind="virginia" />
    <section className="learn-section"><h2>Build the local record</h2><ol className="learn-steps"><li><strong>Site and project stage.</strong> Find the parcel, owner or operator, local application and dated approval. Distinguish proposed, approved, under construction and operating.</li><li><strong>Electricity service.</strong> Establish the serving utility and applicable service agreement or tariff. Requested or contracted MW is not measured demand.</li><li><strong>Water supply.</strong> Find the provider or withdrawal source, reporting period and cooling information. Do not infer local scarcity from volume alone.</li><li><strong>Generators and operation.</strong> Match the registration and locality, read document status and look for operating reports beyond the permit.</li><li><strong>Community decisions.</strong> Read tax or incentive agreements, infrastructure obligations and public proceedings. Separate forecasts from realized outcomes.</li></ol></section>
    <ReadingNote title="A statewide report shows why the distinctions matter"><p>The Virginia SCC’s October 1, 2026 report lists utility service agreements or contracted demand, rather than measured simultaneous consumption. It also says municipal water responses were not received in time. Missing responses do not mean zero water use.</p><p><External href={sources.scc}>Read the SCC report</External></p><p>This statewide source is external context. It does not establish a utility or water-provider relationship for an individual inventory record.</p></ReadingNote>
    <section className="learn-section"><h2>Keep the next unanswered question visible</h2><p>Record the document you checked, what it supports and the specific evidence still needed. A missing value in our collection is not proof that nobody has published it.</p><div className="learn-actions"><a className="learn-text-link" href="#/map?q=Henrico">Explore Henrico records<Arrow /></a><a className="learn-text-link" href="#/data">See source coverage<Arrow /></a></div></section>
  </>;
}

export function LearnPage({ topic }: { topic?: string }) {
  const selected = learningTopics.find(item => item.id === topic);
  const needsExample = selected != null && ['electricity', 'water', 'emissions', 'virginia'].includes(selected.id);
  const state = useExamples(needsExample);
  if (topic && !selected) return <main className="page learn-page"><div className="learn-not-found"><h1>This explanation is not available.</h1><p>Choose a question from the guide to data center evidence.</p><a className="learn-text-link" href="#/learn">Explore the questions<Arrow /></a></div></main>;
  if (!selected) return <main className="page learn-page"><header className="learn-index-head"><h1>Make sense of data center evidence.</h1><p>Start with a question. Learn what the numbers describe, how to read the source, and where an answer needs more evidence.</p></header>
    <div className="learn-index-layout"><section className="learn-question-list" aria-label="Questions about data centers">{learningTopics.map(item => <a key={item.id} href={`#/learn/${item.id}`}><div><h2>{item.title}</h2><p>{item.description}</p></div><Arrow /></a>)}</section>
      <aside className="learn-primer"><h2>What is a data center?</h2><p>Data centers house computers, storage and networking equipment that provide digital services. Electricity runs the equipment and supporting systems. Cooling removes heat.</p><p>Facilities differ in size, workload and cooling design. A data center record does not establish which workloads are AI.</p><p><External href={sources.lbnl}>Read Berkeley Lab’s definitions</External></p><hr /><h3>Read every number with its context</h3><ul><li>Who reported it?</li><li>What place and period does it cover?</li><li>What measure and method does it use?</li><li>What remains unknown?</li></ul><a href="#/reported">Explore the reported data</a></aside></div>
  </main>;
  return <main className="page learn-page"><div className="learn-layout"><aside className="learn-sidebar"><a className="learn-back" href="#/learn">All questions</a><nav aria-label="Data center explainers">{learningTopics.map(item => <a key={item.id} href={`#/learn/${item.id}`} aria-current={item.id === selected.id ? 'page' : undefined}>{item.short}</a>)}</nav><p>Examples use the published data release. Every figure keeps its source and reporting scope.</p></aside>
    <article className="learn-article"><header className="learn-article-head"><h1>{selected.title}</h1></header><ArticleBody topic={selected.id} state={state} /><footer className="learn-article-footer"><p>Explanatory sources reviewed November 3, 2026. Annual examples retain their own reporting year and source methods.</p><a href="#/data">Read data coverage and source dates</a></footer></article></div></main>;
}
