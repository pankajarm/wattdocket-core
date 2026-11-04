import { useEffect, useMemo, useState } from 'react';
import { getGridIndex, getGridRegion } from '../api';
import type { GridRegionSeries, GridRegionSummary } from '../types';
import { dateLabel } from '../dataPresentation';
import { useElementWidth } from '../router';
import './data-pages.css';

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const number = (value: number | null | undefined) => finite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 1 }) : 'Not reported';

export function GridPage() {
  const [regions, setRegions] = useState<GridRegionSummary[] | null>(null);
  const [code, setCode] = useState('');
  const [series, setSeries] = useState<GridRegionSeries | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  useEffect(() => { let active = true; getGridIndex().then((data) => { if (!active) return; setRegions(data.regions); setCode(data.regions.find((r) => r.code === 'US48')?.code ?? data.regions[0]?.code ?? ''); }).catch((error: unknown) => { if (active) setIndexError(error instanceof Error ? error.message : 'Grid regions are unavailable.'); }); return () => { active = false; }; }, []);
  useEffect(() => { if (!code) return; let active = true; setSeries(null); setSeriesError(null); getGridRegion(code).then((data) => { if (active) setSeries(data); }).catch((error: unknown) => { if (active) setSeriesError(error instanceof Error ? error.message : 'This region is unavailable.'); }); return () => { active = false; }; }, [code]);
  const ordered = useMemo(() => (series?.readings ?? []).filter((row) => Number.isFinite(Date.parse(row.interval_start_utc))).slice().sort((a, b) => Date.parse(a.interval_start_utc) - Date.parse(b.interval_start_utc)), [series]);
  const observed = ordered.filter((row) => finite(row.demand_mw) || finite(row.generation_mw) || Object.values(row.fuel_mix ?? {}).some(finite));
  const latest = observed[observed.length - 1];
  const fuels = latest?.fuel_mix ? Object.entries(latest.fuel_mix).filter(([, value]) => finite(value)).sort((a, b) => b[1] - a[1]) : [];
  return <main className="page data-page">
    <header className="data-page__header"><h1>The electricity grid, in context.</h1><p>Reported regional demand and generation. These observations describe the grid as a whole; they do not measure a particular facility.</p></header>
    {indexError ? <div className="data-message" role="alert"><h2>The grid feed is unavailable</h2><p>{indexError}</p><button className="ghostbtn" onClick={() => window.location.reload()}>Reload data</button></div>
      : !regions ? <p className="data-message" role="status">Loading grid regions…</p>
      : regions.length === 0 ? <p className="data-message">No grid regions were published in this release. <a href="#/data">Check source status</a>.</p>
      : <>
        <div className="grid-controls"><label htmlFor="grid-region">Region<select id="grid-region" value={code} onChange={(event) => setCode(event.target.value)}>{regions.map((region) => <option key={region.code} value={region.code}>{region.name} ({region.code})</option>)}</select></label><a href="#/data">Source coverage &amp; freshness</a></div>
        {seriesError ? <p className="data-message" role="alert">{seriesError} Select another region or reload to retry.</p>
          : !series ? <p className="data-message" role="status">Loading reported observations…</p>
          : <section className="grid-observations" aria-live="polite">
            <div className="grid-observations__head"><h2>{series.name || code}</h2><span>Retrieved {dateLabel(series.retrieved_at, true)}</span></div>
            <p className="data-page__note">Source: {series.source_id}. Observations {dateLabel(series.observation_start, true)} – {dateLabel(series.observation_end, true)}. Times are UTC; missing readings stay empty.</p>
            {latest ? <>
              <dl className="grid-current"><div><dt>Latest reported demand</dt><dd>{number(latest.demand_mw)}{finite(latest.demand_mw) ? ' MW' : ''}</dd></div><div><dt>Latest reported generation</dt><dd>{number(latest.generation_mw)}{finite(latest.generation_mw) ? ' MW' : ''}</dd></div><div><dt>Interval start</dt><dd>{dateLabel(latest.interval_start_utc, true)}</dd></div></dl>
              <DemandChart readings={ordered.slice(-72)} />
              {fuels.length > 0 && <div className="grid-fuels"><h3>Generation by fuel in the latest interval</h3><dl>{fuels.map(([fuel, value]) => <div key={fuel}><dt>{fuel.replace(/_/g, ' ')}</dt><dd>{number(value)} MW</dd></div>)}</dl></div>}
              <details className="grid-table-details"><summary>Read the latest {Math.min(48, ordered.length)} intervals</summary><div className="data-table-scroll"><table className="data-table"><thead><tr><th scope="col">Interval start (UTC)</th><th scope="col">Minutes</th><th scope="col">Demand (MW)</th><th scope="col">Generation (MW)</th></tr></thead><tbody>{ordered.slice(-48).reverse().map((row) => <tr key={row.interval_start_utc}><th scope="row">{dateLabel(row.interval_start_utc, true)}</th><td>{row.interval_minutes}</td><td>{number(row.demand_mw)}</td><td>{number(row.generation_mw)}</td></tr>)}</tbody></table></div></details>
            </> : <p className="data-message">This region has no published observations in the current release.</p>}
            {series.five_minute && <FiveMinuteFuel series={series.five_minute} />}
          </section>}
      </>}
  </main>;
}

