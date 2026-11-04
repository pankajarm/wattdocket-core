import type { OperatorWaterData, OperatorWaterObservation } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';

function reported(value: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US', {maximumFractionDigits:1}) : 'Unavailable';
}

function WaterTable({ rows, caption }: {rows: OperatorWaterObservation[]; caption: string}) {
  return <div className="operator-water__scroll" tabIndex={0} role="region" aria-label={caption}><table className="operator-water__table"><caption>{caption} · million gallons</caption><thead><tr><th scope="col">Reported location</th><th scope="col">Withdrawal</th><th scope="col">Discharge</th><th scope="col">Consumption</th></tr></thead><tbody>{rows.map((row,index) => <tr key={`${row.location_label}-${index}`}><th scope="row">{row.location_label}{row.country ? ` (${row.country})` : ''}</th><td>{reported(row.withdrawal_million_gallons)}</td><td>{reported(row.discharge_million_gallons)}</td><td>{reported(row.consumption_million_gallons)}</td></tr>)}</tbody></table></div>;
}

export function OperatorWater({ data }: {data: OperatorWaterData}) {
  const source = data.source;
  const month = /^\d{4}-(0[1-9]|1[0-2])$/.test(source.publication_month) ? new Intl.DateTimeFormat('en-US', {month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(`${source.publication_month}-01T00:00:00Z`)) : 'Not supplied';
  const url = safeSourceUrl(source.pdf_url);
  return <section className="operator-water" aria-labelledby="operator-water-title"><h2 id="operator-water-title">Operator-reported location water</h2>
    <p>{source.data_year} {source.publisher} location totals may include estimates. These are publisher-reported global locations, not individual EPA-record measurements. A named location may include multiple buildings; no facility join is verified.</p>
    <p>{source.report}, table page {source.source_page}. Published {source.published_at ? dateLabel(source.published_at) : month}{source.published_at ? '' : ' (exact day not supplied)'}. Retrieved {dateLabel(data.retrieved_at, true)}. {url && <a href={`${url}#page=${source.source_page}`} target="_blank" rel="noreferrer">Read the publisher’s water table</a>}</p>
    <details><summary>Read {data.observations.length} reported location rows and publisher aggregates</summary>
      <WaterTable rows={data.observations} caption={`${source.data_year} named global locations`} />
      {data.aggregates.length > 0 && <><p>Publisher aggregates are shown separately. They may overlap the named rows and must not be added to them.</p><WaterTable rows={data.aggregates} caption={`${source.data_year} publisher aggregates`} /></>}
      <p>{source.methodology_note}</p><p>The report’s unit, “million gallons,” is retained without assuming a US or imperial gallon convention.</p><p>{source.geography_note}</p>
    </details>
  </section>;
}
