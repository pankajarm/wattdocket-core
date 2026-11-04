import { useEffect, useMemo, useState } from 'react';
import { getFacilityIndex, getFacilityPayload } from '../api';
import { AtlasMap } from '../components/AtlasMap';
import { compact, exact } from '../format';
import { operatorHref, publicHref } from '../router';
import type { Claim, FacilityIndexEntry, FacilityPayload } from '../types';
import { RecordQuality } from '../components/RecordQuality';
import { DataProvenance } from '../components/DataProvenance';
import { dateLabel, safeSourceUrl, mapLocationCounts } from '../dataPresentation';

type IndexLoad =
  | { status: 'loading' }
  | { status: 'ready'; entries: FacilityIndexEntry[]; fixture: boolean; truncated: boolean; indexAsOf: string | null }
  | { status: 'error'; message: string };
type CardLoad =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; id: string; payload: FacilityPayload; fixture: boolean }
  | { status: 'error'; message: string };
type Site = { representative: FacilityIndexEntry; records: FacilityIndexEntry[] };

function siteKey(entry: FacilityIndexEntry): string {
  return entry.site_id || entry.registry_id || entry.id;
}

function groupSites(entries: FacilityIndexEntry[]): Site[] {
  const groups = new Map<string, Site>();
  for (const entry of entries) {
    const key = siteKey(entry);
    const existing = groups.get(key);
    if (existing) existing.records.push(entry);
    else groups.set(key, { representative: entry, records: [entry] });
  }
  return [...groups.values()];
}

function searchable(entry: FacilityIndexEntry): string {
  return [entry.name, entry.address_line, entry.city, entry.state, entry.zip, entry.registry_id]
    .join(' ').toLowerCase();
}

function matchesQuery(entry: FacilityIndexEntry, needle: string): boolean {
  if (searchable(entry).includes(needle)) return true;
  // The same street may exist in multiple cities; a full-address match must
  // agree with the location components supplied after the first comma.
  const street = needle.includes(',') ? needle.split(',')[0]?.trim() : '';
  if (!street || entry.address_line.toLowerCase() !== street) return false;
  const words = (text: string) => text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const location = new Set(words(`${entry.city} ${entry.state} ${entry.zip}`));
  const requestedLocation = words(needle.slice(needle.indexOf(',') + 1));
  return requestedLocation.length > 0 && requestedLocation.every((word) => location.has(word));
}

function place(entry: FacilityIndexEntry): string {
  return [entry.city, entry.state].filter(Boolean).join(', ');
}

function claimValue(claim: Claim | undefined): string {
  if (!claim) return 'Not available';
  if (!claim.renderable) return 'Evidence unavailable';
  if (claim.value.kind === 'NOT_PUBLISHED') return 'Not published';
  if (claim.value.kind === 'BAND') {
    return `${compact(claim.value.low, claim.metric)}–${compact(claim.value.high, claim.metric)} ${claim.value.unit}`;
  }
  return `${exact(claim.value.value, claim.metric)} ${claim.value.unit}`;
}

