import { useState } from 'react';
import type { HourlySeries } from '../types';
import { useElementWidth } from '../router';
import { gridStats } from './GridStrip';

interface Props {
  hourly: HourlySeries | null;
}

type Stop = [number, [number, number, number]];
const RAMP: Stop[] = [
  [0.0, [45, 212, 191]], // teal
  [0.36, [129, 203, 127]],
  [0.55, [198, 196, 75]],
  [0.72, [251, 182, 43]],
  [0.86, [249, 146, 79]],
  [1.0, [248, 113, 113]], // red
];

/** Colour follows intensity, so the ramp itself is a second encoding of the number. */
export function rampColor(t: number): string {
  const x = Math.min(1, Math.max(0, t));
  for (let i = 0; i < RAMP.length - 1; i += 1) {
    const a = RAMP[i];
    const b = RAMP[i + 1];
    if (!a || !b) continue;
    if (x >= a[0] && x <= b[0]) {
      const f = (x - a[0]) / Math.max(b[0] - a[0], 1e-6);
      const c = a[1].map((v, j) => Math.round(v + (b[1][j]! - v) * f));
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }
  }
  return 'rgb(45, 212, 191)';
}

const PAD = { top: 18, right: 10, bottom: 28, left: 48 };
const HEIGHT = 208;

/**
 * Hourly grid intensity, drawn by hand in SVG.
 * Baseline is zero on purpose: a truncated axis would flatter the shape.
 */
export function IntensityChart({ hourly }: Props) {
  const [wrapRef, measuredWidth] = useElementWidth<HTMLDivElement>();
  // Keep labels legible on phones; the containing region scrolls horizontally.
  const width = Math.max(measuredWidth, 480);
  const [hover, setHover] = useState<number | null>(null);

  if (!hourly || hourly.readings.length === 0) {
    return (
      <div className="card chart chart--empty">
        <div className="chart__head">
          <span className="chart__k">WHEN IT RUNS — GRID INTENSITY</span>
          <span className="chart__note">no hourly series for this facility</span>
        </div>
        <p className="chart__empty">
          A grid intensity series appears only when timestamped observations are available.
          The annual grid factor is reported separately.
        </p>
      </div>
    );
  }

  const { values, min, max, minHour, maxHour } = gridStats(hourly);
  const n = values.length;
  const innerW = Math.max(width - PAD.left - PAD.right, 200);
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const slot = innerW / n;
  const barW = Math.max(slot - 3.2, 2);
  const scaleMax = Math.max(1100, Math.ceil(max / 100) * 100);
  const y = (v: number) => PAD.top + innerH - (v / scaleMax) * innerH;
  const avg = values.reduce((a, b) => a + b, 0) / n;

  const ticks = [0, 250, 500, 750, 1000, 1250].filter((t) => t <= scaleMax);
  const hovered = hover != null ? values[hover] ?? null : null;
  const hoveredHour = hover;
  const timeLabel = (index: number) => {
    const reading = hourly.readings[index];
    const date = new Date(reading?.interval_start_utc ?? '');
    return Number.isFinite(date.valueOf())
      ? date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }) + ' UTC'
      : 'Time unavailable';
  };

  return (
    <div className="card chart">
      <div className="chart__head">
        <span className="chart__k">
          WHEN IT RUNS — GRID INTENSITY, {hourly.day.slice(8, 10)} {monthName(hourly.day)} {hourly.day.slice(0, 4)}
        </span>
        <span className="chart__note">
          {hovered != null && hoveredHour != null ? (
            <>
              <b>{timeLabel(hoveredHour)}</b> · {Math.round(hovered).toLocaleString('en-US')} lb/MWh ·{' '}
              {hourly.readings[hoveredHour]?.interval_minutes}-minute interval
            </>
          ) : (
            <>
              {Math.round(min).toLocaleString('en-US')} lb/MWh at {timeLabel(minHour)} →{' '}
              {Math.round(max).toLocaleString('en-US')} at {timeLabel(maxHour)} · {n} published intervals
            </>
          )}
        </span>
      </div>

      <div className="chart__body" ref={wrapRef} tabIndex={0} role="region" aria-label="Hourly intensity chart. Scroll horizontally on small screens.">
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`Hourly grid carbon intensity for ${hourly.day}, ranging ${Math.round(min)} to ${Math.round(max)} lb CO2e per MWh`}
          className="chart__svg"
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(t)}
                y2={y(t)}
                stroke={t === 0 ? 'rgba(27,38,55,0.95)' : 'rgba(27,38,55,0.55)'}
                strokeWidth={1}
              />
              <text x={PAD.left - 8} y={y(t) + 3} className="chart__tick" textAnchor="end">
                {t === 0 ? '0' : t.toLocaleString('en-US')}
              </text>
            </g>
          ))}

          {/* Mean of the displayed observations, separate from the annual factor. */}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={y(avg)}
            y2={y(avg)}
            stroke="rgba(148,163,184,0.5)"
            strokeWidth={1}
            strokeDasharray="3 4"
          />
          <text x={PAD.left + 5} y={y(avg) - 6} className="chart__avg" textAnchor="start">
            displayed mean {Math.round(avg).toLocaleString('en-US')} lb/MWh
          </text>

          {values.map((v, i) => {
            const t = max === min ? 0.5 : (v - min) / (max - min);
            const active = hover === i;
            return (
              <g key={i}>
                <rect
                  x={PAD.left + i * slot + (slot - barW) / 2}
                  y={y(v)}
                  width={barW}
                  height={Math.max(PAD.top + innerH - y(v), 1)}
                  rx={1.5}
                  fill={rampColor(t)}
                  opacity={active ? 1 : 0.9}
                  className="chart__bar"
                />
                {active && (
                  <line
                    x1={PAD.left + i * slot + slot / 2}
                    x2={PAD.left + i * slot + slot / 2}
                    y1={PAD.top - 4}
                    y2={PAD.top + innerH}
                    stroke="rgba(233,239,248,0.35)"
                    strokeWidth={1}
                    strokeDasharray="2 3"
                  />
                )}
                {/* generous hit area, no fill */}
                <rect
                  x={PAD.left + i * slot}
                  y={PAD.top}
                  width={slot}
                  height={innerH}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                />
              </g>
            );
          })}

          {values.map((_, i) =>
            i % 4 === 0 ? (
              <text
                key={`h${i}`}
                x={PAD.left + i * slot + slot / 2}
                y={HEIGHT - 8}
                className="chart__hour"
                textAnchor="middle"
              >
                {timeLabel(i)}
              </text>
            ) : null,
          )}
        </svg>
      )}

      </div>

      <p className="chart__scroll-hint">Scroll across the chart to see every hour.</p>
      <div className="chart__legend">
        <span className="chart__ramp" aria-hidden="true" />
        <span className="chart__legendlabel">cleaner</span>
        <span className="chart__legendlabel chart__legendlabel--right">dirtier</span>
        <span className="chart__badge">
          {hourly.intensity_state === 'PROVISIONAL' ? 'PROVISIONAL' : 'SETTLED'} ·{' '}
          {hourly.factor_set_label} · {hourly.interval_minutes}-min intervals
        </span>
      </div>
    </div>
  );
}

function monthName(day: string): string {
  const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  const m = Number(day.slice(5, 7));
  return months[m - 1] ?? '';
}
