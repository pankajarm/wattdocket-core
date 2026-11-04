import { useEffect, useState } from 'react';
import type { FacilityPayload } from '../types';
import { getExplain } from '../api';
import type { ExplainResult } from '../api';

/**
 * The one place a model writes the copy the reader sees.
 *
 * Two properties matter more than the prose:
 *   - it never blocks the card: the template renders immediately, and the model
 *     sentence replaces it only if it arrives and verifies;
 *   - when it does not arrive, the card says "template" out loud instead of
 *     quietly presenting the same sentence as though a model wrote it.
 */
/** `**like this**` marks the numbers that trace to a claim slot. */
function Emphasis({ text }: { text: string }) {
  const parts = text.split('**');
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? <b key={i}>{part}</b> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

export function ExplainPanel({
  payload,
  template,
  enabled,
}: {
  payload: FacilityPayload;
  template: string;
  enabled: boolean;
}) {
  const [state, setState] = useState<ExplainResult | null>(null);
  const [pending, setPending] = useState(false);
  const [asked, setAsked] = useState(false);

  // A new facility means the old sentence is about the wrong place.
  useEffect(() => {
    setState(null);
    setAsked(false);
    setPending(false);
  }, [payload.facility.id]);

  async function ask() {
    setAsked(true);
    setPending(true);
    const res = await getExplain(payload, template);
    setState(res);
    setPending(false);
  }

  if (!enabled) return null;

  return (
    <div className="explain">
      <div className="explain__head">
        <span className="explain__k">IN PLAIN ENGLISH</span>
        {state ? (
          <span className={`explain__badge explain__badge--${state.source}`}>
            {state.source === 'model' ? 'MUSE SPARK · NUMERALS CHECKED' : 'TEMPLATE FALLBACK'}
          </span>
        ) : null}
      </div>

      {state ? (
        <p className="explain__text">
          <Emphasis text={state.text} />
        </p>
      ) : (
        <p className="explain__text explain__text--template">
          <Emphasis text={template} />
        </p>
      )}

      <div className="explain__foot">
        <button className="ghostbtn ghostbtn--sm" onClick={ask} disabled={pending}>
          {pending ? 'Asking…' : asked ? 'Ask again' : 'Explain this card'}
        </button>
        {state?.degraded ? (
          <span className="explain__note">
            {state.reason === 'DAILY_QUOTA_EXHAUSTED'
              ? 'Today’s explanation limit has been reached; it resets at midnight UTC. Showing the template.'
              : state.reason === 'QUOTA_UNAVAILABLE'
                ? 'The explanation service is temporarily unavailable; showing the template.'
                : state.reason === 'UNKNOWN_FACILITY'
                  ? 'No reviewed model facts for this facility yet; showing the template.'
                  : `Model unavailable (${state.reason ?? 'unknown'}) — showing the template.`}
          </span>
        ) : state?.source === 'model' ? (
          <span className="explain__note">
            Numerals are checked against the source facts; review the wording and sources.
          </span>
        ) : null}
      </div>
    </div>
  );
}
