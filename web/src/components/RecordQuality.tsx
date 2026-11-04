import type { Facility } from '../types';

export function RecordQuality({ facility }: { facility: Facility }) {
  const location = facility.location_quality;
  const identity = facility.identity_match;
  return <div className="record-quality">
    <dl className="record-summary">
      <div><dt>Facility type evidence</dt><dd>{facility.class_label || 'Type not independently verified'}{facility.class_evidence && <small>{facility.class_evidence}</small>}</dd></div>
      {identity && <div><dt>Permit identity match</dt><dd>{identity.status.replace(/_/g, ' ')}{identity.method ? ` · ${identity.method.replace(/_/g, ' ')}` : ''}{identity.note && <small>{identity.note}</small>}</dd></div>}
      {location && <div><dt>Location evidence</dt><dd>{location.status === 'physical' ? 'Physical location supported' : location.status === 'suspect' ? 'Location needs review' : 'Physical location not verified'}<small>Address type: {location.address_type.replace(/_/g, ' ')}. {location.map_eligible ? 'Eligible for approximate geographic context.' : 'Location not shown on the map; geographic calculations are unavailable.'}</small></dd></div>}
    </dl>
    {facility.duplicate_candidates && facility.duplicate_candidates.length > 0 && <details><summary>Shared-address records need identity review</summary><p>These records may share an address. A shared physical site and a merge have not been verified.</p><ul>{facility.duplicate_candidates.map((group,index) => <li key={group.group_id || group.id || index}>{group.record_ids.length} records · {group.basis.replace(/_/g, ' ')}{group.note ? ` — ${group.note}` : ''}</li>)}</ul></details>}
    {location && location.reasons.length > 0 && <ul className="record-quality__reasons">{location.reasons.map((reason) => <li key={reason}>{reason.replace(/_/g, ' ')}</li>)}</ul>}
  </div>;
}
