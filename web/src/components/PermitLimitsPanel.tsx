import type { FacilityPayload, PermitDocumentReview, PermitPageEvidence, PermitLimit } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';
import { EvidenceFreshness } from './EvidenceFreshness';
import { PermitReviewContext, permitCurrentnessSummary } from './PermitReviewContext';

/** Original document evidence, preserving scope and unresolved legal effect. */
const LABELS: Record<string, string> = {
  // Virginia caps these separately, and the difference matters: 17 hours of
  // scheduled testing plus a separate 500-hour total is not a 17-hour site.
  mcrt_hours_limit: 'Maintenance & testing hours',
  generator_hours_limit: 'Quoted operating-hours condition',
  generator_group_hours_limit: 'Combined generator-hours condition',
  fuel_throughput_limit: 'Diesel fuel',
  fuel_sulfur_limit: 'Fuel sulfur',
};

const CAPS: Record<string, string> = {
  mcrt_hours_limit: 'no more than',
  generator_hours_limit: 'no more than',
  fuel_throughput_limit: 'no more than',
  fuel_sulfur_limit: 'at most',
};

function fmt(metric: string, value: number): string {
  if (metric === 'fuel_sulfur_limit') return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function hasHoursException(limit: PermitLimit): boolean {
  return limit.metric === 'mcrt_hours_limit' && /exception|additional\s+hours/i.test(limit.reviewed_operating_purpose ?? '');
}

const EXTENTS = {
  full_permit: 'Full permit document',
  partial_amendment: 'Partial amendment',
  correspondence: 'Permit correspondence',
  permit_letter: 'Permit letter',
  unknown: 'Permit document',
};

function PageLink({ url, page }: { url: string; page?: number | null }) {
  const source = safeSourceUrl(url);
  if (!source || !page) return null;
  return <a href={`${source.split('#')[0]}#page=${page}`} target="_blank" rel="noreferrer">PDF p.{page}</a>;
}

function IdentityEvidence({ label, evidence, url }: { label: string; evidence?: PermitPageEvidence; url: string }) {
  if (!evidence?.value) return null;
  return <p>{label}: {evidence.value}. <PageLink url={url} page={evidence.pdf_page} /></p>;
}

function ReviewedDocument({ document, hasConditions }: { document: PermitDocumentReview; hasConditions: boolean }) {
  const review = document.recovery_review;
  const source = safeSourceUrl(document.url);
  const identity = review.identity_review;
  const statements = review.supersession_review?.statements ?? [];
  return <section className="source-document" aria-label={`Permit document ${document.registration}`}>
    <h3>{document.registration} · {EXTENTS[review.document_extent] ?? EXTENTS.unknown}</h3>
    <p>{document.representation === 'publisher_pdf' ? 'Original publisher PDF. ' : ''}Issued: {dateLabel(document.issued_at)}. Retrieved: {dateLabel(document.retrieved_at)}.</p>
    {review.document_extent === 'partial_amendment' && <p>This amendment replaces selected pages or conditions. Earlier documents are needed for the remaining terms.</p>}
    {(review.document_extent === 'correspondence' || review.document_extent === 'permit_letter') && <p>This file is correspondence about the permit; it does not provide a complete set of permit conditions.</p>}
    {!document.permit_history_review && <p>{permitCurrentnessSummary()}{!hasConditions && ' No qualifying numeric condition is shown from this document.'}</p>}
    {document.permit_history_review && !hasConditions && <p>No qualifying numeric condition is shown from this document.</p>}
    {review.status === 'review_required' && <p>The document has changed. Earlier numeric review approval is withdrawn until the new version is checked.</p>}
    {source && <a href={source} target="_blank" rel="noreferrer">Open original PDF</a>}
    <PermitReviewContext document={document} />
    {(identity || statements.length > 0) && <details>
      <summary>Read identity and document history</summary>
      <IdentityEvidence label="Registration in document" evidence={identity?.registration} url={document.url} />
      <IdentityEvidence label="County in source evidence" evidence={identity?.county} url={document.url} />
      <IdentityEvidence label="Physical location stated in document" evidence={identity?.physical_address} url={document.url} />
      {statements.map((statement, index) => <div className="source-document__statement" key={`${statement.pdf_page}-${index}`}>
        <blockquote>{statement.quote}</blockquote>
        <PageLink url={document.url} page={statement.pdf_page} />
        {statement.scope && <p>Change described: {statement.scope.replace(/_/g, ' ')}.</p>}
        {statement.replaced_dates.length > 0 && <p>Earlier document dates named: {statement.replaced_dates.map((date) => dateLabel(date)).join(', ')}.</p>}
      </div>)}
      {review.reviewed_at && <p>Document review: {dateLabel(review.reviewed_at)}. This review does not establish that every condition is still effective.</p>}
    </details>}
  </section>;
}

export function PermitLimitsPanel({ payload, limits: suppliedLimits, reviews: suppliedReviews }: { payload?: FacilityPayload; limits?: PermitLimit[]; reviews?: PermitDocumentReview[] }) {
  const raw = suppliedLimits ?? payload?.facility.permit_limits ?? [];
  const reviews = suppliedReviews ?? payload?.facility.permit_reviews ?? [];
  if (raw.length === 0 && reviews.length === 0) return null;
  const ORDER = ['mcrt_hours_limit', 'generator_hours_limit', 'generator_group_hours_limit', 'fuel_throughput_limit',
                 'fuel_sulfur_limit'];
  const limits = [...raw].sort(
    (a, b) => ORDER.indexOf(a.metric) - ORDER.indexOf(b.metric),
  );

  const viaProxy = limits.some((l) => (l.retrieved_via ?? '').includes('r.jina.ai'));
  const registrations = [...new Set([...limits.map((l) => l.registration), ...reviews.map((r) => r.registration)].filter(Boolean))];

  return (
    <div className="limits">
      <div className="limits__head">
        <span className="limits__k">{limits.length ? 'QUOTED PERMIT CONDITIONS' : 'PERMIT DOCUMENTS'}</span>
        <span className="limits__no">
          Virginia DEQ permit {registrations.join(', ')}
        </span>
      </div>

      {limits.length > 0 && <ul className="limits__rows">
        {limits.map((l, index) => (
          <li key={`${l.registration}-${l.metric}-${l.page}-${index}`} className="limits__condition">
            <span className="limits__what">{LABELS[l.metric] ?? l.metric}</span>
            <span className="limits__val">
              {hasHoursException(l) ? 'quoted baseline' : CAPS[l.metric] ?? 'no more than'} <b>{fmt(l.metric, l.value)}</b>{' '}
              {l.unit}
              {hasHoursException(l) && <> · {l.reviewed_operating_purpose}</>}
            </span>
            {l.page ? <span className="limits__pg">p.{l.page}</span> : null}
            <div className="limits__scope">{l.applies_to_entire_site === true ? 'The source explicitly identifies this as a whole-site condition.' : 'Applies only to the equipment or activity in the quoted condition; a whole-site limit is not established.'}
              {l.reviewed_condition_scope && <p>Condition applies to: {l.reviewed_condition_scope}.</p>}
              {l.reviewed_operating_purpose && !hasHoursException(l) && <p>Operating purpose: {l.reviewed_operating_purpose}.</p>}
              {l.reviewed_period && <p>Reporting period: {l.reviewed_period}.</p>}
              {l.representation === 'reviewed_pdf_transcription' && <p>The quotation was transcribed and checked against the original PDF image.</p>}
              <p>{l.program ? `Program: ${l.program}. ` : ''}Document issued: {dateLabel(l.issued_at)}. {l.document_status ? `Document state: ${l.document_status.replace(/_/g, ' ')}. ` : ''}Current effectiveness: {l.effective_status?.replace(/_/g, ' ') || 'not verified'}.</p>
              {l.supersedes && l.supersedes.length > 0 && <p>Supersedes: {l.supersedes.join(', ')}.</p>}
              <EvidenceFreshness evidence={l} />
              {l.quote && <details><summary>Read the permit condition</summary><blockquote>{l.quote}</blockquote>{l.page && <span>Page {l.page}. </span>}{safeSourceUrl(l.url) && <a href={safeSourceUrl(l.url)} target="_blank" rel="noreferrer">Open source document ↗</a>}</details>}
            </div>
          </li>
        ))}
      </ul>}

      {reviews.map((document) => <ReviewedDocument key={`${document.registration}-${document.content_sha256}`} document={document} hasConditions={limits.some((limit) => limit.registration === document.registration)} />)}

      <div className="limits__foot">
        These are conditions in the cited documents. Their current legal effect and supersession may be unresolved. They do not establish actual operating hours, fuel use, or complete facility limits.
        {viaProxy ? (
          <>
            {' '}
            · text retrieved through a reading service because the Commonwealth&apos;s
            server blocks this host; the quote and page are from the document itself.
          </>
        ) : null}
      </div>
    </div>
  );
}
