import type { ComplianceItem } from '../types';

export function ComplianceStrip({
  items,
  peak,
}: {
  items: ComplianceItem[];
  peak: ComplianceItem;
}) {
  return (
    <>
      <div className="sectionhead">Available records and reference sizing</div>
      <div className="kpis">
        {items.map((item) => (
          <Kpi key={item.key} item={item} />
        ))}
      </div>
      <Kpi item={peak} wide />
    </>
  );
}

function Kpi({ item, wide }: { item: ComplianceItem; wide?: boolean }) {
  // A missing record and a reference load are not measurements against a
  // limit. The source builder uses placeholder percentages for those states;
  // rendering them would tell both visual and screen-reader users that an
  // unknown value is zero or a reference load is 50% of a real permit.
  const showGauge = item.value.trim() !== '—' && item.mark_pct != null && item.pct != null && Number.isFinite(item.pct);
  return (
    <div className={`card kpi${wide ? ' kpi--wide' : ''}`}>
      <div className="kpi__lab">{item.label}</div>
      <div className="kpi__val">
        {item.value}
        <small className={item.unit.trim() === '%' ? 'kpi__unit--tight' : undefined}>{item.unit}</small>
      </div>
      <div className="kpi__sub">{item.sub}</div>
      {showGauge && <div className="gauge" role="img" aria-label={`${item.label}: value at ${item.pct}% of the displayed scale; permit cap at ${item.mark_pct}%`}>
        <div
          className={`gauge__fill gauge__fill--${item.tone}`}
          style={{ width: `${Math.max(0, Math.min(item.pct!, 100))}%` }}
        />
        {item.mark_pct != null && (
          <div className="gauge__mark" style={{ left: `${item.mark_pct}%` }} title="permit cap" />
        )}
      </div>}
      {showGauge && (
        <div className="kpi__mark">▲ permit cap at {item.mark_pct}%</div>
      )}
    </div>
  );
}
