import { useId } from 'react';

export type BarTone = 'teal' | 'amber' | 'blue' | 'red' | 'gray';

interface Props {
  /** Numeric band. When absent, the bar renders a missing/blocked state. */
  p10?: number | null;
  p50?: number | null;
  p90?: number | null;
  /** For BAND claims (water), a low/high pair with no centre. */
  band?: { low: number; high: number } | null;
  state?: 'value' | 'band' | 'missing' | 'blocked';
  tone?: BarTone;
  /** Drawer variant: prints the p10 / p50 / p90 numerals under the bar. */
  showScale?: boolean;
  labelFor?: (v: number) => string;
  rangeKind?: 'scenario' | 'confidence' | 'reported' | null;
}

const TONES = Object.fromEntries(['teal', 'amber', 'blue', 'red', 'gray'].map((tone) => {
  const color = `var(--${tone === 'gray' ? 'muted' : tone})`;
  return [tone, { band: color, bandTo: color, mark: color }];
})) as Record<BarTone, { band: string; bandTo: string; mark: string }>;

/**
 * THE HONESTY BAR.
 *
 * One stamped unit: the p10–p90 band, the p50 tick, the edge caps, and — via the
 * chip that wraps it — the provenance badge. The band is drawn on a padded
 * domain (18% of the span on each side) so a wide band and a narrow band read as
 * different shapes instead of both filling the slot.
 *
 * Nothing here rounds: positions are computed from the raw numbers.
 */
export function HonestyBar({
  p10 = null,
  p50 = null,
  p90 = null,
  band = null,
  state,
  tone = 'teal',
  showScale = false,
  labelFor,
  rangeKind = 'scenario',
}: Props) {
  const uid = useId().replace(/[:]/g, '');
  const resolvedState = state ?? (p10 != null && p90 != null ? 'value' : band ? 'band' : 'missing');
  const colors = TONES[tone];

  const low = band ? band.low : p10;
  const high = band ? band.high : p90;
  const hasGeometry = low != null && high != null && high > low;
  const pointOnly = resolvedState === 'value' && !hasGeometry;

  const scale = 100;
  const pad = hasGeometry ? (high! - low!) * 0.18 : 0;
  const domainLo = hasGeometry ? low! - pad : 0;
  const domainHi = hasGeometry ? high! + pad : 1;
  const x = (v: number) => ((v - domainLo) / (domainHi - domainLo)) * scale;

  const bandLeft = hasGeometry ? x(low!) : 19;
  const bandRight = hasGeometry ? x(high!) : 81;
  const tickX = resolvedState === 'value' && p50 != null ? (pointOnly ? 50 : x(p50)) : null;

  const fmt = labelFor ?? ((v: number) => String(Math.round(v)));

  return (
    <div
      className="honesty"
      data-state={resolvedState}
      data-tone={tone}
      role="img"
      aria-label={
        pointOnly
          ? `Single value ${p50 != null ? fmt(p50) : low != null ? fmt(low) : 'available'}, no uncertainty range`
          : resolvedState === 'value'
          ? `${rangeKind === 'confidence' ? '10th–90th percentile range' : rangeKind === 'reported' ? 'Reported range' : 'Scenario range'} ${fmt(low!)} to ${fmt(high!)}, central value ${p50 != null ? fmt(p50) : 'n/a'}`
          : resolvedState === 'band'
            ? `Modelled band ${fmt(low!)} to ${fmt(high!)}, no single value`
            : resolvedState === 'blocked'
              ? 'Blocked — no renderable range'
              : 'Not available in our sources — no range'
      }
    >
      <svg
        className="honesty__svg"
        viewBox="0 0 100 20"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={`hb-${uid}`} x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor={colors.band} stopOpacity="0.48" />
            <stop offset="100%" stopColor={colors.bandTo} stopOpacity="0.22" />
          </linearGradient>
          <pattern
            id={`hb-hatch-${uid}`}
            width="3"
            height="3"
            patternTransform="rotate(45)"
            patternUnits="userSpaceOnUse"
          >
            <line x1="0" y1="0" x2="0" y2="3" stroke="var(--red)" strokeWidth="0.7" />
          </pattern>
        </defs>

        {/* track: the domain itself, so the reader can see how much of it the band covers */}
        <rect className="honesty__track" x="0" y="8" width="100" height="4" rx="2" />

        {resolvedState === 'blocked' ? (
          <rect
            x={bandLeft}
            y="8"
            width={bandRight - bandLeft}
            height="4"
            rx="2"
            fill={`url(#hb-hatch-${uid})`}
          />
        ) : !pointOnly ? (
          <rect
            className="honesty__band"
            x={bandLeft}
            y="7.5"
            width={Math.max(bandRight - bandLeft, 1)}
            height="5"
            rx="2.5"
            fill={
              resolvedState === 'missing'
                ? 'var(--track)'
                : `url(#hb-${uid})`
            }
          />
        ) : null}

        {resolvedState === 'value' && !pointOnly && (
          <>
            <line
              className="honesty__edge"
              x1={bandLeft}
              x2={bandLeft}
              y1="5.5"
              y2="14.5"
              stroke={colors.mark}
              strokeOpacity="0.6"
              strokeWidth="0.5"
            />
            <line
              className="honesty__edge"
              x1={bandRight}
              x2={bandRight}
              y1="5.5"
              y2="14.5"
              stroke={colors.mark}
              strokeOpacity="0.6"
              strokeWidth="0.5"
            />
            {tickX != null && (
              <line
                className="honesty__tick"
                x1={tickX}
                x2={tickX}
                y1="3"
                y2="17"
                stroke={colors.mark}
                strokeWidth="0.8"
              />
            )}
          </>
        )}

        {pointOnly && <line className="honesty__tick" x1="50" x2="50" y1="3" y2="17" stroke={colors.mark} strokeWidth="0.8" />}

        {resolvedState === 'band' && (
          <>
            <line x1={bandLeft} x2={bandLeft} y1="5.5" y2="14.5" stroke={colors.mark} strokeOpacity="0.45" strokeWidth="0.4" />
            <line x1={bandRight} x2={bandRight} y1="5.5" y2="14.5" stroke={colors.mark} strokeOpacity="0.45" strokeWidth="0.4" />
            <line x1={bandLeft} x2={bandRight} y1="10" y2="10" stroke={colors.mark} strokeOpacity="0.25" strokeWidth="0.4" strokeDasharray="1.5 1.5" />
          </>
        )}

        {resolvedState === 'missing' && (
          <line
            x1="19"
            x2="81"
            y1="10"
            y2="10"
            stroke="var(--muted)"
            strokeWidth="0.4"
            strokeDasharray="2 2"
          />
        )}
      </svg>

      {showScale && (
        <div className="honesty__scale" aria-hidden="true">
          <span>{rangeKind === 'confidence' ? 'p10' : 'Low'} {low != null ? fmt(low) : '—'}</span>
          <span className="honesty__p50">
            {resolvedState === 'value' && p50 != null
              ? `${rangeKind === 'confidence' ? 'p50' : 'Central'} ${fmt(p50)}`
              : resolvedState === 'band'
                ? 'no single value'
                : resolvedState === 'blocked'
                  ? 'blocked'
                  : 'not published'}
          </span>
          <span>{rangeKind === 'confidence' ? 'p90' : 'High'} {high != null ? fmt(high) : '—'}</span>
        </div>
      )}
    </div>
  );
}
