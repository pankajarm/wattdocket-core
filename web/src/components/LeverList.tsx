import type { Lever } from '../types';

interface Props {
  levers: Lever[];
  baseTonnes: number | null;
  selected: ReadonlySet<number>;
  onToggle: (rank: number) => void;
}

export function LeverList({ levers, baseTonnes, selected, onToggle }: Props) {
  const avoided = levers
    .filter((l) => selected.has(l.rank))
    .reduce((sum, l) => sum + l.tonnes, 0);
  // Residual is what remains AFTER the avoidance, so it must fall.
  // This read `baseTonnes + avoided` and reported 19,025 t with 1,286 t saved as
  // a 20,311 t "residual" — a saving displayed as an increase, on the screen an
  // operator would use to justify spending money.
  const residual = baseTonnes === null ? null : Math.max(baseTonnes - avoided, 0);
  const quantifiedCount = levers.filter((l) => l.tonnes > 0).length;

  return (
    <div className="card card--levers">
      {levers.map((l) => {
        const quantified = l.tonnes > 0;
        const on = quantified && selected.has(l.rank);
        return (
          <button
            type="button"
            key={l.rank}
            className={`lever${on ? ' lever--on' : ''}`}
            onClick={() => onToggle(l.rank)}
            aria-pressed={on}
            disabled={!quantified}
          >
            <span className="lever__t">
              <span className="lever__rank">{String(l.rank).padStart(2, '0')}</span>
              {l.title}
              <span className="lever__detail">{l.detail}</span>
            </span>
            <span className="lever__d">{quantified ? `${l.tonnes.toLocaleString('en-US')} t` : 'Not quantified'}</span>
            <span className="lever__c">{l.cost}</span>
          </button>
        );
      })}
      <div className="lever__sum">
        <span>
          {quantifiedCount === 0
            ? 'No reduction can be quantified from the available public data.'
            : selected.size === 0
            ? 'Select levers to build a portfolio — arithmetic, no new model'
            : `${selected.size} lever${selected.size > 1 ? 's' : ''} selected · ${avoided.toLocaleString('en-US')} t avoided`}
        </span>
        {quantifiedCount > 0 && residual !== null && <b>{residual.toLocaleString('en-US')} t residual</b>}
      </div>
    </div>
  );
}
