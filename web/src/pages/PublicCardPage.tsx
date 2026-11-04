import { useEffect, useState } from 'react';
import type { Claim, FacilityPayload, Metric } from '../types';
import { explainAvailable, getFacilityPayload } from '../api';
import { operatorHref } from '../router';
import { NumberChip } from '../components/NumberChip';
import { SourceDrawer } from '../components/SourceDrawer';
import { GridStrip } from '../components/GridStrip';
import { IntensityChart } from '../components/IntensityChart';
import { ExplainPanel } from '../components/ExplainPanel';
import { PermitPanel } from '../components/PermitPanel';
import { PermitLimitsPanel } from '../components/PermitLimitsPanel';
import { FacilityShare } from '../components/FacilityShare';
import { DataProvenance } from '../components/DataProvenance';
import { ReferenceCalculator } from '../components/ReferenceCalculator';
import { LinkedFacilityDisclosures } from '../components/FacilityDisclosures';
import { RecordQuality } from '../components/RecordQuality';
import { SourceDocuments } from '../components/SourceDocuments';
import { dateLabel, safeSourceUrl } from '../dataPresentation';
import './data-pages.css';

type Load =
  | { status: 'loading' }
  | { status: 'ready'; payload: FacilityPayload; fixture: boolean }
  | { status: 'error'; message: string };

