import type { DisclosureLocation, FacilityObservations, ReportedObservation } from './types';

export type DisclosureFamily = 'all' | 'electricity' | 'water' | 'emissions';
export const scopeName: Record<DisclosureLocation['reporting_scope'], string> = {
  campus: 'Campus', building: 'Building', regulatory_facility: 'Regulatory reporting facility',
  operator_location: 'Operator reporting location',
};

const sourcePublishers: Record<string, string> = {
  meta_actuals2024: 'Meta', google_water2025: 'Google',
  massachusetts_energy: 'Massachusetts Department of Energy Resources', ghgrp: 'U.S. EPA GHGRP',
  nyc_benchmarking: 'New York City Department of Buildings',
  seattle_benchmarking: 'City of Seattle Office of Sustainability and Environment',
};

export const disclosedFamily = (metric: string): Exclude<DisclosureFamily, 'all'> =>
  metric === 'electricity_consumption' ? 'electricity' : metric.startsWith('water_') ? 'water' : 'emissions';

export function normalizeDisclosureFamily(value?: string): DisclosureFamily {
  return value === 'electricity' || value === 'water' || value === 'emissions' ? value : 'all';
}

export function disclosurePublishers(location: DisclosureLocation): string[] {
  const names = [...new Set(location.observations.map(row => row.publisher).filter(Boolean))];
  const sourcePublisher = sourcePublishers[location.source_id];
  return names.length ? names : sourcePublisher ? [sourcePublisher] : [];
}

export function reportedLocationHref(locationId: string): string {
  return `#/reported?location=${encodeURIComponent(locationId)}`;
}

export interface DisclosureFilters {
  query?: string;
  family?: string;
  year?: string;
  publisher?: string;
  scope?: string;
  locationId?: string;
}

/** Apply quantity-level filters to the same observation, never to different rows of a campus. */
export function filterDisclosureLocations(locations: DisclosureLocation[], filters: DisclosureFilters = {}) {
  const query = (filters.query ?? '').trim().toLowerCase();
  const family = normalizeDisclosureFamily(filters.family);
  const year = filters.year && filters.year !== 'all' ? filters.year : null;
  const publisher = filters.publisher && filters.publisher !== 'all' ? filters.publisher : null;
  const scope = filters.scope && filters.scope !== 'all' ? filters.scope : null;
  return locations.flatMap(location => {
    if (filters.locationId && location.disclosure_location_id !== filters.locationId) return [];
    if (scope && location.reporting_scope !== scope) return [];
    const publishers = disclosurePublishers(location);
    const address = location.address ? Object.values(location.address).filter(Boolean).join(' ') : '';
    if (query && !`${location.location_label} ${location.operator ?? ''} ${address} ${publishers.join(' ')}`.toLowerCase().includes(query)) return [];
    const observations = location.observations.filter(row =>
      (family === 'all' || disclosedFamily(row.metric) === family) &&
      (!year || String(row.data_year) === year) && (!publisher || row.publisher === publisher));
    if (observations.length) return [{ location, observations }];
    // Preserve genuine missing-data entries only when no quantity filter is active.
    if (!location.observations.length && family === 'all' && !year && (!publisher || publishers.includes(publisher))) {
      return [{ location, observations }];
    }
    return [];
  });
}

/** Count canonical observations and reporting entries, never linked-record references or summed quantities. */
export function disclosureSummary(data: Pick<FacilityObservations, 'locations'>) {
  const locations = [...new Map(data.locations.map(location => [location.disclosure_location_id, location])).values()];
  const observations = [...new Map(locations.flatMap(location => location.observations).map(row => [row.observation_id, row])).values()];
  const familyLocations = (family: Exclude<DisclosureFamily, 'all'>) => locations.filter(location =>
    location.observations.some(row => disclosedFamily(row.metric) === family)).length;
  return {
    locations: locations.length,
    populated: locations.filter(location => location.observations.length > 0).length,
    missing: locations.filter(location => location.observations.length === 0).length,
    observations: observations.length,
    electricity: familyLocations('electricity'), water: familyLocations('water'), emissions: familyLocations('emissions'),
    years: [...new Set(observations.map(row => row.data_year))].sort((a, b) => b - a),
    publishers: [...new Set(locations.flatMap(disclosurePublishers))].sort(),
    scopes: [...new Set(locations.map(location => location.reporting_scope))],
  };
}

export function reportingBasisLabel(row: ReportedObservation): string {
  if (row.reporting_basis === 'publisher_calculated_from_reported_energy') return 'Publisher-calculated from reported energy';
  return row.reporting_basis.replace(/_/g, ' ');
}
