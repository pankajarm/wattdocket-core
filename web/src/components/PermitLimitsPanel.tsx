import type { FacilityPayload } from '../types';
import { dateLabel, safeSourceUrl } from '../dataPresentation';
import { EvidenceFreshness } from './EvidenceFreshness';

/**
 * What Virginia legally caps for this site.
 *
 * The single most important word on this panel is "permitted". These are limits
 * the Commonwealth imposes, not measurements of what the site did. A reader who
 * merges the two would conclude a facility runs its generators 100 hours — when
 * 100 hours is merely the ceiling. So the heading says PERMITTED LIMITS, every
 * row is phrased as a cap ("no more than"), and the footnote says outright that
 * nothing here is an operating record.
 */
const LABELS: Record<string, string> = {
  // Virginia caps these separately, and the difference matters: 17 hours of
  // scheduled testing plus a separate 500-hour total is not a 17-hour site.
  mcrt_hours_limit: 'Maintenance & testing hours',
  generator_hours_limit: 'Quoted operating-hours condition',
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

export function PermitLimitsPanel({ payload }: { payload: FacilityPayload }) {
  const raw = payload.facility.permit_limits;
  if (!raw || raw.length === 0) return null;
  const ORDER = ['mcrt_hours_limit', 'generator_hours_limit', 'fuel_throughput_limit',
                 'fuel_sulfur_limit'];
  const limits = [...raw].sort(
    (a, b) => ORDER.indexOf(a.metric) - ORDER.indexOf(b.metric),
  );

  const viaProxy = limits.some((l) => (l.retrieved_via ?? '').includes('r.jina.ai'));
  const registrations = [...new Set(limits.map((l) => l.registration).filter(Boolean))];

  return (
    <div className="limits">
      <div className="limits__head">
        <span className="limits__k">QUOTED PERMIT CONDITIONS</span>
        <span className="limits__no">
          Virginia DEQ permit {registrations.join(', ')}
        </span>
      </div>

      <ul className="limits__rows">
        {limits.map((l, index) => (
          <li key={`${l.registration}-${l.metric}-${l.page}-${index}`} className="limits__condition">
            <span className="limits__what">{LABELS[l.metric] ?? l.metric}</span>
            <span className="limits__val">
              {CAPS[l.metric] ?? 'no more than'} <b>{fmt(l.metric, l.value)}</b>{' '}
              {l.unit}
            </span>
            {l.page ? <span className="limits__pg">p.{l.page}</span> : null}
            <div className="limits__scope">{l.applies_to_entire_site === true ? 'The source explicitly identifies this as a whole-site condition.' : 'Applies only to the equipment or activity in the quoted condition; a whole-site limit is not established.'}
              <p>{l.program ? `Program: ${l.program}. ` : ''}Document issued: {dateLabel(l.issued_at)}. {l.document_status ? `Document state: ${l.document_status.replace(/_/g, ' ')}. ` : ''}Current effectiveness: {l.effective_status?.replace(/_/g, ' ') || 'not verified'}.</p>
              {l.supersedes && l.supersedes.length > 0 && <p>Supersedes: {l.supersedes.join(', ')}.</p>}
              <EvidenceFreshness evidence={l} />
              {l.quote && <details><summary>Read the permit condition</summary><blockquote>{l.quote}</blockquote>{l.page && <span>Page {l.page}. </span>}{safeSourceUrl(l.url) && <a href={safeSourceUrl(l.url)} target="_blank" rel="noreferrer">Open source document ↗</a>}</details>}
            </div>
          </li>
        ))}
      </ul>

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
