import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FacilityIndexEntry, ResolveResult } from '../types';
import { getFacilityIndex, resolveAddress } from '../api';
import { looksLikeStreetAddress } from '../geo';
import { navigate, publicHref } from '../router';
import { AddressFallback } from '../components/AddressFallback';

type Load =
  | { status: 'loading' }
  | { status: 'ready'; entries: FacilityIndexEntry[]; fixture: boolean }
  | { status: 'error'; message: string };

type Lookup =
  | { status: 'idle' }
  | { status: 'loading'; query: string }
  | { status: 'ready'; result: ResolveResult }
  | { status: 'error'; query: string; message: string };

function matches(e: FacilityIndexEntry, needle: string): boolean {
  // "45901 Nokes Blvd, Sterling, VA" is a whole address; the index holds the
  // street line on its own, so the part before the first comma is tried too.
  const street = needle.includes(',') ? needle.split(',')[0]!.trim() : '';
  if (street && e.address_line.toLowerCase() === street) {
    // A street line alone is not unique nationwide. A full-address search
    // must also agree with the supplied city/state/ZIP before it suppresses
    // the Census lookup or presents a facility as the match.
    const words = (text: string) => text.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    const location = new Set(words(`${e.city} ${e.state} ${e.zip}`));
    const requestedLocation = words(needle.slice(needle.indexOf(',') + 1));
    if (requestedLocation.length && requestedLocation.every((word) => location.has(word))) return true;
  }
  return (
    e.name.toLowerCase().includes(needle) ||
    e.address_line.toLowerCase().includes(needle) ||
    e.city.toLowerCase().includes(needle) ||
    e.state.toLowerCase().includes(needle) ||
    e.zip.includes(needle) ||
    e.registry_id.includes(needle)
  );
}