function FiveMinuteFuel({ series }: { series: GridRegionSeries }) {
  const observed = series.readings.filter((row) => Object.values(row.fuel_mix ?? {}).some(finite)).slice().sort((a,b) => Date.parse(a.interval_start_utc) - Date.parse(b.interval_start_utc));
  const latest = observed[observed.length - 1];
  if (!latest) return null;
  return <section className="grid-fast"><h3>ERCOT five-minute generation</h3><p className="data-page__note">Reported fuel mix at {dateLabel(latest.interval_start_utc, true)}. Retrieved {dateLabel(series.retrieved_at, true)}. Storage is reported separately from generating fuels.</p><dl className="grid-current"><div><dt>Generation, excluding storage</dt><dd>{number(latest.generation_mw)}{finite(latest.generation_mw) ? ' MW' : ''}</dd></div><div><dt>Reported storage</dt><dd>{number(latest.storage_mw)}{finite(latest.storage_mw) ? ' MW' : ''}</dd></div><div><dt>Interval length</dt><dd>{latest.interval_minutes} minutes</dd></div></dl><div className="grid-fuels"><dl>{Object.entries(latest.fuel_mix ?? {}).filter(([,value])=>finite(value)).sort((a,b)=>b[1]-a[1]).map(([fuel,value])=><div key={fuel}><dt>{fuel.replace(/_/g,' ')}</dt><dd>{number(value)} MW</dd></div>)}</dl></div></section>;
}

export function DemandChart({ readings }: { readings: GridRegionSeries['readings'] }) {
  const [ref, measured] = useElementWidth<HTMLDivElement>();
  const width = Math.max(measured, 280);
  const height = 260;
  const left = 62, right = 14, top = 24, bottom = 48;
  const values = readings.map((row) => row.demand_mw).filter(finite);
  if (values.length === 0) return <p className="data-message">No reported demand values are available to plot.</p>;
  const minimum = Math.min(0, ...values), maximum = Math.max(1, ...values) * 1.05;
  const first = Date.parse(readings[0]!.interval_start_utc), last = Date.parse(readings[readings.length - 1]!.interval_start_utc);
  const x = (value: number) => left + (value - first) / Math.max(last - first, 1) * (width - left - right);
  const y = (value: number) => top + (maximum - value) / (maximum - minimum) * (height - top - bottom);
  let path = '', connected = false, previous: number | null = null;
  for (const row of readings) {
    const at = Date.parse(row.interval_start_utc);
    if (!finite(row.demand_mw)) { connected = false; previous = at; continue; }
    if (previous != null && at - previous > Math.max(1, row.interval_minutes) * 60000 * 1.5) connected = false;
    path += `${connected ? 'L' : 'M'}${x(at).toFixed(2)},${y(row.demand_mw).toFixed(2)} `;
    connected = true; previous = at;
  }
  const ticks = Array.from({ length: 4 }, (_, index) => minimum + (maximum - minimum) * index / 3);
  return <figure className="demand-chart" ref={ref}><figcaption>Reported demand · most recent {readings.length} intervals</figcaption><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Reported grid demand ranges from ${number(Math.min(...values))} to ${number(Math.max(...values))} megawatts. Missing intervals are gaps.`}>
    {ticks.map((value) => <g key={value}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text x={left - 8} y={y(value) + 4} textAnchor="end">{Math.round(value).toLocaleString('en-US')}</text></g>)}
    <text x={left} y={14}>MW</text><path className="demand-chart__line" d={path} />
    {[0, 1, 2].map((step) => { const at = first + (last - first) * step / 2; return <text key={step} x={x(at)} y={height - 24} textAnchor={step === 0 ? 'start' : step === 2 ? 'end' : 'middle'}>{new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', timeZone: 'UTC' }).format(at)}</text>; })}
    <text x={(width + left - right) / 2} y={height - 4} textAnchor="middle">Interval start · UTC</text>
  </svg></figure>;
}
