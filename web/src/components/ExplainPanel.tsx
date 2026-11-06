import { useEffect, useId, useRef, useState } from 'react';
import type { FacilityPayload } from '../types';
import { getExplain } from '../api';
import type { ExplainRequest, ExplainResult, ExplainSubject } from '../api';
import './explain-panel.css';

/** Model prose is text, never executable HTML or model-provided links. */
function Emphasis({ text }: { text: string }) {
  return <>{text.split('**').map((part, i) => i % 2 ? <b key={i}>{part}</b> : <span key={i}>{part}</span>)}</>;
}

function fallbackNote(reason?: string) {
  if (reason === 'DAILY_QUOTA_EXHAUSTED') return 'Today’s explanation limit has been reached; it resets at midnight UTC. Showing the source summary.';
  if (reason === 'QUOTA_UNAVAILABLE') return 'The explanation service is temporarily unavailable; showing the source summary.';
  if (reason === 'CLIENT_UPDATE_REQUIRED') return 'This page needs an update to match an explanation to its published data. Reload the page to continue.';
  if (reason === 'EXPLAIN_NOT_IN_RELEASE') return 'This published release does not include explanation evidence. Reload to check a newer release.';
  if (reason === 'REGISTRY_UNAVAILABLE') return 'The published explanation evidence is temporarily unavailable. Showing the source summary; you can try again.';
  if (reason === 'REQUEST_DEADLINE') return 'The service could not start an explanation in time. Showing the source summary; you can try again.';
  if (reason === 'UNKNOWN_FACILITY' || reason === 'UNKNOWN_SUBJECT') return 'This reporting subject has no reviewed explanation facts in this release. The source data remains available above.';
  if (reason === 'RELEASE_UNAVAILABLE' || reason === 'UNKNOWN_RELEASE' || reason === 'RELEASE_NOT_FOUND') return 'An explanation is unavailable for this data release. Reload to check the latest published release.';
  if (reason === 'EVIDENCE_MISMATCH' || reason === 'INVALID_RESPONSE') return 'The explanation did not match the displayed evidence. Showing the source summary; try again or inspect the sources.';
  return 'The explanation could not be loaded. Showing the source summary; you can try again.';
}

export function ExplainPanel({ payload, subject, releaseId, template, enabled = true,
  actionLabel = 'Explain this card', scopeNote, title = 'Explain the published evidence' }: {
  payload?: FacilityPayload;
  subject?: ExplainSubject;
  releaseId?: string;
  template: string;
  enabled?: boolean;
  actionLabel?: string;
  scopeNote?: string;
  title?: string;
}) {
  const target = subject ?? {kind: 'facility' as const, id: payload?.facility.id ?? ''};
  const request: ExplainRequest = {release_id: releaseId ?? payload?.release_id ?? '', subject: target};
  const key = JSON.stringify(request);
  const liveKey = useRef(key);
  liveKey.current = key;
  const running = useRef<{key: string; controller: AbortController} | null>(null);
  const [answer, setAnswer] = useState<{key: string; result: ExplainResult} | null>(null);
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [askedKey, setAskedKey] = useState<string | null>(null);
  const headingId = useId();
  const noteId = useId();
  const state = answer?.key === key ? answer.result : null;
  const pending = pendingKey === key;
  const available = Boolean(request.release_id && target.id);

  useEffect(() => {
    setAnswer(null); setPendingKey(null); setAskedKey(null);
    return () => { running.current?.controller.abort(); running.current = null; };
  }, [key]);

  async function ask() {
    if (!available || running.current?.key === key) return;
    const invocation = {key, controller: new AbortController()};
    running.current = invocation;
    setAskedKey(key); setPendingKey(key);
    try {
      const result = await getExplain(request, template, {signal: invocation.controller.signal});
      if (running.current !== invocation || liveKey.current !== key || invocation.controller.signal.aborted) return;
      if (result.release_id !== request.release_id || result.subject?.kind !== target.kind || result.subject.id !== target.id) {
        setAnswer({key, result: {text: template, source: 'template', degraded: true, reason: 'EVIDENCE_MISMATCH'}});
      } else setAnswer({key, result});
    } catch {
      if (running.current === invocation && liveKey.current === key && !invocation.controller.signal.aborted) {
        setAnswer({key, result: {text: template, source: 'template', degraded: true, reason: 'unreachable'}});
      }
    } finally {
      if (running.current === invocation) { running.current = null; setPendingKey(null); }
    }
  }

  if (!enabled) return null;
  return <section className="explain" aria-labelledby={headingId}>
    <div className="explain__head">
      <h4 id={headingId} className="explain__title">{title}</h4>
      {state && <span className={`explain__badge explain__badge--${state.source}`}>
        {state.source === 'model' ? 'MUSE SPARK · NUMERALS & SOURCES CHECKED' : 'TEMPLATE FALLBACK'}
      </span>}
    </div>
    <p id={noteId} className="explain__scope">{scopeNote ?? 'Explains this published record and linked evidence, including any published reference scenario. Calculator changes are not included.'}</p>
    <div aria-live="polite" aria-atomic="true">
      <p className={`explain__text${state ? '' : ' explain__text--template'}`}><Emphasis text={state?.text ?? template}/></p>
    </div>
    {!!state?.citations?.length && <details className="explain__sources" open>
      <summary>Sources used in this explanation</summary>
      <ul>{state.citations.map((citation, index) => <li key={`${citation.fact_id}:${index}`}>
        <a href={citation.url} target="_blank" rel="noreferrer">{citation.label || 'Original source'}</a>
        {citation.locator && <span> · {citation.locator}</span>}
        <details><summary>Source fingerprint</summary><code>{citation.sha256}</code></details>
      </li>)}</ul>
    </details>}
    {!!state?.cited_facts?.length && <details className="explain__sources">
      <summary>Compare with the published evidence</summary>
      <ul>{state.cited_facts.map(fact => <li key={fact.id}><p>{fact.text}</p>
        {(state.citations ?? []).filter(citation => citation.fact_id === fact.id).map((citation, i) => <p key={i}><a href={citation.url} target="_blank" rel="noreferrer">{citation.label || 'Original source'}</a> · {citation.locator}</p>)}
      </li>)}</ul>
    </details>}
    <div className="explain__foot">
      <button type="button" className="ghostbtn ghostbtn--sm" onClick={ask} disabled={pending || !available} aria-describedby={noteId}>
        {pending ? 'Preparing explanation…' : askedKey === key ? 'Explain again' : actionLabel}
      </button>
      {pending && <span className="explain__note" role="status">Reading the published evidence…</span>}
      {!available ? <span className="explain__note">A published release is required for this explanation.</span>
        : state?.degraded ? <span className="explain__note">{fallbackNote(state.reason)}</span>
          : state?.source === 'model' ? <span className="explain__note">Numbers, citations and required scope qualifiers are checked. Review the wording against the published evidence.</span> : null}
    </div>
  </section>;
}
