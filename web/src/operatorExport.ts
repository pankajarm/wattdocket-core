import type { OperatorPayload } from './types';
import { calculateScenario } from './scenario';

/**
 * The operator export, built in the browser from the payload already on screen.
 *
 * No server call and no new arithmetic: every number in the file is a number
 * the bench shows, and every withheld figure is exported as withheld, with its
 * reason, never as a zero. Missing values stay null in JSON and blank in CSV;
 * a reported or computed zero remains a numeric zero.
 */

export interface ExportMeta {
  /** Where the payload came from. A fixture file must say so inside the file. */
  dataSource: 'api' | 'fixture';
  exportedAt: string;
}

const COLUMNS = ['section', 'item', 'value', 'unit', 'status', 'basis', 'source', 'scope', 'observation_start', 'observation_end', 'release_id', 'snapshot_published_at'] as const;
type Row = Record<(typeof COLUMNS)[number], string | number | null | undefined>;

function isWithheld(text: string): boolean {
  return /^withheld\b/i.test(text.trim());
}

function cell(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return '';
  const s = String(v);
  // Quote anything a spreadsheet would split or misread. A leading = + - @ is
  // prefixed so a quoted permit passage cannot run as a formula.
  const safe = /^[=+\-@]/.test(s) ? `'${s}` : s;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function operatorRows(p: OperatorPayload, meta: ExportMeta): Row[] {
  const rows: Row[] = [];
  const m = p.method;
  const add = (r: Partial<Row>) =>
    rows.push({ section: '', item: '', value: '', unit: '', status: '', basis: '', source: '', scope: '', observation_start: '', observation_end: '', release_id: p.release_id ?? '', snapshot_published_at: p.built_at ?? p.data_status?.built_at ?? '', ...r });

  add({ section: 'facility', item: 'facility_id', value: p.facility_id });
  add({ section: 'facility', item: 'label', value: p.facility_label });
  add({ section: 'facility', item: 'inventory_retrieved_at', value: p.as_of });
  add({ section: 'facility', item: 'calc_version', value: p.calc_version });
  add({ section: 'facility', item: 'exported_at', value: meta.exportedAt });
  add({
    section: 'facility',
    item: 'data_source',
    value: meta.dataSource,
    basis:
      meta.dataSource === 'fixture'
        ? 'FIXTURE DATA: a synthetic payload, not the production estimator'
        : 'operator payload built from public sources',
  });
  add({
    section: 'facility',
    item: 'synthetic_meter_data',
    value: String(p.synthetic_meter_data),
  });

  add({section:'release',item:'release_id',value:p.release_id});
  add({section:'release',item:'snapshot_published_at',value:p.built_at ?? p.data_status?.built_at});
  for (const r of p.methods) {
    const withheld = isWithheld(r.note) || r.tonnes == null || !Number.isFinite(r.tonnes);
    add({
      section: r.key === 'location' ? 'scenario' : 'facility_accounting',
      scope: r.key === 'location' ? 'scenario' : 'facility',
      observation_start: m?.observation_start, observation_end: m?.observation_end,
      item: r.label,
      value: withheld ? '' : r.tonnes,
      unit: 't CO2e/yr',
      status: withheld ? 'withheld' : r.key === 'location' ? 'illustrative_calculation' : 'computed',
      basis: r.note,
      source: !withheld && m?.factor_set_id ? `factor set ${m.factor_set_id}` : '',
    });
  }

  for (const c of [...p.compliance, p.peak]) {
    const withheld = c.value.trim() === '—' || isWithheld(c.sub);
    add({
      section: c.key === 'peak' ? 'scenario_input' : 'operating_evidence',
      scope: c.key === 'peak' ? 'scenario' : 'facility',
      item: c.label,
      value: withheld ? '' : c.value,
      unit: c.unit,
      status: withheld ? 'withheld' : 'stated',
      basis: c.sub,
    });
  }

  for (const group of p.disclosures ?? []) {
    for (const observation of group.location.observations) {
      add({ section: 'reported_disclosure', item: `${group.location.location_label}: ${observation.label}`, value: observation.value, unit: observation.unit, status: observation.reporting_basis, scope: observation.reporting_scope, observation_start: observation.period_start, observation_end: observation.period_end, source: observation.source_url, basis: `${observation.methodology_note} Relationship: ${group.link.relationship}; inventory allocation: ${group.link.inventory_allocation}. Observation: ${observation.observation_id}; source SHA-256: ${observation.source_sha256}` });
    }
  }

  const env = p.permit_envelope;
  if (env && Array.isArray(env.rows)) {
    for (const r of env.rows) {
      add({ section: 'permit_envelope', item: r.label, value: r.value, status: 'arithmetic', basis: r.sub });
    }
    if (env.assumption) add({ section: 'permit_envelope', item: 'assumption', basis: env.assumption });
    add({ section: 'permit_envelope', item: 'note', basis: env.note });
  }

  for (const l of p.levers) {
    const quantified = l.tonnes > 0;
    add({
      section: 'lever',
      item: `${l.rank}. ${l.title}`,
      value: quantified ? l.tonnes : '',
      unit: 't CO2e/yr avoided',
      status: quantified ? 'computed' : 'not quantified',
      basis: `${l.detail}; cost: ${l.cost}`,
    });
  }

  if (m) {
    add({ section: 'method', item: 'factor_year', value: m.data_vintage });
    add({ section: 'method', item: 'publisher_release_date', value: m.published_at });
    add({ section: 'method', item: 'factor_retrieved_at', value: m.retrieved_at });
    add({ section: 'method', item: 'factor_source', source: m.source_url });
    add({ section: 'method', item: 'archived_factor_row', source: m.evidence_url });
    add({ section: 'method', item: 'factor_set_id', value: m.factor_set_id });
    add({ section: 'method', item: 'factor_set_sha256', value: m.factor_set_sha256 });
    add({
      section: 'method',
      item: 'grid_subregion',
      value: m.subregion,
      basis: [m.subregion_name, m.binding_method].filter(Boolean).join(' · '),
    });
    add({ section: 'method', item: 'grid_rate', value: m.rate_lb_per_mwh, unit: 'lb CO2e/MWh' });
    if (m.calc_id) add({ section: 'method', item: 'calc_id', value: m.calc_id });
    m.chain.forEach((step, i) => add({ section: 'method', item: `chain ${i + 1}`, basis: step }));
    (m.transforms ?? []).forEach((t, i) => add({ section: 'method', item: `transform ${i + 1}`, basis: t }));
  } else {
    add({
      section: 'method',
      item: 'factor_set',
      status: 'not in payload',
      basis: 'this payload predates the export fields; the factor set is on the public card',
    });
  }

  if (p.sources && p.sources.length > 0) {
    for (const s of p.sources) {
      add({
        section: 'source',
        item: s.supports ?? '',
        status: s.verified ? 'textual_support_checked' : 'quoted',
        basis: s.quote ?? '',
        source: [s.publisher, s.document, s.locator, s.doc_sha256 ? `sha256 ${s.doc_sha256}` : '', s.url ?? '']
          .filter(Boolean)
          .join(' · '),
      });
    }
  } else {
    add({
      section: 'source',
      item: 'sources',
      status: 'not in payload',
      basis: 'this payload carries no source rows; each claim cites its sources on the public card',
    });
  }

  const inputs = p.scenario_inputs ?? m?.inputs;
  if (inputs) {
    add({section:'scenario_input',item:'load_mw',value:inputs.load_mw,unit:'MW',scope:'scenario',basis:'User-selected grid-side load; not a facility measurement.'});
    add({section:'scenario_input',item:'input_source',value:inputs.input_source ?? 'not supplied',scope:'scenario'});
    add({section:'scenario_input',item:'capacity_basis',value:inputs.capacity_basis,scope:'scenario'});
    add({section:'scenario_input',item:'hours_per_year',value:inputs.hours_per_year,unit:'hours',scope:'scenario'});
    add({section:'scenario_input',item:'factor_year',value:inputs.factor_year,scope:'scenario'});
    const result = calculateScenario(inputs,m?.rate_lb_per_mwh);
    for (const key of ['low','base','high'] as const) {
      add({section:'scenario_input',item:`${key}_utilization`,value:inputs.utilization[key],unit:'fraction',scope:'scenario',basis:'Chosen scenario, not a percentile.'});
      if (!('error' in result)) {
        add({section:'scenario_result',item:`${key}_annual_energy`,value:result.energy_mwh[key],unit:'MWh/year',scope:'scenario',status:'illustrative_calculation',observation_start:m?.observation_start,observation_end:m?.observation_end});
        add({section:'scenario_result',item:`${key}_annual_emissions`,value:result.carbon_t[key],unit:'t CO2e/year',scope:'scenario',status:'illustrative_calculation',observation_start:m?.observation_start,observation_end:m?.observation_end});
      }
    }
  }
  for (const permit of p.permit_limits ?? []) {
    add({section:'permit_document_condition',item:permit.metric,value:permit.value,unit:permit.unit,scope:permit.scope ?? 'quoted_condition',status:permit.effective_status ?? 'current_effectiveness_not_verified',basis:permit.quote,source:permit.url});
    for (const key of ['program','issued_at','freshness','retrieved_at','archived_by_at','latest_fetch_status','latest_fetch_at','representation','page_derivation'] as const) add({section:'permit_provenance',item:`${permit.registration ?? ''} ${permit.metric}: ${key}`,value:permit[key],scope:'document'});
  }
  for (const source of p.data_status?.sources ?? []) {
    add({section:'source_dates',item:source.label,scope:'source',observation_start:source.observation_start,observation_end:source.observation_end,status:source.status,basis:`Reporting period: ${source.data_vintage ?? 'not supplied'}; publisher released: ${source.published_at ?? 'not supplied'}; retrieved: ${source.retrieved_at ?? 'not supplied'}`,source:source.url});
  }
  add({
    section: 'operator_held',
    item: 'Hourly load, gen-set run log, water meter',
    status: 'withheld',
    basis: 'operator-held, not public; no figure is exported in their place',
  });

  return rows;
}

export function operatorCsv(p: OperatorPayload, meta: ExportMeta): string {
  const lines = [COLUMNS.join(',')];
  for (const r of operatorRows(p, meta)) lines.push(COLUMNS.map((c) => cell(r[c])).join(','));
  // The BOM makes Excel read the file as UTF-8; without it every em-dash is mojibake.
  return '\uFEFF' + lines.join('\r\n') + '\r\n';
}

export function operatorJson(p: OperatorPayload, meta: ExportMeta): string {
  return (
    JSON.stringify(
      {
        export_version: 'wattdocket.operator-export@3',
        exported_at: meta.exportedAt,
        data_source: meta.dataSource,
        note:
          'Public evidence retains its source dates and scope. Interactive scenario results use the exported inputs and historical factor. Unavailable numeric values are null. ' +
          'Reported or computed zero values remain numeric zero.',
        payload: p,
      },
      null,
      2,
    ) + '\n'
  );
}

export function exportFilename(p: OperatorPayload, meta: ExportMeta, ext: 'csv' | 'json'): string {
  const id = p.facility_id.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const day = (p.as_of || meta.exportedAt).slice(0, 10);
  const tag = meta.dataSource === 'fixture' ? '-FIXTURE' : '';
  return `wattdocket-operator-${id}-${day}${tag}.${ext}`;
}

/** Hand the browser a file. The object URL is released after the click. */
export function download(filename: string, body: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([body], { type: `${mime};charset=utf-8` }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
