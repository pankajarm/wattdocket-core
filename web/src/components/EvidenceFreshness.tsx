import type { EvidenceRetrieval } from '../types';
import { dateLabel } from '../dataPresentation';

export function EvidenceFreshness({ evidence, detailed = false }: { evidence: EvidenceRetrieval; detailed?: boolean }) {
  if (!evidence.freshness && !evidence.archived_by_at && !evidence.latest_fetch_status) return null;
  const label = evidence.freshness === 'stale' ? 'Last verified document; update delayed'
    : evidence.freshness === 'archived' ? 'Archived evidence'
    : evidence.freshness === 'cached' ? 'Cached document; no new document check'
    : 'Document retrieved';
  return <span className="evidence-freshness" data-state={evidence.freshness}>
    <strong>{label}.</strong>{' '}{evidence.retrieved_at ? `Original retrieval: ${dateLabel(evidence.retrieved_at)}.` : 'Original retrieval date unavailable.'}
    {evidence.archived_by_at && <> Present in the archive by {dateLabel(evidence.archived_by_at)}.</>}
    {evidence.latest_fetch_status === 'failed' && <> Latest document refresh failed{evidence.latest_fetch_at ? ` on ${dateLabel(evidence.latest_fetch_at)}` : ''}; no newer evidence is asserted.</>}
    {detailed && evidence.representation && <> Source representation: {evidence.representation.replace(/_/g, ' ')}.</>}
    {detailed && evidence.page_derivation && <> Page locator: {evidence.page_derivation}.</>}
    {detailed && evidence.latest_fetch_error && <span className="evidence-freshness__error">Collection error: {evidence.latest_fetch_error}</span>}
  </span>;
}
