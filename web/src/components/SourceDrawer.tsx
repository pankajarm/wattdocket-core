import { useEffect, useRef, useState } from 'react';
import type { Claim, DrawerPayload } from '../types';
import { getDrawer } from '../api';
import { badgeClass, BADGE_LABEL, shortHash } from '../format';
import { claimDateLabel, dateLabel, safeSourceUrl } from '../dataPresentation';
import { EvidenceFreshness } from './EvidenceFreshness';

interface Props {
  facilityId: string;
  claim: Claim | null;
  onClose: () => void;
  onOperator: () => void;
}

/** Wraps the verified substring in <mark>; refuses to invent a highlight. */
function Quote({ quote, matched, notPublished }: { quote: string; matched: string; notPublished: boolean }) {
  if (!quote) {
    return (
      <span className="quote--none">
        {notPublished
          ? 'No citation is available for this metric and record in this dataset.'
          : 'No citation was supplied for this figure.'}
      </span>
    );
  }
  if (!matched) return <>{quote}</>;
  const at = quote.indexOf(matched);
  if (at < 0) {
    return (
      <>
        <span className="quote__blocked">{quote}</span>
        <span className="quote__fail">highlight target not found in quote</span>
      </>
    );
  }
  return (
    <>
      {quote.slice(0, at)}
      <mark>{matched}</mark>
      {quote.slice(at + matched.length)}
    </>
  );
}

