import type { OperatorPayload } from '../types';
import { download, exportFilename, operatorCsv, operatorJson, type ExportMeta } from '../operatorExport';

/**
 * Two files, built here from the payload already on screen: a CSV a reviewer
 * can open in a spreadsheet, and the JSON payload with its method and sources.
 * PDF is not offered: nothing in this build renders one.
 */
export function ExportBlock({ payload, fixture, disabled = false }: { payload: OperatorPayload; fixture: boolean; disabled?: boolean }) {
  const meta = (): ExportMeta => ({
    dataSource: fixture ? 'fixture' : 'api',
    exportedAt: new Date().toISOString(),
  });
  const sources = payload.sources?.length ?? 0;

  return (
    <>
      <div className="sectionhead">Export</div>
      <div className="card card--export">
        {payload.export.map((r) => (
          <div className="export__row" key={r.label}>
            <span className="export__label">{r.label}</span>
            <span>· {r.detail}</span>
          </div>
        ))}
        <div className="export__actions">
          <button
            type="button"
            disabled={disabled}
            className="ghostbtn ghostbtn--sm"
            onClick={() => {
              const m = meta();
              download(exportFilename(payload, m, 'csv'), operatorCsv(payload, m), 'text/csv');
            }}
          >
            Download CSV
          </button>
          <button
            type="button"
            disabled={disabled}
            className="ghostbtn ghostbtn--sm"
            onClick={() => {
              const m = meta();
              download(exportFilename(payload, m, 'json'), operatorJson(payload, m), 'application/json');
            }}
          >
            Download JSON
          </button>
        </div>
        <div className="export__foot">
          {disabled && <p>Correct the calculator inputs to export a valid result.</p>}
          Built in your browser from this page&apos;s payload ·{' '}
          {payload.method ? `factor set ${payload.method.factor_set_id}` : 'factor set on the public card'} ·{' '}
          {sources > 0 ? `${sources} cited passage${sources === 1 ? '' : 's'}` : 'sources on the public card'}
          {fixture ? ' · FIXTURE DATA, marked in the file' : ''}
        </div>
      </div>
    </>
  );
}
