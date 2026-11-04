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

  // The index is 479 ECHO rows. Any other US street address falls back to the
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
  const vaCount = entries.filter((e) => e.state === 'VA').length;
  // One EPA facility can hold several air permits, and each permit is its own
  // record here. Counting records as facilities overstates the number of
  // buildings by 13, which is the sort of thing this product exists to stop.

  return (
    <>
      <main className="page page--search">
        <section className="hero">
          {load.status === 'ready' && load.fixture && <span className="demo">STATIC SNAPSHOT</span>}
          <div className="eyebrow">
            <span className="eyebrow__dot" /> PUBLIC RECORDS · US LOCATIONS
          </div>
          <h1 className="hero__h1">
            Every number here
            <br />
            has a receipt,
            <br />
            and its assumptions.
          </h1>
          <p className="hero__sub">
            Find a public facility listing or look up a US street address. Explore source records,
            available permit evidence and geographic grid context. Missing operating measurements
            stay unavailable; reference scenarios appear separately with their assumptions.
          </p>

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
              className="bigsearch__input"
              value={q}
              onChange={(e) => {
                // Invalidate both the visible answer and any request in flight
                // as soon as the user edits the address it belongs to.
                lookupSeq.current++;
                setLookup({ status: 'idle' });
                setQ(e.target.value);
              }}
              placeholder="45901 Nokes Blvd, Sterling, VA — or a facility name, or a source ID"
              aria-label="Search facilities by address, name or registry id"
            />
            <button type="submit" className="bigsearch__btn">
              Find the facility
            </button>
          </form>
          <div className="hero__meta">
            {load.status === 'ready' ? (
              <>
                {entries.length} source records · {vaCount} records in Virginia
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
            <div className="sectionhead">Start with a card that is built out</div>
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
                  Nothing in the index matches that. The index contains {entries.length} public records and does not cover every US
                  campus — try a street name, a city, or a registry id.
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

          <aside className="rail">
            <div className="rail__block">
              <div className="rail__k">WHAT EVERY CARD CARRIES</div>
              <ol className="rail__list">
                <li>
                  <b>Value</b> with its unit and evidence basis
                </li>
                <li>
                  <b>Range</b> — with the scenario or reported basis stated
                </li>
                <li>
                  <b>Badge</b> — measured, reported, modeled or estimated
                </li>
                <li>
                  <b>Source period</b>, retrieval date and evidence
                </li>
              </ol>
            </div>

            <div className="rail__block">
              <div className="rail__k">BADGES, IN ORDER OF EVIDENCE</div>
              <div className="rail__badges">
                <span className="badge badge--measured">measured</span>
                <span className="badge badge--reported">reported</span>
                <span className="badge badge--modeled">modeled</span>
                <span className="badge badge--estimated">estimated</span>
              </div>
              <p className="rail__p">
                Nothing else appears on a chip. A provisional factor set adds one orthogonal flag,
                and never upgrades itself.
              </p>
            </div>

            <div className="rail__block">
              <div className="rail__k">NOT ON THIS SITE</div>
              <ul className="rail__no">
                <li>company rankings</li>
                <li>an aggregate “AI is bad” score</li>
                <li>offsets and REC marketing</li>
                <li>generated imagery</li>
                <li>legal conclusions</li>
              </ul>
            </div>

            <div className="rail__block rail__block--quiet">
              <div className="rail__k">ABOUT THE DATA</div>
              <p className="rail__p">
                Facility identity comes from attributed public listings. Coverage varies by source. Reference scenarios stay separate from facility measurements.
              </p>
            </div>
          </aside>
        </div>
      </main>
    </>
  );
}