/** The atlas keeps the user's map context while they inspect an ECHO record. */
export function AtlasPage({ initialQuery, selectedId, onSelectFacility }: {
  initialQuery: string;
  selectedId: string | null | undefined;
  onSelectFacility: (id: string | null) => void;
}) {
  const [load, setLoad] = useState<IndexLoad>({ status: 'loading' });
  const [card, setCard] = useState<CardLoad>({ status: 'idle' });
  const [query, setQuery] = useState(initialQuery);
  const [state, setState] = useState('ALL');
  const [visibleCount, setVisibleCount] = useState(100);
  const [focusToken, setFocusToken] = useState(0);
  const [mobileView, setMobileView] = useState<'map' | 'list' | 'detail'>('map');
  useEffect(() => {
    // Separate mobile panels share the page scroll position. Start each view
    // below the header rather than leaving its heading behind the sticky nav.
    if (typeof window !== 'undefined' && window.matchMedia?.('(max-width: 1000px)').matches) {
      window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
    }
  }, [mobileView]);

  useEffect(() => {
    let cancelled = false;
    getFacilityIndex().then((res) => {
      if (!cancelled) {
        setLoad({ status: 'ready', entries: res.data, fixture: res.source === 'fixture', truncated: res.truncated === true, indexAsOf: res.indexAsOf ?? null });
        if (selectedId === undefined) {
          const first = !initialQuery.trim() ? res.data.find((entry) => entry.hero) ?? res.data[0] : undefined;
          onSelectFacility(first?.id ?? null);
        }
      }
    }).catch((err: unknown) => {
      if (!cancelled) setLoad({ status: 'error', message: err instanceof Error ? err.message : String(err) });
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { setQuery(initialQuery); }, [initialQuery]);
  useEffect(() => { setVisibleCount(100); }, [query, state]);

  useEffect(() => {
    if (!selectedId) { setCard({ status: 'idle' }); return; }
    let cancelled = false;
    setCard({ status: 'loading' });
    getFacilityPayload(selectedId).then((res) => {
      if (!cancelled) setCard({ status: 'ready', id: selectedId, payload: res.data, fixture: res.source === 'fixture' });
    }).catch((err: unknown) => {
      if (!cancelled) setCard({ status: 'error', message: err instanceof Error ? err.message : String(err) });
    });
    return () => { cancelled = true; };
  }, [selectedId]);

  const entries = load.status === 'ready' ? load.entries : [];
  const sites = useMemo(() => groupSites(entries), [entries]);
  const states = useMemo(() => [...new Set(entries.map((entry) => entry.state).filter(Boolean))].sort(), [entries]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sites.flatMap((site) => {
      const matched = site.records.find((entry) =>
        (state === 'ALL' || entry.state === state) && (!needle || matchesQuery(entry, needle)),
      );
      return matched ? [{ ...site, representative: matched }] : [];
    });
  }, [sites, query, state]);
  const mapEntries = useMemo(() => filtered.map((site) => site.representative), [filtered]);
  const locationCounts = mapLocationCounts(mapEntries);
  const selectedSite = sites.find((site) => site.records.some((record) => record.id === selectedId));
  const selectedRecord = selectedSite?.records.find((record) => record.id === selectedId);
  // Search results and markers represent a grouped site. A user can choose a
  // sibling record from its detail panel even if that sibling's name did not
  // match the search; it remains visible as long as its site is in the results.
  const selectionVisible = selectedSite && filtered.some(
    (site) => siteKey(site.representative) === siteKey(selectedSite.representative),
  );
  const selected = selectionVisible ? selectedSite : undefined;
  const filteredSelected = selected && filtered.find((site) => siteKey(site.representative) === siteKey(selected.representative));
  const currentCard = card.status === 'ready' && card.id === selectedId ? card : null;
  const canCountSites = false; // Source records do not establish distinct physical sites.

  function selectSite(id: string) {
    if (id !== selectedId) setCard({ status: 'loading' });
    setFocusToken((token) => token + 1);
    onSelectFacility(id);
    setMobileView('detail');
  }

  return (
    <div className="atlas-shell">
      <div className="atlas-mobile-switch" role="group" aria-label="Atlas view">
        <button type="button" aria-pressed={mobileView === 'map'} onClick={() => setMobileView('map')}>Map</button>
        <button type="button" aria-pressed={mobileView === 'list'} onClick={() => setMobileView('list')}>Results</button>
        <button type="button" aria-pressed={mobileView === 'detail'} onClick={() => setMobileView('detail')}>Details</button>
      </div>

      <main className={`atlas-layout atlas-layout--mobile-${mobileView}`}>
        <aside className="atlas-sidebar" aria-label="Explore facilities">
          <div className="atlas-sidebar__head">
            <h1 className="atlas-sidebar__title">Explore the record.</h1>
            <p className="atlas-sidebar__description">Explore facility listings and see what the public record can tell us about each place.</p>
            <label className="atlas-search">
              <span className="atlas-sr-only">Search by name, address, city, state or source ID</span>
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, address or city" />
            </label>
            <a className="atlas-address-link" href="#/address">Look up any US street address <span aria-hidden="true">→</span></a>
            <div className="atlas-filterbar">
              <label className="atlas-select"><span>State</span>
                <select value={state} onChange={(event) => setState(event.target.value)}>
                  <option value="ALL">All states</option>
                  {states.map((code) => <option key={code} value={code}>{code}</option>)}
                </select>
              </label>
              {(state !== 'ALL' || query) && <button className="atlas-clear" type="button" onClick={() => { setQuery(''); setState('ALL'); }}>Clear filters</button>}
            </div>
          </div>
          <section className="atlas-results" aria-label="Matching records">
            <div className="atlas-results__header">
              <h2>{load.status === 'loading' ? 'Loading records' : `${filtered.reduce((count, group) => count + group.records.length, 0).toLocaleString()} ${canCountSites ? (filtered.length === 1 ? 'site' : 'sites') : (filtered.length === 1 ? 'record' : 'records')}`}</h2>
              <a href="#/data">Data coverage</a>
            </div>
            <p className="atlas-results__caption">{canCountSites ? 'Records with a shared site identifier are grouped.' : 'The index lists source records; verified site grouping is not available.'}</p>
            {load.status === 'ready' && <p className="atlas-results__caption">{locationCounts.shown.toLocaleString()} reported points shown; {locationCounts.review.toLocaleString()} locations need review. All records remain searchable.</p>}
            {load.status === 'ready' && load.truncated && <p className="atlas-results__caption" role="status">The source returned a partial index; more records may exist.</p>}
            <div className="atlas-results__list">
              {load.status === 'error' && <div className="atlas-empty" role="alert">The facility index is unavailable. Please reload and try again.</div>}
              {load.status === 'ready' && filtered.length === 0 && <div className="atlas-empty">No records match these filters. Try a different place or clear your search.{query.trim() && <p><a href={`#/address?q=${encodeURIComponent(query.trim())}`}>Look up this as a US street address →</a></p>}</div>}
              {filtered.slice(0, visibleCount).map(({ representative: entry, records }) => (
                <button type="button" key={entry.id} onClick={() => selectSite(entry.id)}
                  className={`atlas-result${filteredSelected?.representative.id === entry.id ? ' atlas-result--selected' : ''}`}
                  aria-pressed={filteredSelected?.representative.id === entry.id}>
                  <span className="atlas-result__marker" aria-hidden="true" />
                  <span className="atlas-result__body"><span className="atlas-result__name">{entry.name}</span>
                    <span className="atlas-result__place">{place(entry) || 'Location not published'}</span>
                    <span className="atlas-result__id">{records.length > 1 ? `${records.length} source records` : `${entry.source_label || 'Source'} ${entry.registry_id}`}</span></span>
                  <span className="atlas-result__arrow" aria-hidden="true">›</span>
                </button>
              ))}
              {filtered.length > visibleCount && <button type="button" className="atlas-load-more" onClick={() => setVisibleCount((count) => count + 100)}>Show more results ({filtered.length - visibleCount} remaining)</button>}
            </div>
          </section>
          <details className="atlas-about">
            <summary>About this map</summary>
            <p>The directory joins public facility listings. EPA computing-industry records alone do not establish that a site is a data center. Each listing keeps its source and any regulator-confirmed classification.</p>
            <p>Locations are approximate. A point does not show a site boundary or an electrical connection.</p>
            <a href="#/data">Sources, coverage and latest collection</a>
          </details>
        </aside>

        <section className="atlas-map-area" aria-label="Facility map">
          <AtlasMap facilities={mapEntries} selectedId={filteredSelected?.representative.id ?? null} onSelect={selectSite} focusToken={focusToken} />
          <div className="atlas-map-note"><strong>Public records, placed in context</strong><span>{load.status === 'ready' && load.indexAsOf ? `Listing snapshot retrieved ${dateLabel(load.indexAsOf)}` : 'Collection date unavailable'} · locations are approximate</span><span>{locationCounts.shown.toLocaleString()} reported points shown; {locationCounts.review.toLocaleString()} locations need review</span><a href="#/data">Check source dates</a></div>
        </section>

        <aside className="atlas-detail" aria-label="Selected facility evidence" aria-live="polite">
          {!selected ? (
            <div className="atlas-detail__intro">
              <span className="atlas-detail__intro-mark" aria-hidden="true" />
              {selectedSite && !selectionVisible ? <>
                <h2>Selected record is outside these filters.</h2>
                <p>Clear the filters to see it again, or choose another result. The public card remains available in the navigation.</p>
                <button className="atlas-clear" type="button" onClick={() => { setQuery(''); setState('ALL'); }}>Clear filters</button>
              </> : <>
                <h2>Every place has a story. Start with the record.</h2>
                <p>Choose a point on the map or a result in the list to explore the evidence available for that place.</p>
              </>}
              <div className="atlas-detail__intro-divider" />
              <h3>What you’ll find</h3>
              <ul><li>Publicly listed facility records</li><li>Historical grid context and an optional calculator</li><li>Permit information, where available</li></ul>
              <p className="atlas-detail__intro-foot">Where a figure isn’t published, we say so.</p>
            </div>
          ) : (
            <>
              <div className="atlas-detail__top"><span>Selected record</span><button type="button" className="atlas-detail__close" onClick={() => { onSelectFacility(null); setMobileView('map'); }} aria-label="Close facility details">Close</button></div>
              <h2 className="atlas-detail__title">{selectedRecord?.name}</h2>
              <p className="atlas-detail__place">{selectedRecord && [selectedRecord.address_line, place(selectedRecord), selectedRecord.zip].filter(Boolean).join(' · ')}</p>
              <p className="atlas-detail__class">{currentCard && !currentCard.fixture ? (currentCard.payload.facility.class_label || 'Public facility listing') : selectedRecord?.source_label || 'Public facility listing'}</p>
              {selected.records.length > 1 && <p className="atlas-detail__source">{selected.records.length} source records share this site. The card below covers the selected record.</p>}
              <div className="atlas-detail__actions">
                <a className="atlas-primary-link" href={publicHref(selectedId!)}>Open full public card <span aria-hidden="true">→</span></a>
                <a className="atlas-secondary-link" href={operatorHref(selectedId!)}>View operator bench</a>
              </div>
              {card.status === 'loading' && <div className="atlas-status" role="status">Loading the public record…</div>}
              {card.status === 'error' && <div className="atlas-status" role="status">A detailed card is unavailable for this record. The map listing remains available.</div>}
              {currentCard && currentCard.fixture && <div className="atlas-status" role="status">Demo fixture only: estimates are withheld here because the source is synthetic. Connect the public card API to view evidence.</div>}
              {currentCard && !currentCard.fixture && <FacilityDetails payload={currentCard.payload} />}
              {selected.records.length > 1 && <section className="atlas-detail__section"><h3 className="atlas-detail__heading">Other records at this site</h3><ul className="atlas-detail__siblings">{selected.records.filter((record) => record.id !== selectedId).map((record) => <li key={record.id}><button type="button" onClick={() => selectSite(record.id)}>{record.name} · {record.source_label} {record.registry_id}</button></li>)}</ul></section>}
            </>
          )}
        </aside>
      </main>
    </div>
  );
}

function FacilityDetails({ payload }: { payload: FacilityPayload }) {
  const actual = (metric: Claim['metric']) => payload.claims.find((claim) => claim.metric === metric && claim.scope !== 'scenario');
  const power = actual('power_mw');
  const water = actual('water_gal');
  const diesel = actual('diesel_mw');
  const { grid, facility } = payload;
  const scenarios = payload.claims.filter((claim) => claim.scope === 'scenario');
  return <>
    <RecordQuality facility={facility} />
    <section className="atlas-detail__section">
      <h3 className="atlas-detail__heading">What the record says</h3>
      {facility.record_summary && facility.record_summary.length > 0 && <dl className="record-summary record-summary--compact">{facility.record_summary.map((row, index) => <div key={`${row.label}-${index}`}><dt>{row.label}</dt><dd>{safeSourceUrl(row.source_url) ? <a href={safeSourceUrl(row.source_url)} target="_blank" rel="noreferrer">{row.value}</a> : row.value}</dd></div>)}</dl>}
      <div className="atlas-detail__metrics">
        <div className="atlas-metric"><span className="atlas-metric__label">Facility electricity use</span><strong className="atlas-metric__value">{claimValue(power)}</strong><span className="atlas-metric__note">{power?.value.kind === 'NOT_PUBLISHED' ? power.value.reason : 'See the cited measurement and its period'}</span></div>
        <div className="atlas-metric"><span className="atlas-metric__label">Facility water use</span><strong className="atlas-metric__value">{claimValue(water)}</strong><span className="atlas-metric__note">{water?.value.kind === 'NOT_PUBLISHED' ? water.value.reason : 'See the public card for context'}</span></div>
        <div className="atlas-metric"><span className="atlas-metric__label">{diesel?.label || 'Reported generators'}</span><strong className="atlas-metric__value">{claimValue(diesel)}</strong><span className="atlas-metric__note">{diesel?.unit_note || 'Permit nameplate capacity, where available'}</span></div>
        <div className="atlas-metric"><span className="atlas-metric__label">Grid region</span><strong className="atlas-metric__value">{grid.egrid_subregion || 'Not established'}</strong><span className="atlas-metric__note">{grid.subregion_label || 'No verified geographic binding'}</span></div>
      </div>
    </section>
    {scenarios.length > 0 && <section className="atlas-detail__section"><h3 className="atlas-detail__heading">Try a historical grid scenario</h3><p className="atlas-carbon__lead">The public card includes a separate calculation for a hypothetical load on this grid. It does not measure this facility’s emissions.</p><a href={publicHref(facility.id)}>Open the historical grid calculator →</a></section>}
    <DataProvenance data={payload.data_status} compact />
  </>;
}
