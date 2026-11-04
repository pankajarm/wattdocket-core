import type { MethodRow } from '../types';

export function MethodBars({ methods, note }: { methods: MethodRow[]; note: string }) {
  // A withheld method used to render as a zero bar labelled "0 t", which reads
  // as "zero emissions" — the most misleading thing this screen could say. Only
  // computed methods set the scale, and withheld ones get no bar at all.
  const max = Math.max(...methods.map((m) => m.tonnes).filter((v): v is number => typeof v === 'number' && Number.isFinite(v)), 1);

  return (
    <div className="card card--methods">
      <div className="bars">
        {methods.map((m) => {
          const tonnes = typeof m.tonnes === 'number' && Number.isFinite(m.tonnes) && !/^withheld\b/i.test(m.note.trim()) ? m.tonnes : null;
          const withheld = tonnes === null;
          const width = tonnes === null ? 0 : Math.max((tonnes / max) * 100, 0);
          return (
            <div className="bar" key={m.key} title={m.note}>
              <div className="bar__n">
                {m.label}
                {withheld ? <span className="bar__held"> withheld</span> : null}
              </div>
              <div
                className={`bar__track${withheld ? ' bar__track--empty' : ''}`}
                role="img"
                aria-label={
                  withheld
                    ? `${m.label}: withheld, not estimated`
                    : `${m.label}: ${tonnes.toLocaleString('en-US')} tonnes CO2e`
                }
              >
                {withheld ? null : (
                  <div
                    className={`bar__fill bar__fill--${m.tone}`}
                    style={{ width: `${width}%` }}
                  />
                )}
              </div>
              <div className={`bar__v${withheld ? ' bar__v--held' : ''}`}>
                {withheld ? '—' : `${tonnes.toLocaleString('en-US')} t`}
              </div>
            </div>
          );
        })}
      </div>
      <p className="note" dangerouslySetInnerHTML={{ __html: renderBold(note) }} />
    </div>
  );
}

/** The fixture note carries **bold** spans; escape everything else. */
function renderBold(text: string): string {
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return escaped.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
}
