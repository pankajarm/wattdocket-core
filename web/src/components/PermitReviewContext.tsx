import type { PermitDocumentReview, PermitHistoryReview, PermitReviewEvidence } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';

const LISTING_LABELS = {
  latest_listed_for_registration: 'Latest action listed for this registration',
  listed_with_later_document: 'Other, later documents share this registration',
  not_listed: 'Document not found in the checked index',
  unknown: 'Index history not established',
};

export function permitCurrentnessSummary(history?: PermitHistoryReview): string {
  if (history?.status === 'review_required') return 'The source record changed; the earlier history review needs to be checked again.';
  const legal = history?.status === 'verified' ? history.legal_effectiveness : undefined;
  if (legal?.status === 'superseded' && legal.evidence?.length) {
    return legal.scope === 'entire_document' ? 'The reviewed source explicitly replaces this document.' : 'The reviewed source replaces specified conditions; this does not mean every condition was replaced.';
  }
  if (legal?.status === 'source_confirmed_current' && legal.evidence?.length) {
    return legal.scope === 'entire_document' ? 'The cited regulator record confirms this document as current as of the source check.' : 'The cited regulator record confirms only the specified conditions as current as of the source check.';
  }
  return 'Current legal effectiveness is unknown.';
}

function ReviewEvidence({ evidence, label }: {evidence?: PermitReviewEvidence[]; label: string}) {
  if (!evidence?.length) return null;
  return <details>
    <summary>{label}</summary>
    {evidence.map((item, index) => {
      const source = safeSourceUrl(item.url);
      return <div className="source-document__statement" key={`${item.url}-${index}`}>
        {item.quote && <blockquote>{item.quote}</blockquote>}
        {source && <a href={`${source.split('#')[0]}${item.pdf_page ? `#page=${item.pdf_page}` : ''}`} target="_blank" rel="noreferrer">{item.locator || 'Open supporting public record'}</a>}
        {item.receipt?.retrieved_at && <p>Source checked: {dateLabel(item.receipt.retrieved_at)}.</p>}
      </div>;
    })}
  </details>;
}

/** A regulator's site identity, site status and permit history are separate findings. */
export function PermitReviewContext({ document }: {document: PermitDocumentReview}) {
  const history = document.permit_history_review;
  const site = document.site_identity_review;
  if (!history && !site) return null;
  const address = site?.physical_address;
  const addressText = address ? [address.street, address.city, address.state, address.postal_code].filter(Boolean).join(', ') : '';
  const operatingStatus = site?.site_status?.air_operating_status || site?.site_status?.status_group;
  const listing = history?.registry_listing;
  return <div className="source-document__statement" aria-label={`Site and permit history for ${document.registration}`}>
    {site && <>
      <h4>{site.status === 'regulator_site_verified' ? 'Site verified in the regulator’s register' : site.status === 'review_required' ? 'Site match needs a new review' : 'Site identity remains unresolved'}</h4>
      {addressText && <p>Physical address in the regulator record: {addressText}.</p>}
      {operatingStatus && <p>Regulator-reported site status: <strong>{operatingStatus}</strong>{site.retrieved_at && <> · source captured {dateLabel(site.retrieved_at)}</>}. This is a site status, not confirmation that every permit condition remains effective.</p>}
      {site.source_freshness && site.source_freshness !== 'current' && <p>The site-register refresh is delayed. This is the last retained source record.</p>}
      {site.note && <p>{site.note}</p>}
      {site.inventory_links?.filter((link) => link.combined_address_conflict).map((link) => <div key={link.record_id} className="source-document__statement">
        <h4>Address conflict in the source records</h4>
        <p>EPA’s consolidated record gives {link.combined_address_conflict!.consolidated_frs_address}. The air-program record gives {link.combined_address_conflict!.air_program_address}, corroborated by DEQ at {link.combined_address_conflict!.deq_physical_address}.</p>
        <p>The permit link identifies the regulatory air site. Map placement and regional grid assignment are withheld until the physical-location conflict is resolved.</p>
      </div>)}
      <ReviewEvidence evidence={site.evidence} label="Read the site-match evidence" />
    </>}
    {history && <>
      <h4>Permit history check</h4>
      <p>{permitCurrentnessSummary(history)}</p>
      {listing && <p>{LISTING_LABELS[listing.status] || LISTING_LABELS.unknown}.{listing.registry_as_of && <> Index dated {dateLabel(listing.registry_as_of)}.</>} {listing.checked_at && <>Checked {dateLabel(listing.checked_at)}.</>}</p>}
      {history.legal_effectiveness?.note && <p>{history.legal_effectiveness.note}</p>}
      {history.note && history.note !== history.legal_effectiveness?.note && <p>{history.note}</p>}
      {history.events && history.events.length > 0 && <details>
        <summary>Read the reviewed changes and exceptions</summary>
        {history.events.map((event, index) => <div className="source-document__statement" key={index}>
          <p>{event.scope}</p>
          <ReviewEvidence evidence={event.evidence} label="Read the supporting document" />
        </div>)}
      </details>}
      <ReviewEvidence evidence={history.legal_effectiveness?.evidence} label="Read the permit-status evidence" />
      {listing?.current_rows && listing.current_rows.length > 1 && <details>
        <summary>Documents listed under this registration</summary>
        <ul>{listing.current_rows.map((row) => <li key={`${row.registration}-${row.url}`}>
          {safeSourceUrl(row.url) ? <a href={safeSourceUrl(row.url)} target="_blank" rel="noreferrer">{row.registration}</a> : row.registration}{row.issued_at && <> · {dateLabel(row.issued_at)}</>}
        </li>)}</ul>
      </details>}
    </>}
  </div>;
}
