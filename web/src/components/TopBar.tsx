import { useEffect, useState } from 'react';
import { operatorHref, publicHref } from '../router';
import { ThemeControl } from './ThemeControl';

interface Props {
  active: 'home' | 'learn' | 'reported' | 'map' | 'address' | 'public' | 'operator' | 'data' | 'grid' | null;
  facilityId: string | null;
  signedIn: boolean;
  onSignOut: () => void;
}
const destinations = [
  {id:'home',href:'#/',label:'Overview'},
  {id:'learn',href:'#/learn',label:'Understand'},
  {id:'reported',href:'#/reported',label:'Reported data'},
  {id:'map',href:'#/map',label:'Records map'},
  {id:'grid',href:'#/grid',label:'Regional grid'},
  {id:'data',href:'#/data',label:'Sources'},
] as const;

export function TopBar({ active, facilityId, signedIn, onSignOut }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => { setMenuOpen(false); }, [active]);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false); };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);
  return <>
    <a className="skip-link" href="#page-content" onClick={event => { event.preventDefault(); document.getElementById('page-content')?.focus(); }}>Skip to content</a>
    <header className="top awareness-header" data-menu-open={menuOpen}>
      <a className="brand" href="#/" aria-label="WattDocket home">watt<b>docket</b><span className="brand__purpose">Data centers, explained.</span></a>
      <button className="nav-toggle" type="button" aria-expanded={menuOpen} aria-controls="main-navigation" onClick={() => setMenuOpen(open => !open)}>{menuOpen ? 'Close' : 'Menu'}</button>
      <nav id="main-navigation" className="nav" aria-label="Main navigation" onClick={event => { if ((event.target as HTMLElement).closest('a')) setMenuOpen(false); }}>
        {destinations.map(item => <a key={item.id} href={item.href} className={active === item.id ? 'nav__on' : undefined} aria-current={active === item.id ? 'page' : undefined}>{item.label}</a>)}
        <details className="nav-tools" data-active={['address','public','operator'].includes(active ?? '')}><summary>Tools</summary><div className="nav-tools__menu">
          <a href="#/address" aria-current={active === 'address' ? 'page' : undefined}>Address lookup</a>
          {facilityId ? <><a href={publicHref(facilityId)} aria-current={active === 'public' ? 'page' : undefined}>Selected record</a><a href={operatorHref(facilityId)} aria-current={active === 'operator' ? 'page' : undefined}>Operator workspace</a></> : <span>Select a map record to open its operator workspace.</span>}
          {signedIn && <button type="button" onClick={onSignOut}>Sign out</button>}
        </div></details>
      </nav>
      <div className="top__actions"><ThemeControl /></div>
    </header>
  </>;
}
