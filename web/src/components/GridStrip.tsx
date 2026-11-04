import type { GridBinding, HourlySeries } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';

export function gridStats(hourly: HourlySeries) {
  const values = hourly.readings.map((r) => r.intensity_lb_co2e_per_mwh);
  const min = Math.min(...values), max = Math.max(...values);
  const avg = values.reduce((a, b) => a + b, 0) / Math.max(values.length, 1);
  return { values, min, max, avg, minHour: values.indexOf(min), maxHour: values.indexOf(max) };
}

export function GridStrip({ grid }: { grid: GridBinding; hourly: HourlySeries | null }) {
  const source = safeSourceUrl(grid.source_url);
  const evidence = safeSourceUrl(grid.evidence_url);
  const year = grid.data_vintage;
  const rate = grid.rate_lb_per_mwh;
  return <>
    <dl className="strip">
      <div className="strip__cell"><dt className="strip__k">Geographic grid region</dt><dd className="strip__v">{grid.egrid_subregion && grid.egrid_subregion !== '—' ? `eGRID ${grid.egrid_subregion}` : 'Not established'}{grid.subregion_label ? ` (${grid.subregion_label})` : ''}</dd></div>
      <div className="strip__cell"><dt className="strip__k">{year ? `${year} annual regional emissions rate` : 'Historical annual regional emissions rate'}</dt><dd className="strip__v">{rate == null ? 'Not available' : <>{Math.round(rate).toLocaleString('en-US')} lb CO₂e/MWh</>}</dd></div>
      <div className="strip__cell"><dt className="strip__k">Factor source</dt><dd className="strip__v">{source ? <a href={source} target="_blank" rel="noreferrer">{grid.factor_publisher || grid.source_label || 'Annual factor source'}</a> : grid.source_label || 'Source not supplied'}{evidence && <small><a href={evidence} target="_blank" rel="noreferrer">Read the archived factor row</a></small>}</dd></div>
    </dl>
    <p className="evidence-section__note">This historical regional average does not report this facility’s emissions or its current electricity supply.</p>
    <details className="grid-factor-details"><summary>Factor dates and accounting basis</summary><dl className="source-row__facts"><div><dt>Observation period</dt><dd>{year || 'Not supplied'}</dd></div><div><dt>Publisher release date</dt><dd>{dateLabel(grid.published_at)}</dd></div><div><dt>Source retrieved</dt><dd>{dateLabel(grid.retrieved_at, true)}</dd></div></dl><p>{grid.accounting_basis || 'Annual location-based regional CO₂e average. This is neither an hourly nor a marginal emissions factor.'}</p></details>
    {grid.alternative && <details className="grid-alternative"><summary>Independent annual comparison ({grid.alternative.data_year})</summary><p>{grid.alternative.source_label}: {grid.alternative.rate_lb_per_mwh.toLocaleString('en-US', { maximumFractionDigits: 1 })} lb CO₂e/MWh. This independent historical estimate is not the official EPA baseline used above. Method differences must be considered before comparing years.</p><p>Publisher released: {dateLabel(grid.alternative['published_at'] as string | null)}. Retrieved: {dateLabel(grid.alternative['retrieved_at'] as string | null, true)}.</p><p>{grid.alternative.methodology || String(grid.alternative['accounting_basis'] || '')}</p><p>{String(grid.alternative['note'] || '')} {grid.alternative['gwp_basis'] ? `Global warming potential basis: ${String(grid.alternative['gwp_basis'])}.` : ''}</p>{safeSourceUrl(grid.alternative.source_url) && <a href={safeSourceUrl(grid.alternative.source_url)} target="_blank" rel="noreferrer">Comparison source and method</a>}</details>}
  </>;
}