export function SourceDrawer({ facilityId, claim, onClose, onOperator }: Props) {
  const [state, setState] = useState<
    | { status: 'idle' }
    | { status: 'loading' }
    | { status: 'ready'; drawer: DrawerPayload; fixture: boolean }
    | { status: 'error'; message: string }
  >({ status: 'idle' });
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!claim) {
      setState({ status: 'idle' });
      return;
    }
    let cancelled = false;
    setState({ status: 'loading' });
    getDrawer(facilityId, claim.id)
      .then((res) => {
        if (!cancelled) {
          setState({ status: 'ready', drawer: res.data, fixture: res.source === 'fixture' });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', message: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [claim, facilityId]);

  useEffect(() => {
    if (!claim) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [claim, onClose]);

  const open = claim != null;
  const drawer = state.status === 'ready' ? state.drawer : null;
  const blocked = claim != null && !claim.renderable;
  // Older snapshots include these two literal sensitivities even though no
  // calculation or source supports them (see docs/board/HK6-number-audit.md).
  const sensitivities = drawer?.sensitivity.filter((item) => !(
    (item.label === 'marginal instead of average' && item.delta === '+38%') ||
    (item.label === 'at 100 hr/yr per unit (TCEQ emergency limit)' && /0\.7% of annual grid MWh/.test(item.delta))
  )) ?? [];

  return (
    <>
      <div className={`drawer-scrim${open ? ' drawer-scrim--on' : ''}`} onClick={onClose} aria-hidden="true" />
      <aside
        className={`drawer${open ? ' drawer--open' : ''}`}
        aria-hidden={!open}
        aria-label="Source drawer"
        ref={panelRef}
      >
        {claim && (
          <>
            <div className="drawer__top">
              <span className="drawer__kicker">Source drawer · {drawerWord(claim.metric)}</span>
              <button type="button" className="drawer__close" onClick={onClose} aria-label="Close source drawer">
                ✕
              </button>
            </div>

            {state.status === 'loading' && <DrawerSkeleton />}

            {state.status === 'error' && (
              <div className="drawer__error">
                <div className="drawer__errorh">Drawer unavailable</div>
                <p>{state.message}</p>
              </div>
            )}

            {drawer && (
              <>
                <div className="drawer__num">
                  {claim.value.kind === 'NOT_PUBLISHED' ? 'Not available in this dataset' : drawer.headline}
                  {drawer.unit && claim.value.kind !== 'NOT_PUBLISHED' && <> <small>{drawer.unit}</small></>}
                </div>
                <div className="drawer__meta">
                  <span
                    className={
                      blocked
                        ? 'badge badge--blocked'
                        : claim.value.kind === 'NOT_PUBLISHED'
                          ? 'badge badge--none'
                          : badgeClass(drawer.badge)
                    }
                  >
                    {blocked
                      ? 'blocked'
                      : claim.value.kind === 'NOT_PUBLISHED'
                        ? 'unavailable'
                        : BADGE_LABEL[drawer.badge]}
                  </span>
                  {drawer.provisional && <span className="badge badge--provisional">provisional</span>}
                  <span className="drawer__metatext">
                    {claim.value.kind === 'NOT_PUBLISHED'
                      ? 'no value available in this dataset'
                      : drawer.p10 != null && drawer.p90 != null && drawer.p10 !== drawer.p90
                        ? `${claim.range_kind === 'confidence' ? 'p10–p90' : claim.scope === 'scenario' || claim.range_kind === 'scenario' ? 'Scenario range' : 'Range'} ${fmt(drawer.p10)}–${fmt(drawer.p90)}`
                        : claim.value.kind === 'BAND'
                          ? 'modelled band · no scalar'
                          : 'no range'}
                    {' · '}{claimDateLabel(claim)} ·{' '}
                    {drawer.receipt_count} {drawer.receipt_count === 1 ? 'receipt' : 'receipts'}
                  </span>
                </div>
                {claim.scope === 'scenario' && <p className="drawer__metatext">Reference scenario: this number is calculated from chosen assumptions, not a measurement of this facility.</p>}
                {claim.unit_note && <p className="drawer__metatext">{claim.unit_note}</p>}
                <EvidenceFreshness evidence={drawer.citation.freshness ? drawer.citation : claim} detailed />
                {(claim.retrieved_at || drawer.retrieved_at) && <p className="drawer__metatext">Source retrieved {dateLabel(claim.retrieved_at ?? drawer.retrieved_at, true)}</p>}

                {blocked && claim.blocked_reason && (
                  <div className="drawer__blocked">
                    <b>Not rendered.</b> {claim.blocked_reason}
                  </div>
                )}

                <section className="dblock">
                  <h3 className="dblock__h">Source evidence</h3>
                  <blockquote className="quote">
                    “<Quote quote={drawer.citation.quote} matched={drawer.citation.matched_text} notPublished={claim.value.kind === 'NOT_PUBLISHED'} />”
                  </blockquote>
                  <div className="quote__src">
                    {drawer.citation.publisher} · {drawer.citation.document_id}
                    {drawer.citation.page != null ? ` · p.${drawer.citation.page}` : ''}
                    {drawer.citation.line ? ` line ${drawer.citation.line}` : ''}
                  </div>
                  {safeSourceUrl(drawer.citation.url) && <a href={safeSourceUrl(drawer.citation.url)} target="_blank" rel="noreferrer">Open source evidence ↗</a>}
                  <div
                    className={`verify${
                      drawer.citation.quote === '' ? ' verify--none' : drawer.citation.verified ? '' : ' verify--fail'
                    }`}
                  >
                    {drawer.citation.quote === ''
                      ? '— no receipt attached'
                      : drawer.citation.verified && drawer.citation.document_sha256
                        ? `Artifact check passed · sha256 ${shortHash(drawer.citation.document_sha256)} · ${drawer.citation.verifier_version}`
                        : drawer.citation.verified ? 'Document fingerprint not supplied; artifact verification is incomplete.'
                      : `✕ substring check failed · sha256 ${shortHash(drawer.citation.document_sha256)} — receipt rejected`}
                  </div>
                  {drawer.extra_citations.length > 0 && (
                    <ul className="quote__more">
                      {drawer.extra_citations.map((c) => (
                        <li key={c.id}>
                          <span className={c.verified ? 'dot dot--ok' : 'dot dot--bad'} />
                          {c.publisher} · {c.document_id}
                          {c.page != null ? ` · p.${c.page}` : ''}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className="dblock">
                  <h3 className="dblock__h">Calculation steps</h3>
                  {drawer.chain.steps.length === 0 && (
                    <p className="chain__empty">
                      {claim.value.kind === 'NOT_PUBLISHED'
                        ? 'No calculation: the required value is not available in this dataset.'
                        : 'No calculation steps were supplied for this figure.'}
                    </p>
                  )}
                  <ol className="chain">
                    {drawer.chain.steps.map((s, i) => (
                      <li key={i} className={`chain__step chain__step--${s.kind}`}>
                        <span className="chain__n">{String(i + 1).padStart(2, '0')}</span>
                        <span className="chain__line">{s.line}</span>
                      </li>
                    ))}
                  </ol>
                  <div className="chain__foot">
                    {drawer.chain.calc_id} · {drawer.chain.name}@{drawer.chain.version}
                    {drawer.chain.code_sha256 && <> · code {shortHash(drawer.chain.code_sha256)}</>}
                  </div>
                </section>

                <section className="dblock">
                  <h3 className="dblock__h">Unit conversions</h3>
                  {drawer.transforms.length === 0 && (
                    <p className="chain__empty">No separate unit-conversion details were supplied.</p>
                  )}
                  {drawer.transforms.map((t) => (
                    <div className="row" key={t.label}>
                      <span>{t.label}</span>
                      <b>{t.value}</b>
                    </div>
                  ))}
                </section>

                <section className="dblock">
                  <h3 className="dblock__h">Sensitivity</h3>
                  {sensitivities.length === 0 && (
                    <p className="chain__empty">No sensitivity details were supplied for this claim.</p>
                  )}
                  {sensitivities.map((s) => (
                    <div className="row" key={s.label}>
                      <span>{s.label}</span>
                      <b className={`delta delta--${s.tone}`}>{s.delta}</b>
                    </div>
                  ))}
                </section>

                <section className="dblock dblock--drivers">
                  <h3 className="dblock__h">Assumptions &amp; limitations</h3>
                  {drawer.drivers.length === 0 ? (
                    <p className="chain__empty">No additional uncertainty drivers were supplied for this claim.</p>
                  ) : (
                    <div className="drivers">
                      {drawer.drivers.map((d) => (
                        <span className="driver" key={d}>
                          {d.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  )}
                  <button type="button" className="linkbtn" onClick={onOperator}>
                    Open the operator bench →
                  </button>
                </section>
              </>
            )}
          </>
        )}
      </aside>
    </>
  );
}

function DrawerSkeleton() {
  return (
    <div className="skel">
      <div className="skel__line skel__line--num" />
      <div className="skel__line skel__line--meta" />
      {[0, 1, 2].map((i) => (
        <div className="skel__block" key={i}>
          <div className="skel__line skel__line--h" />
          <div className="skel__line" />
          <div className="skel__line skel__line--short" />
        </div>
      ))}
    </div>
  );
}

const DRAWER_WORD: Record<string, string> = {
  carbon_t: 'carbon',
  power_mw: 'power',
  water_gal: 'water',
  diesel_mw: 'diesel',
  diesel_hours: 'diesel hours',
  land_acres: 'land',
};

function drawerWord(metric: string): string {
  return DRAWER_WORD[metric] ?? metric.replace(/_/g, ' ');
}

function fmt(v: number): string {
  return v >= 10_000 ? Math.round(v).toLocaleString('en-US') : v.toFixed(v % 1 === 0 ? 0 : 1);
}
