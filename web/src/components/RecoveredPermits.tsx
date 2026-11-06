import { useEffect, useRef, useState } from 'react';
import type { RecoveredPermitDocument } from '../types';
import { normaliseClaim } from '../normalise';
import { safeSourceUrl } from '../dataPresentation';
import { publicHref } from '../router';
import { PermitLimitsPanel } from './PermitLimitsPanel';
import { ExplainPanel } from './ExplainPanel';
import { permitCurrentnessSummary } from './PermitReviewContext';

function RecoveredPermit({ document, releaseId, focused }: {document: RecoveredPermitDocument; releaseId: string; focused: boolean}) {
  const [open, setOpen] = useState(focused);
  const element = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    setOpen(focused);
    if (!focused || !element.current) return;
    // Wait for the expanded row and the router's scroll reset to settle.
    const frame = window.requestAnimationFrame(() => element.current?.scrollIntoView?.({block: 'start'}));
    return () => window.cancelAnimationFrame(frame);
  }, [focused]);
  const records = (document.inventory_record_ids ?? []).filter((id) => typeof id === 'string' && /^(echo|osm):[A-Za-z0-9:_-]+$/.test(id));
  const equipment = (document.equipment_claims ?? []).map((raw) => normaliseClaim(raw, 'permit_document:' + document.registration))
    .filter((claim) => claim?.metric === 'diesel_mw' && claim.renderable);
  const siteVerified = document.site_identity_review?.status === 'regulator_site_verified';
  const summary = `${document.site} · Virginia permit document ${document.registration}. ${permitCurrentnessSummary(document.permit_history_review)} ${siteVerified ? 'The physical site was checked against the regulator’s register. ' : ''}${records.length ? 'A verified inventory link is available below; document conditions are not operating measurements.' : 'No verified inventory match is established. This remains an independent document record.'}`;
  return <details ref={element} id={`permit-${document.registration}`} className="source-document" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><strong>{document.site}</strong> · {document.registration}{document.county ? ` · ${document.county}` : ''}</summary>
    <p>{records.length ? 'Verified inventory match:' : siteVerified ? 'Physical site verified; no matching inventory record has been established.' : 'No verified inventory match. This remains an independent document record.'}
      {records.map((id, index) => <span key={id}> {index > 0 && ' · '}<a href={publicHref(id)}>View inventory record {id}</a></span>)}
    </p>
    <PermitLimitsPanel limits={document.conditions ?? []} reviews={[document]} />
    {equipment.map((claim) => claim?.value.kind === 'NUMERIC' ? <div className="source-document__statement" key={claim.id}>
      <h3>{claim.label}: {claim.value.value.toLocaleString('en-US', {maximumFractionDigits: 3})} {claim.value.unit}</h3>
      <p>{claim.unit_note || 'Only the cited equipment table; a complete current fleet is not established.'} This is nameplate evidence, not actual operating power.</p>
      {claim.citations.length > 0 && <details><summary>Read equipment table evidence</summary>
        {claim.citations.map((citation, index) => <div key={index}><blockquote>{citation.quote}</blockquote>
          {safeSourceUrl(citation.url) && <a href={`${safeSourceUrl(citation.url)!.split('#')[0]}${citation.page ? '#page=' + citation.page : ''}`} target="_blank" rel="noreferrer">Original PDF{citation.page ? ` p.${citation.page}` : ''}</a>}
        </div>)}
      </details>}
    </div> : null)}
    <ExplainPanel releaseId={releaseId} subject={{kind: 'permit_document', id: document.registration}}
      template={summary} actionLabel="Explain this permit document" title="Explain this document’s evidence"
      scopeNote="Explains the reviewed site match, permit history, quoted conditions and equipment evidence. Site status and document history do not by themselves establish current legal limits or actual operation." />
    <p><a href={`#/data?permit=${encodeURIComponent(document.registration)}`}>Link to this permit document</a></p>
  </details>;
}

export function RecoveredPermits({ documents, releaseId, focusedRegistration }: {documents: RecoveredPermitDocument[]; releaseId: string; focusedRegistration?: string | null}) {
  if (!documents.length) return null;
  return <section aria-labelledby="recovered-permits-title">
    <h2 id="recovered-permits-title">Recovered Virginia permit documents</h2>
    <p className="data-page__note">{documents.length} independent document records with reviewed original page evidence. Each entry distinguishes the physical site, any inventory match and what the permit history establishes. Quoted conditions are not operating measurements.</p>
    {focusedRegistration && !documents.some((document) => document.registration === focusedRegistration) && <p role="status">That permit document is not in this release’s reviewed collection.</p>}
    {documents.map((document) => <RecoveredPermit key={document.registration} document={document} releaseId={releaseId} focused={document.registration === focusedRegistration} />)}
  </section>;
}
