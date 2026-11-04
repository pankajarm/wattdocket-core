import { useEffect, useRef } from 'react';
import type { Claim, Metric } from '../types';
import { isBandClaim, isNumericClaim, isNotPublishedClaim } from '../types';
import { badgeClass, BADGE_LABEL, compact, rangeText } from '../format';
import { HonestyBar } from './HonestyBar';
import { claimDateLabel } from '../dataPresentation';
import { EvidenceFreshness } from './EvidenceFreshness';

interface Props {
  claim: Claim;
  onOpen: (claim: Claim) => void;
  active?: boolean;
}

const METRIC_LABEL: Record<Metric, string> = {
  power_mw: 'Power draw',
  carbon_t: 'Carbon',
  water_gal: 'Water',
  diesel_mw: 'Backup diesel',
  diesel_hours: 'Diesel hours',
  land_acres: 'Land',
};

/**
 * The number chip — six slots, fixed order, all required:
 *   value · unit · p10–p90 range · badge · as-of · source count
 *
 * A claim that cannot render (INV-7) shows the blocked state and its reason in
 * the value slot instead of a number. A missing value keeps the numeric
 * typeface: "Not published", never blank.
 */
export function NumberChip({ claim, onOpen, active }: Props) {
  const ref = useRef<HTMLButtonElement>(null);

  // Render-time assertion (dev only): the six slots must exist in the DOM.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const node = ref.current;
    if (!node) return;
    const required = ['value', 'unit', 'range', 'badge', 'as-of', 'sources'];
    const missing = required.filter((slot) => !node.querySelector(`[data-slot="${slot}"]`));
    if (missing.length > 0) {
      console.error(
        `[NumberChip] claim ${claim.id} is missing required slot(s): ${missing.join(', ')}`,
      );
    }
  }, [claim.id]);

  const label = claim.label || METRIC_LABEL[claim.metric];
  const blocked = !claim.renderable;
  const kind = claim.value.kind;

  const tone =
    blocked ? 'red' : kind === 'BAND' ? 'amber' : claim.metric === 'diesel_mw' ? 'amber' : 'teal';

  let valueNode: React.ReactNode;
  let unitNode: React.ReactNode;
  let rangeNode: React.ReactNode;

  if (blocked) {
    valueNode = <span className="chip__blocked">Blocked</span>;
    unitNode = <small className="chip__unit">no receipt</small>;
    rangeNode = <span className="chip__why">{claim.blocked_reason ?? 'Claim is not renderable.'}</span>;
  } else if (isNumericClaim(claim)) {
    valueNode = <span className="chip__num">{compact(claim.value.value, claim.metric)}</span>;
    unitNode = <small className="chip__unit">{claim.value.unit}</small>;
    // A band with p10 == p90 is not a range: the value is a choice, not an
    // estimate. Printing "p10–p90 10 – 10" looks like a rendering fault and
    // invites the reader to look for uncertainty that was never claimed.
    const flat = claim.p10 === claim.p90;
    // A single point is either a figure we CHOSE (the reference load) or a
    // figure a document STATES (a permit's installed capacity). Calling the
    // second one a "reference value" is wrong, so the wording follows the
    // source: cited claims say exactly that, uncited ones admit they are ours.
    const cited = claim.citations.length > 0;
    rangeNode = (
      <span className={`chip__range${flat ? ' chip__range--flat' : ''}`}>
        {flat
          ? cited
            ? 'single figure stated by the source — no band claimed'
            : 'reference value — no uncertainty band claimed'
          : `${claim.range_kind === 'confidence' ? 'p10–p90' : claim.range_kind === 'scenario' || claim.scope === 'scenario' ? 'Scenario range' : 'Range'} ${rangeText(claim.p10, claim.p90, claim.metric)}`}
      </span>
    );
  } else if (isBandClaim(claim)) {
    valueNode = (
      <span className="chip__num chip__num--band">
        {compact(claim.value.low, claim.metric)}–{compact(claim.value.high, claim.metric)}
      </span>
    );
    unitNode = <small className="chip__unit">{claim.value.unit}</small>;
    rangeNode = <span className="chip__range">modelled band · no published scalar</span>;
  } else {
    valueNode = <span className="chip__num chip__num--missing">Not published</span>;
    unitNode = (
      <small className="chip__unit chip__unit--none" title="no unit: no published value">
        —
      </small>
    );
    rangeNode = <span className="chip__range">{claim.value.reason}</span>;
  }

  return (
    <button
      ref={ref}
      type="button"
      className={`chip${active ? ' chip--active' : ''}${blocked ? ' chip--blocked' : ''}`}
      onClick={() => onOpen(claim)}
      aria-label={`${label}: open source drawer`}
      data-metric={claim.metric}
    >
      <span className="chip__top">
        <span className="chip__label">{label}</span>
        <span className="chip__sources" data-slot="sources">
          {claim.source_count === 0
            ? 'no sources'
            : `${claim.source_count} ${claim.source_count === 1 ? 'source' : 'sources'} ↗`}
        </span>
      </span>

      <span className="chip__value" data-slot="value">
        {valueNode}
        <span data-slot="unit">{unitNode}</span>
      </span>

      <span className="chip__rangeline" data-slot="range">
        {rangeNode}
      {claim.unit_note && !isNotPublishedClaim(claim) && <span className="chip__scope-note">{claim.unit_note}</span>}
      <EvidenceFreshness evidence={claim} />
      </span>

      <span className="chip__bar">
        <HonestyBar
          p10={claim.p10}
          p50={claim.p50}
          p90={claim.p90}
          band={isBandClaim(claim) ? { low: claim.value.low, high: claim.value.high } : null}
          state={blocked ? 'blocked' : kind === 'NOT_PUBLISHED' ? 'missing' : kind === 'BAND' ? 'band' : 'value'}
          tone={tone}
          rangeKind={claim.range_kind}
          labelFor={(v) => compact(v, claim.metric)}
        />
      </span>

      <span className="chip__foot">
        <span className="chip__badges" data-slot="badge">
          {blocked ? (
            <span className="badge badge--blocked">blocked</span>
          ) : kind === 'NOT_PUBLISHED' ? (
            <span className="badge badge--none">unavailable</span>
          ) : (
            <>
              <span className={badgeClass(claim.badge)}>{BADGE_LABEL[claim.badge]}</span>
              {claim.provisional && <span className="badge badge--provisional">provisional</span>}
            </>
          )}
        </span>
        <span className="chip__asof" data-slot="as-of">
          {claimDateLabel(claim)}
        </span>
      </span>
    </button>
  );
}
