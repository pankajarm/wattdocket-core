import type { FacilityPayload } from '../types';

/**
 * What a state air permit says is physically on this site.
 *
 * This panel exists because of one specific dishonesty the card used to commit:
 * it answered "backup diesel: not published" for every facility, including ones
 * whose state permit names every generator, its rating, and the page it is on.
 * "Not published" was true of the registries we had loaded and false about the
 * world. When a permit has been read, this replaces that answer.
 */
export function PermitPanel({ payload }: { payload: FacilityPayload }) {
  const permit = payload.facility.permit;
  if (!permit || !permit.facts?.length) return null;

  const count = permit.facts.find((f) => f.metric === 'backup_generator_count');
  const rating = permit.facts.find((f) => f.metric === 'generator_unit_rating_kw');
  const diesel = payload.claims.find((c) => c.metric === 'diesel_mw');

  return (
    <div className="permitpanel">
      <div className="permitpanel__head">
        <span className="permitpanel__k">WHAT THE PERMIT SAYS</span>
        <span className="permitpanel__no">
          {permit.authority} permit {permit.number}
        </span>
      </div>

      <ul className="permitpanel__facts">
        {count ? (
          <li>
            <b>{count.value.toLocaleString('en-US')}</b> diesel emergency generators
            {count.page ? <span className="permitpanel__pg"> p.{count.page}</span> : null}
          </li>
        ) : null}
        {rating ? (
          <li>
            each rated above <b>{rating.value.toLocaleString('en-US')} kW</b> — so the
            capacity below is a floor, not a figure
            {rating.page ? <span className="permitpanel__pg"> p.{rating.page}</span> : null}
          </li>
        ) : null}
        {typeof diesel?.value === 'object' && diesel.value.kind === 'NUMERIC' ? (
          <li>
            together at least{' '}
            <b>{diesel.value.value.toLocaleString('en-US', { maximumFractionDigits: 0 })} MW</b>{' '}
            of backup diesel — installed capacity, not an emission
          </li>
        ) : null}
      </ul>

      <div className="permitpanel__foot">
        matched on {permit.match ?? 'address'}
        {permit.address_on_permit ? ` · permit names ${permit.address_on_permit}` : ''}
        {' · '}
        <span className="permitpanel__hash">
          sha256 {permit.sha256?.slice(0, 12)}…
        </span>
      </div>
    </div>
  );
}
