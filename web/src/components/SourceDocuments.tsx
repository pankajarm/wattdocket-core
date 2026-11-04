import type { FacilityDocument } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';

export function SourceDocuments({ documents }: { documents?: FacilityDocument[] }) {
  if (!documents?.length) return null;
  return <section className="evidence-section" aria-labelledby="source-documents-heading"><h2 id="source-documents-heading" className="evidence-section__heading">Source documents</h2>
    <p className="evidence-section__note">A document can describe a proposal or regulatory review. Its statements remain separate from measured facility data and current permit limits.</p>
    {documents.map((document) => <article className="source-document" key={document.id}>
      <h3>{safeSourceUrl(document.url) ? <a href={safeSourceUrl(document.url)} target="_blank" rel="noreferrer">{document.name} ↗</a> : document.name}</h3>
      <p>{document.publisher} · {document.document_type.replace(/_/g, ' ')}{document.permit_number ? ` · Permit ${document.permit_number}` : ''}<br />Document date: {dateLabel(document.document_date)} · Retrieved {dateLabel(document.retrieved_at, true)}</p>
      <p>{document.note || 'Current effectiveness has not been verified.'}</p>
      {document.statements.length > 0 && <details><summary>Read {document.statements.length} verified document {document.statements.length === 1 ? 'statement' : 'statements'}</summary>{document.statements.map((statement, index) => <div className="source-document__statement" key={`${statement.page}-${index}`}><blockquote>{statement.quote}</blockquote><p>Page {statement.page}. This is a statement in the document; it is not presented as a current operating limit.</p></div>)}</details>}
      <span className="source-document__hash">Source artifact SHA-256 {document.content_sha256}</span>
    </article>)}
  </section>;
}
