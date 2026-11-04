import type { ScenarioInputs } from './types';

export const LB_PER_METRIC_TONNE = 2204.622621848776;
export function defaultScenario(factorYear?: string | number | null): ScenarioInputs {
  return { load_mw: 10, capacity_basis: 'GRID_MW', utilization: { low: .45, base: .65, high: .80 }, hours_per_year: 8760, factor_year: factorYear, input_source:'illustrative_preset', facility_measurement:false };
}

/** A user-controlled illustration using a historical regional factor. No facility input is inferred. */
export function calculateScenario(inputs: ScenarioInputs, rate: number | null | undefined) {
  const { low, base, high } = inputs.utilization;
  if (rate == null || !Number.isFinite(rate) || rate < 0) return { error: 'A verified annual grid factor is needed for this calculation.' } as const;
  if (!Number.isFinite(inputs.load_mw) || inputs.load_mw <= 0 || inputs.capacity_basis !== 'GRID_MW') return { error: 'Enter a grid-side load greater than zero.' } as const;
  if (![low, base, high].every((value) => Number.isFinite(value) && value >= 0 && value <= 1) || low > base || base > high) return { error: 'Use utilization from 0% to 100%, ordered low ≤ base ≤ high.' } as const;
  if (inputs.hours_per_year !== 8760) return { error: 'This illustration uses a standard 8,760-hour year.' } as const;
  const energy = { low: inputs.load_mw * 8760 * low, base: inputs.load_mw * 8760 * base, high: inputs.load_mw * 8760 * high };
  const carbon = { low: energy.low * rate / LB_PER_METRIC_TONNE, base: energy.base * rate / LB_PER_METRIC_TONNE, high: energy.high * rate / LB_PER_METRIC_TONNE };
  return { energy_mwh: energy, carbon_t: carbon } as const;
}
