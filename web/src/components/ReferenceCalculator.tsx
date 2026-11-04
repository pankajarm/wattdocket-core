import { useId, useState } from 'react';
import type { ScenarioInputs } from '../types';
import { calculateScenario, defaultScenario } from '../scenario';
import { safeSourceUrl } from '../dataPresentation';

export function ReferenceCalculator({ rate, year, region, sourceUrl, evidenceUrl, value, onChange }: {
  rate: number | null; year?: string | number | null; region: string; sourceUrl?: string | null; evidenceUrl?: string | null;
  value?: ScenarioInputs; onChange?: (inputs: ScenarioInputs) => void;
}) {
  const id = useId();
  const [local, setLocal] = useState(() => defaultScenario(year));
  const inputs = value ?? local;
  const update = (next: ScenarioInputs) => { const chosen: ScenarioInputs = { ...next, input_source:'user_input', facility_measurement:false }; setLocal(chosen); onChange?.(chosen); };
  const result = calculateScenario(inputs, rate);
  const format = (n: number) => n.toLocaleString('en-US', { maximumFractionDigits: 1 });
  return <section className="reference-calculator" aria-label="Historical grid scenario calculator">
    <h3>Try a historical grid scenario</h3>
    <p>Choose a hypothetical grid-side load and utilization. This calculator uses {region}’s {year || 'historical'} annual regional factor; it does not estimate this facility’s current footprint. The initial 10 MW and utilization values are illustrative presets.</p>
    <div className="calculator-inputs">
      <label htmlFor={`${id}-load`}>Grid-side load (MW)<input id={`${id}-load`} type="number" min="0" step="any" value={Number.isNaN(inputs.load_mw) ? '' : inputs.load_mw} onChange={(event) => update({ ...inputs, load_mw: event.currentTarget.value === '' ? NaN : Number(event.currentTarget.value) })} /></label>
      {(['low', 'base', 'high'] as const).map((key) => <label key={key} htmlFor={`${id}-${key}`}>{key === 'base' ? 'Base' : key === 'low' ? 'Low' : 'High'} utilization (%)<input id={`${id}-${key}`} type="number" min="0" max="100" step="any" value={Number.isNaN(inputs.utilization[key]) ? '' : Math.round(inputs.utilization[key] * 10000) / 100} onChange={(event) => update({ ...inputs, utilization: { ...inputs.utilization, [key]: event.currentTarget.value === '' ? NaN : Number(event.currentTarget.value) / 100 } })} /></label>)}
    </div>
    <p className="calculator-basis">Grid-side means electricity drawn from the grid, including facility overhead. No PUE multiplier is added. Low/base/high are input scenarios, not probabilities.</p>
    {'error' in result ? <p className="data-message" role="status">{result.error}</p> : <div className="calculator-results" aria-live="polite"><table><caption>Illustrative annual results using the {year || 'historical'} grid mix</caption><thead><tr><th scope="col">Scenario</th><th scope="col">Electricity (MWh)</th><th scope="col">Emissions (t CO₂e)</th></tr></thead><tbody>{(['low', 'base', 'high'] as const).map((key) => <tr key={key}><th scope="row">{key === 'base' ? 'Base' : key === 'low' ? 'Low' : 'High'}</th><td>{format(result.energy_mwh[key])}</td><td>{format(result.carbon_t[key])}</td></tr>)}</tbody></table></div>}
    <details className="calculator-method"><summary>Calculation and source</summary><p>Annual MWh = grid-side MW × 8,760 hours × utilization. Annual metric tonnes CO₂e = MWh × {rate == null ? 'unavailable' : format(rate)} lb CO₂e/MWh ÷ 2,204.622621848776. The inputs are yours; the grid factor is historical.</p>{safeSourceUrl(sourceUrl) && <a href={safeSourceUrl(sourceUrl)} target="_blank" rel="noreferrer">Publisher’s annual factor source</a>}{safeSourceUrl(evidenceUrl) && <> · <a href={safeSourceUrl(evidenceUrl)} target="_blank" rel="noreferrer">Archived factor row</a></>}</details>
  </section>;
}
