import type { DisclosureLocation, FacilityObservations, ReportedObservation } from './types';

export const learningTopics = [
  { id: 'electricity', title: 'How much electricity do data centers use?', short: 'Electricity', description: 'Read annual energy figures without confusing them with power demand.' },
  { id: 'water', title: 'What do the water numbers mean?', short: 'Water', description: 'Follow the difference between withdrawal, discharge and consumption.' },
  { id: 'emissions', title: 'Why can one campus report two emissions numbers?', short: 'Emissions', description: 'Understand the two ways companies account for electricity emissions.' },
  { id: 'generators', title: 'What can a generator permit tell us?', short: 'Generators', description: 'Separate equipment and document conditions from actual operation.' },
  { id: 'grid', title: 'What can the regional grid tell us?', short: 'The grid', description: 'Explore demand and generation without attributing them to a building.' },
  { id: 'bills', title: 'Who pays for additional electricity infrastructure?', short: 'Electricity bills', description: 'See which utility documents are needed to answer the local question.' },
  { id: 'community', title: 'What changes for the surrounding community?', short: 'Community', description: 'Look for documented benefits, local decisions and operating evidence.' },
  { id: 'virginia', title: 'How do I investigate a data center in Virginia?', short: 'Virginia research guide', description: 'Build a record of the site, utility, water supply and public decisions.' },
] as const;

export type LearningTopic = typeof learningTopics[number]['id'];
export type ReportedExample = { location: DisclosureLocation; rows: ReportedObservation[]; year: number };

/** A story never silently combines years, boundaries or duplicate metric rows. */
export function selectReportedExample(data: FacilityObservations | null, locationId: string, metrics: string[], sameUnit = false): ReportedExample | null {
  const location = data?.locations.find(row => row.disclosure_location_id === locationId);
  if (!location || metrics.length === 0) return null;
  const relevant = location.observations.filter(row => metrics.includes(row.metric));
  if (!relevant.length) return null;
  const year = Math.max(...relevant.map(row => row.data_year));
  const latest = relevant.filter(row => row.data_year === year);
  const rows: ReportedObservation[] = [];
  for (const metric of metrics) {
    const candidates = latest.filter(row => row.metric === metric);
    if (candidates.length !== 1 || !Number.isFinite(candidates[0]!.value)) return null;
    rows.push(candidates[0]!);
  }
  const first = rows[0]!;
  if (rows.some(row => row.disclosure_location_id !== locationId || row.reporting_scope !== location.reporting_scope || row.period_start !== first.period_start || row.period_end !== first.period_end || row.publisher !== first.publisher || row.source_id !== first.source_id || (sameUnit && row.unit !== first.unit))) return null;
  return { location, rows, year };
}

/** A proportional water graphic is only valid for comparable, reconciling values. */
export function waterShares(example: ReportedExample): { discharged: number; consumed: number } | null {
  const withdrawal = example.rows.find(row => row.metric === 'water_withdrawal');
  const discharge = example.rows.find(row => row.metric === 'water_discharge');
  const consumption = example.rows.find(row => row.metric === 'water_consumption');
  if (!withdrawal || !discharge || !consumption || withdrawal.unit !== discharge.unit || withdrawal.unit !== consumption.unit) return null;
  if (withdrawal.value <= 0 || discharge.value < 0 || consumption.value < 0) return null;
  // The source reports one decimal place; allow combined cell rounding only.
  if (Math.abs(withdrawal.value - discharge.value - consumption.value) > 0.151) return null;
  if (discharge.value > withdrawal.value || consumption.value > withdrawal.value) return null;
  return { discharged: discharge.value / withdrawal.value * 100, consumed: consumption.value / withdrawal.value * 100 };
}