export function SearchPage({ initialQuery }: { initialQuery: string }) {
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [q, setQ] = useState(initialQuery);
  const [state, setState] = useState('ALL');

  useEffect(() => {
    let cancelled = false;
    getFacilityIndex()
      .then((res) => {
        if (!cancelled) setLoad({ status: 'ready', entries: res.data, fixture: res.source === 'fixture' });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ status: 'error', message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setQ(initialQuery);
  }, [initialQuery]);

  const entries = useMemo(() => (load.status === 'ready' ? load.entries : []), [load]);

  const states = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of entries) counts.set(e.state, (counts.get(e.state) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [entries]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return entries.filter((e) => {
      if (state !== 'ALL' && e.state !== state) return false;
      if (!needle) return true;
      return matches(e, needle);
    });
  }, [entries, q, state]);

  // Addresses outside the collected index can still receive approximate
  // regional context: geocode, point-in-polygon, and an optional calculator.
  const [lookup, setLookup] = useState<Lookup>({ status: 'idle' });
  const lookupSeq = useRef(0);
  useEffect(() => () => { lookupSeq.current++; }, []);
  const runLookup = useCallback(
    (query: string) => {
      const seq = ++lookupSeq.current;
      setLookup({ status: 'loading', query });
      resolveAddress(query, entries)
        .then((result) => {
          if (seq === lookupSeq.current) setLookup({ status: 'ready', result });
        })
        .catch((err: unknown) => {
          if (seq === lookupSeq.current)
            setLookup({
              status: 'error',
              query,
              message: err instanceof Error ? err.message : String(err),
            });
        });
    },
    [entries],
  );

  useEffect(() => {
    lookupSeq.current++;
    setLookup({ status: 'idle' });
    if (load.status !== 'ready') return;
    const needle = initialQuery.trim().toLowerCase();
    if (!needle || !looksLikeStreetAddress(needle)) return;
    if (load.entries.some((e) => matches(e, needle))) return;
    runLookup(initialQuery.trim());
  }, [initialQuery, load, runLookup]);

  const heroes = entries.filter((e) => e.hero);

  return (
    <>
      <main className="page page--search evidence-address">
        <section className="hero">
          {load.status === 'ready' && load.fixture && <span className="demo">STATIC SNAPSHOT</span>}
          <h1 className="hero__h1">Search records or a US address.</h1>
          <p className="hero__sub">Look for a name or address in our public-record collection. A US address lookup can also show geographic grid context. Neither a listing nor a geocoded address confirms an operating data center.</p>
          <label className="address-search-label" htmlFor="address-search">Name, street address or source ID</label>

          <form
            className="bigsearch"
            onSubmit={(e) => {
              e.preventDefault();
              const query = q.trim();
              const route = `/address?q=${encodeURIComponent(query)}`;
              // Navigating to the existing hash emits no hashchange. Allow a
              // user to retry an address after a transient geocoder failure.
              if (window.location.hash === `#${route}` && load.status === 'ready' &&
                  looksLikeStreetAddress(query) && !entries.some((entry) => matches(entry, query.toLowerCase()))) {
                runLookup(query);
              } else {
                navigate(route);
              }
            }}
          >
            <span className="bigsearch__icon" aria-hidden="true">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="6.5" cy="6.5" r="4.4" stroke="currentColor" strokeWidth="1.4" />
              <path d="M9.8 9.8 14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            </span>
            <input
              id="address-search"
              className="bigsearch__input"
              value={q}
              onChange={(e) => {
                // Invalidate both the visible answer and any request in flight
                // as soon as the user edits the address it belongs to.
                lookupSeq.current++;
                setLookup({ status: 'idle' });
                setQ(e.target.value);
              }}
              placeholder="Street address, name or source ID"
              aria-label="Search public records by address, name or source ID"
            />
            <button type="submit" className="bigsearch__btn">
              Search records
            </button>
          </form>
          <div className="hero__meta">
            {load.status === 'ready' ? (
              <>
                {entries.length.toLocaleString()} searchable source records. Coverage is incomplete.
              </>
            ) : (
              'Loading the published facility index…'
            )}
          </div>
        </section>

        {load.status === 'error' && (
          <div className="state state--inline">
            <div className="state__k">INDEX UNAVAILABLE</div>
            <p className="state__p state__p--mono">{load.message}</p>
          </div>
        )}

        {heroes.length > 0 && (
          <section className="featured">
            <div className="sectionhead">Example records with additional evidence</div>
            <div className="featured__grid">
              {heroes.map((h) => (
                <a className="fcard" key={h.id} href={publicHref(h.id)}>
                  <span className="fcard__k">PUBLIC CARD</span>
                  <span className="fcard__addr">{h.address_line}</span>
                  <span className="fcard__city">
                    {h.city}, {h.state} {h.zip}
                  </span>
                  <span className="fcard__name">{h.name}</span>
                  <span className="fcard__go">Open ↗</span>
                </a>
              ))}
            </div>
          </section>
        )}

        <div className="searchgrid">
          <section className="results">
            <div className="results__bar">
              <div className="sectionhead sectionhead--inline">
                {filtered.length} {filtered.length === 1 ? 'RECORD' : 'RECORDS'}
                {q.trim() ? ` MATCHING “${q.trim()}”` : ''}
              </div>
              <label className="select">
                <span>State</span>
                <select value={state} onChange={(e) => setState(e.target.value)}>
                  <option value="ALL">All</option>
                  {states.map(([code, count]) => (
                    <option key={code} value={code}>
                      {code} ({count})
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="rlist">
              <div role="list" aria-label="Matching facility records">
                {filtered.map((e) => (
                  <div className="rlist__item" key={e.id} role="listitem">
                    <a className="rrow" href={publicHref(e.id)}>
                      <span className="rrow__name">
                        {e.name}
                        {e.hero && <em className="rrow__tag">CARD</em>}
                      </span>
                      <span className="rrow__addr">
                        {e.address_line}
                        {e.duplicate_of && <span className="rrow__dup"> · duplicate listing</span>}
                      </span>
                      <span className="rrow__city">
                        {e.city}, {e.state} {e.zip}
                      </span>
                      <span className="rrow__id">{e.source_label || "Source"} {e.registry_id}</span>
                    </a>
                  </div>
                ))}
              </div>

              {load.status === 'ready' && filtered.length === 0 && lookup.status === 'idle' && (
                <div className="rlist__empty">
                  No collected record matches that search. This does not establish that no data center exists here. Try a street name, city or source ID.
                  {looksLikeStreetAddress(q.trim()) && (
                    <button
                      type="button"
                      className="afb__btn"
                      onClick={() => runLookup(q.trim())}
                    >
                      Look up “{q.trim()}” as a US street address
                    </button>
                  )}
                </div>
              )}
            </div>

            {lookup.status === 'loading' && (
              <div className="state state--inline">
                <div className="state__k">LOOKING UP THE ADDRESS</div>
                <p className="state__p">
                  Asking the US Census geocoder where “{lookup.query}” is, then which eGRID
                  subregion contains it…
                </p>
              </div>
            )}
            {lookup.status === 'error' && (
              <div className="state state--inline">
                <div className="state__k">NO ADDRESS FALLBACK</div>
                <p className="state__p">{lookup.message}</p>
              </div>
            )}
            {lookup.status === 'ready' && <AddressFallback result={lookup.result} />}
          </section>

          <aside className="rail evidence-address-guide" aria-label="How to read a result">
            <div className="rail__block">
              <h2>What will a result tell me?</h2>
              <dl>
                <div><dt>Reported figures</dt><dd>Annual quantities belong to the campus, building or reporting entity named by the source.</dd></div>
                <div><dt>Document evidence</dt><dd>Permit quotations describe the cited document. They do not establish actual operation or current legal effect.</dd></div>
                <div><dt>Regional context</dt><dd>A geographic grid region describes an area. It does not establish a facility's serving utility.</dd></div>
                <div><dt>Illustrative calculations</dt><dd>A chosen load and historical grid factor explain a scenario, with the assumptions visible.</dd></div>
              </dl>
            </div>
            <div className="rail__block">
              <h2>Start with a question</h2>
              <ul className="evidence-guide-links"><li><a href="#/learn/electricity">How much electricity is reported?</a></li><li><a href="#/learn/water">What do water figures measure?</a></li><li><a href="#/learn/emissions">Why do emissions methods differ?</a></li></ul>
            </div>
            <div className="rail__block">
              <h2>Keep the gaps visible</h2>
              <p className="rail__p">Some records have coordinates that need review and are not placed on the map. A missing figure is unavailable in our collection, not zero.</p>
              <a href="#/data">Review sources and coverage</a>
              <a className="evidence-guide-map" href={`#/map?q=${encodeURIComponent(q.trim())}`}>Explore these records on the map</a>
            </div>
          </aside>
        </div>
      </main>
    </>
  );
}
