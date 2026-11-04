import type { OperatorPayload } from '../types';

/**
 * Where the two faces meet.
 *
 * The public card says what a state caps. This panel asks what those caps mean
 * for a year of operation, and — the useful part — which cap runs out first. A
 * site permitted for 500 hours but given 684,127 gallons cannot reach 500 hours
 * at full load; the fuel cap limits it to roughly 164. An operator who reads
 * only the hours line would plan against a ceiling they cannot use.
 *
 * Every row is either arithmetic on two permit numbers or explicitly labelled an
 * estimate with its assumption shown.
 */
export function PermitEnvelope({ payload }: { payload: OperatorPayload }) {
  const env = payload.permit_envelope;
  // normaliseOperator passes the payload through untouched, so nothing
  // guarantees this block is well formed. A missing or malformed envelope must
  // render nothing rather than break the bench.
  if (!env || !Array.isArray(env.rows) || env.rows.length === 0) return null;

  return (
    <div className="envelope">
      <div className="envelope__head">
        <span className="envelope__k">THE PERMIT ENVELOPE</span>
        <span className={`envelope__tag${env.inconsistent ? ' envelope__tag--warn' : ''}`}>
          {env.inconsistent
            ? 'CAPS DO NOT RECONCILE — NO ENVELOPE CLAIMED'
            : env.binding === 'fuel'
              ? 'FUEL CAP BINDS FIRST'
              : env.binding === 'hours'
                ? 'HOURS CAP BINDS FIRST'
                : env.binding === 'neither'
                  ? 'BOTH CAPS COINCIDE'
                  /* binding is null when the payload carries only the ceiling
                     arithmetic and no fuel cap to compare against. The previous
                     badge defaulted to "HOURS CAP BINDS FIRST" in that case,
                     asserting a comparison that was never made. */
                  : 'CEILING ONLY — NO BINDING CAP COMPARED'}
        </span>
      </div>

      <div className="envelope__rows">
        {env.rows.map((r) => (
          <div className="envelope__row" key={r.label}>
            <span className="envelope__label">{r.label}</span>
            <span className="envelope__value">{r.value}</span>
            <span className="envelope__sub">{r.sub}</span>
          </div>
        ))}
      </div>

      <p className="envelope__note">{env.note}</p>
      {env.assumption ? (
        <p className="envelope__assume">
          The last row is an estimate and the only modelled figure here: {env.assumption}.
        </p>
      ) : null}
    </div>
  );
}
