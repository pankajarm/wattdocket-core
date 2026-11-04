import { useState } from 'react';
import { navigate, operatorHref, publicHref } from '../router';
import { ThemeControl } from './ThemeControl';

interface Props {
  active: 'map' | 'address' | 'public' | 'operator' | 'data' | 'grid' | null;
  facilityId: string | null;
  signedIn: boolean;
  onSignOut: () => void;
}

/** One navigation bar for the atlas and all of its supporting views. */
export function TopBar({ active, facilityId, signedIn, onSignOut }: Props) {
  const [query, setQuery] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className="top" data-menu-open={menuOpen}>
      <a className="brand" href="#/" aria-label="WattDocket home">
        watt<b>docket</b>
      </a>
      <span className="top__tagline">Public records, in perspective.</span>
      <form className="search" onSubmit={(event) => {
        event.preventDefault();
        navigate(`/?q=${encodeURIComponent(query.trim())}`);
      }}>
        <span className="search__icon" aria-hidden="true">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
            <circle cx="6.5" cy="6.5" r="4.4" stroke="currentColor" strokeWidth="1.4" />
            <path d="M9.8 9.8 14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </span>
        <input className="search__input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Address, facility name, or source ID" aria-label="Search facilities" />
        {query && <button type="button" className="search__clear" onClick={() => setQuery('')} aria-label="Clear search">✕</button>}
      </form>
      <button className="nav-toggle" type="button" aria-expanded={menuOpen} aria-controls="main-navigation" onClick={() => setMenuOpen((open) => !open)}>{menuOpen ? 'Close menu' : 'Menu'}</button>
      <nav id="main-navigation" className="nav" aria-label="Main navigation" onClick={() => setMenuOpen(false)}>
        <a href="#/" className={active === 'map' ? 'nav__on' : undefined} aria-current={active === 'map' ? 'page' : undefined}>
          Map
        </a>
        <a href="#/address" className={active === 'address' ? 'nav__on' : undefined} aria-current={active === 'address' ? 'page' : undefined}>
          Address
        </a>
        <a href="#/grid" className={active === 'grid' ? 'nav__on' : undefined} aria-current={active === 'grid' ? 'page' : undefined}>Grid</a>
        <a href="#/data" className={active === 'data' ? 'nav__on' : undefined} aria-current={active === 'data' ? 'page' : undefined}>Data</a>
        {facilityId ? (
          <>
            <a href={publicHref(facilityId)} className={active === 'public' ? 'nav__on' : undefined} aria-current={active === 'public' ? 'page' : undefined}>
              Public card
            </a>
            <a href={operatorHref(facilityId)} className={active === 'operator' ? 'nav__on' : undefined} aria-current={active === 'operator' ? 'page' : undefined}>
              Operator
            </a>
          </>
        ) : (
          <>
            <span className="nav__disabled" aria-disabled="true" aria-label="Public card: select a facility on the map first" title="Select a facility on the map to open its public card">Public card</span>
            <span className="nav__disabled" aria-disabled="true" aria-label="Operator: select a facility on the map first" title="Select a facility on the map to open its operator bench">Operator</span>
          </>
        )}
        {signedIn && <button className="nav-mobile-signout" type="button" onClick={onSignOut}>Sign out</button>}
      </nav>
      <div className="top__actions">
        <ThemeControl />
        {signedIn && <button type="button" className="nav__signout" aria-label="Sign out" title="Sign out" onClick={onSignOut}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M9 4H4v16h5M9 12h12m-4-4 4 4-4 4" /></svg>
          <span>Sign out</span>
        </button>}
      </div>
    </header>
  );
}
