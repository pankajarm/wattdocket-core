import { useState } from 'react';
import type { HeatmapCell } from '../types';
import { useElementWidth } from '../router';

interface Props {
  months: string[];
  hours: number;
  cells: HeatmapCell[];
  note: string;
}

const LABEL_W = 30;
const TOP = 14;
const ROW_H = 13;
const GAP = 1.5;

function cellFill(cell: HeatmapCell | undefined): string {
  if (!cell || cell.matched == null) return 'rgba(248,113,113,0.52)';
  const m = cell.matched;
  if (m >= 0.85) return 'rgba(45,212,191,0.92)';
  if (m >= 0.6) return 'rgba(45,212,191,0.62)';
  if (m >= 0.4) return 'rgba(251,191,36,0.62)';
  return 'rgba(248,113,113,0.38)';
}

/**
 * 12 × 24 matched-hours heatmap. Unmatched is red — the absence is the point,
 * so it gets the loudest colour, not the lightest.
 */
export function MatchedHoursHeatmap({ months, hours, cells, note }: Props) {
  // An empty 12 × 24 grid with a legend invites a reader to read absence as
  // data. Say why it is missing instead of drawing the grid.
  if (cells.length === 0) {
    return (
      <div className="card heat heat--none">
        <div className="heat__head">
          <span className="heat__k">WHERE THE GAP IS — MATCHED HOURS BY MONTH × HOUR</span>
          <span className="heat__cap">withheld</span>
        </div>
        <p className="note">{note}</p>
      </div>
    );
  }

  const [ref, width] = useElementWidth<HTMLDivElement>();
  const [hover, setHover] = useState<HeatmapCell | null>(null);

  const innerW = Math.max(width - LABEL_W, 200);
  const colW = innerW / hours;
  const height = TOP + months.length * ROW_H + 22;

  const lookup = new Map<string, HeatmapCell>();
  for (const c of cells) lookup.set(`${c.month}-${c.hour}`, c);

  return (
    <div className="card card--heat">
      <div className="heat__head">
        <span className="sectionhead sectionhead--inline">WHERE THE GAP IS — MATCHED HOURS BY MONTH × HOUR</span>
        <span className="heat__readout">
          {hover
            ? `${months[hover.month - 1] ?? ''} ${String(hover.hour).padStart(2, '0')}:00 · ${
                hover.matched == null ? 'unmatched' : `${Math.round(hover.matched * 100)}% matched`
              }`
            : 'hover a cell'}
        </span>
      </div>

      <div className="heat__body" ref={ref}>
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label="Matched clean-energy hours by month and hour of day; winter evenings are unmatched"
        >
          {months.map((m, row) => (
            <text
              key={m}
              x={LABEL_W - 8}
              y={TOP + row * ROW_H + ROW_H / 2 + 3}
              className="heat__month"
              textAnchor="end"
            >
              {m}
            </text>
          ))}
          {months.map((_, row) =>
            Array.from({ length: hours }, (_, hour) => {
              const cell = lookup.get(`${row + 1}-${hour}`);
              return (
                <rect
                  key={`${row}-${hour}`}
                  x={LABEL_W + hour * colW + GAP / 2}
                  y={TOP + row * ROW_H + GAP / 2}
                  width={Math.max(colW - GAP, 1)}
                  height={ROW_H - GAP}
                  rx={1}
                  fill={cellFill(cell)}
                  onMouseEnter={() => cell && setHover(cell)}
                  onMouseLeave={() => setHover(null)}
                />
              );
            }),
          )}
          {Array.from({ length: hours }, (_, hour) =>
            hour % 3 === 0 ? (
              <text
                key={`h-${hour}`}
                x={LABEL_W + hour * colW + colW / 2}
                y={height - 6}
                className="heat__hour"
                textAnchor="middle"
              >
                {String(hour).padStart(2, '0')}
              </text>
            ) : null,
          )}
        </svg>
      )}

      </div>

      <div className="heat__legend">
        <span><i className="sw sw--full" />matched</span>
        <span><i className="sw sw--part" />partial</span>
        <span><i className="sw sw--none" />unmatched</span>
        <span className="heat__cap">
          {cells.length > 0
            ? `12 months × 24 hours · ${cells.length.toLocaleString('en-US')} cells`
            : 'no metered rows — this build has none'}
        </span>
      </div>
      <p className="note">{note}</p>
    </div>
  );
}