export function PublicCardPage({ facilityId }: { facilityId: string }) {
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [openClaim, setOpenClaim] = useState<Claim | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoad({ status: 'loading' });
    setOpenClaim(null);
    getFacilityPayload(facilityId)
      .then((res) => {
        if (cancelled) return;
        setLoad({ status: 'ready', payload: { ...res.data, release_id: res.releaseId ?? res.data.release_id }, fixture: res.source === 'fixture' });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoad({ status: 'error', message: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [facilityId]);

  if (load.status === 'loading') {
    return (
      <>
        <main className="page page--public">
          <PageSkeleton />
        </main>
      </>
    );
  }

  if (load.status === 'error') {
    return (
      <>
        <main className="page page--public">
          <MissingCard facilityId={facilityId} message={load.message} />
        </main>
      </>
    );
  }

  const { payload, fixture } = load;
  const { facility, grid, claims, hourly, verdict } = payload;
  // Keep the same metric order in numeric figures and collection gaps.
  // Diesel hours is a second unit of the same fact, available in its drawer.
  const CHIP_ORDER: Metric[] = ['power_mw', 'carbon_t', 'water_gal', 'diesel_mw'];
  const chipClaims = CHIP_ORDER.map((m) => claims.find((c) => c.metric === m && c.scope !== 'scenario')).filter(
    (c): c is Claim => c != null,
  );
  const exactMetrics = new Set((payload.disclosures ?? []).filter(group => group.link.inventory_allocation).flatMap(group => group.location.observations.map(row => row.metric)));
  const visibleChipClaims = chipClaims.filter(claim => !(claim.metric === 'water_gal' && [...exactMetrics].some(metric => metric.startsWith('water_'))) && !(claim.metric === 'carbon_t' && [...exactMetrics].some(metric => metric.startsWith('emissions_') || metric === 'reported_ghg_total')));
  const availableClaims = visibleChipClaims.filter(claim => claim.renderable && claim.value.kind !== 'NOT_PUBLISHED');
  const unavailableClaims = visibleChipClaims.filter(claim => !claim.renderable || claim.value.kind === 'NOT_PUBLISHED');
  const scenarioClaims = claims.filter((claim) => claim.scope === 'scenario');
  const production = Boolean(payload.release_id || payload.data_status?.release_id);
  const factorSource = payload.data_status?.sources.find((source) => source.id === 'egrid');
  const factorEvidence = scenarioClaims.flatMap((claim) => claim.citations).find((citation) => citation.url);
  const displayGrid = { ...grid, source_url: grid.source_url || factorSource?.url, evidence_url: grid.evidence_url || factorEvidence?.url, published_at: grid.published_at || factorSource?.published_at };
  const citationCount = claims.reduce((count, claim) => count + claim.citations.length, 0);
  const registry = facility.id.replace(/^[a-z_]+[:_]/i, '');
  const sourceUrl = safeSourceUrl(facility.source_url);

  return (
    <>
      <main
        className={`page page--public${openClaim ? ' page--with-drawer' : ''}`}
        data-hourly={hourly ? `series:${hourly.readings.length}` : 'none'}
      >
        <div className="page__inner">
          <nav className="evidence-path" aria-label="Record navigation"><a href="#/map">Explore records</a><span aria-hidden="true">/</span><span>Public record</span></nav>
          {fixture && <span className="demo">FIXTURE DATA</span>}
          <div className="fachead">
            <div>
              <h1 className="fachead__h1">
                {facility.name}
              </h1>
              <p className="fachead__sub">{[facility.address_line, facility.city, facility.state, facility.zip].filter(Boolean).join(', ')}</p>
              {facility.attribution && <p className="fachead__sub">{facility.attribution}{facility.license && <> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">{facility.license}</a></>}</p>}
              {(facility.site_permit_count ?? 1) > 1 ? (
                <div className="fachead__multi">
                  {facility.site_permit_count} source records are associated with this listing.
                  This card covers the selected record; scenarios apply to a hypothetical load at this location.
                </div>
              ) : null}
              <div className="fachead__sub">
                {facility.class_label ?? (facility.naics ? `Listed under NAICS ${facility.naics}` : 'Public facility listing')} ·{' '}
                {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer">{facility.source_label || 'Source record'} {registry}</a> : <span>{facility.source_label || 'Source record'} {registry}</span>}
                {facility.county ? ` · ${facility.county}` : ''}
                {facility.permit_url ? (
                  <>
                    {' · '}
                    <a
                      className="fachead__permit"
                      href={facility.permit_url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      permit document ↗
                    </a>
                  </>
                ) : null}
              </div>
            </div>
            <div className="fachead__actions">
              <FacilityShare facilityId={facility.id} production={production} />
              <a className="ghostbtn" href={operatorHref(facility.id)}>
                Operator workspace →
              </a>
            </div>
          </div>

          {/* Production introduces the evidence boundaries before quantities.
              Development retains the opt-in explanation panel. */}
          {production && !explainAvailable() ? <p className="evidence-summary">This page follows one public source record. Annual disclosures keep their own reporting boundaries; permit documents and regional grid conditions provide separate evidence.</p> : <ExplainPanel payload={payload} template={verdict.template} enabled />}
          <LinkedFacilityDisclosures groups={payload.disclosures} />
          {!payload.disclosures?.length && <p className="record-disclosure-gap">No reviewed annual disclosure is linked to this record in our collection. <a href="#/reported">Browse the reported data we do have</a>.</p>}

          <section className="evidence-section" aria-labelledby="facility-evidence-title">
          <h2 id="facility-evidence-title" className="evidence-section__heading">What does this record establish?</h2>
          <p className="evidence-section__note">Read the listing and any cited quantities in their original scope. A source record does not establish a complete physical-site footprint.</p>
          <details className="record-identity-details"><summary>Review facility type, identity and location</summary><RecordQuality facility={facility} /></details>
          {facility.record_summary && facility.record_summary.length > 0 && <dl className="record-summary">{facility.record_summary.map((row, index) => <div key={`${row.label}-${index}`}><dt>{row.label}</dt><dd>{safeSourceUrl(row.source_url) ? <a href={safeSourceUrl(row.source_url)} target="_blank" rel="noreferrer">{row.value}</a> : row.value}</dd></div>)}</dl>}
          {availableClaims.length > 0 && <div className="chipgrid">
            {availableClaims.map((claim) => (
              <NumberChip
                key={claim.id}
                claim={claim}
                active={openClaim?.id === claim.id}
                onOpen={setOpenClaim}
              />
            ))}
          </div>}
          {unavailableClaims.length > 0 && <details className="record-gaps"><summary>What remains unavailable for this record?</summary>
            <p>Missing values mean our collected sources do not answer these questions. They are not zeros. Related campus figures, if shown above, keep their own reporting boundaries.</p>
            <dl>{unavailableClaims.map(claim => <div key={claim.id}>
              <dt><button type="button" data-metric={claim.metric} onClick={() => setOpenClaim(claim)} aria-label={`${claim.label || claim.metric}: open source drawer`}>{claim.label || claim.metric.replace(/_/g, ' ')}</button></dt>
              <dd>{!claim.renderable ? claim.blocked_reason || 'Supporting evidence is unavailable.' : claim.value.kind === 'NOT_PUBLISHED' ? claim.value.reason : 'Not available in our sources.'}</dd>
            </div>)}</dl>
            <a href="#/learn">Understand these measures and their limits</a>
          </details>}
          </section>

          <PermitPanel payload={payload} />

          <PermitLimitsPanel payload={payload} />
          <SourceDocuments documents={facility.documents} />

          <section className="evidence-section" aria-labelledby="grid-context-title">
            <h2 id="grid-context-title" className="evidence-section__heading">What does the surrounding grid tell us?</h2>
            <p className="evidence-section__note">The region’s electricity background. A geographic grid region does not establish a facility’s utility connection. <a href="#/grid">Explore reported grid demand</a>.</p>
            <GridStrip grid={displayGrid} hourly={hourly} />
            {hourly && <IntensityChart hourly={hourly} />}
          </section>

          {grid.rate_lb_per_mwh != null && facility.location_quality?.map_eligible !== false && <details className="evidence-scenario"><summary>Open the historical grid calculator</summary>
            <ReferenceCalculator key={facility.id} rate={grid.rate_lb_per_mwh} year={grid.data_vintage} region={grid.subregion_label || grid.egrid_subregion} sourceUrl={displayGrid.source_url} evidenceUrl={displayGrid.evidence_url} />
          </details>}
          <DataProvenance data={payload.data_status} />
          <details className="pagefoot"><summary>About this snapshot</summary><p>{citationCount} cited claim {citationCount === 1 ? 'passage' : 'passages'}. Document statements and registry links appear in their own sections.</p><p>WattDocket snapshot published {dateLabel(payload.data_status?.built_at ?? payload.as_of)}. Source reporting periods are shown separately.</p></details>

        </div>

        <SourceDrawer
          facilityId={facility.id}
          claim={openClaim}
          onClose={() => setOpenClaim(null)}
          onOperator={() => {
            window.location.hash = operatorHref(facility.id);
          }}
        />
      </main>
    </>
  );
}

function PageSkeleton() {
  return (
    <div className="page__inner">
      <div className="skel">
        <div className="skel__line skel__line--h1" />
        <div className="skel__line skel__line--meta" />
        <div className="skel__verdict" />
        <div className="skel__chips">
          {[0, 1, 2, 3].map((i) => (
            <div className="skel__chip" key={i} />
          ))}
        </div>
      </div>
    </div>
  );
}

function MissingCard({ facilityId, message }: { facilityId: string; message: string }) {
  return (
    <div className="state">
      <div className="state__k">CARD UNAVAILABLE</div>
      <h2 className="state__h">This public card is unavailable.</h2>
      <p className="state__p">
        The card could not be loaded from the published data. Please try again or return to the atlas.
        Facility requested:{' '}
        <code>{facilityId}</code>.
      </p>
      <p className="state__p state__p--mono" title={message}>
        The service may be unavailable or the requested record may not exist.
      </p>
      <div className="state__links">
        <button className="ghostbtn" onClick={() => window.location.reload()}>Try again</button>
        <a className="ghostbtn ghostbtn--quiet" href="#/map">
          Explore public records
        </a>
      </div>
    </div>
  );
}
