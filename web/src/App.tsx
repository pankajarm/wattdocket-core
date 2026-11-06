import { useEffect, useState } from 'react';
import { useRoute } from './router';
import { PublicCardPage } from './pages/PublicCardPage';
import { OperatorBenchPage } from './pages/OperatorBenchPage';
import { SearchPage } from './pages/SearchPage';
import { AtlasPage } from './pages/AtlasPage';
import { TopBar } from './components/TopBar';
import { SiteFooter } from './components/SiteFooter';
import { OperatorSignIn } from './components/OperatorSignIn';
import { DataStatusPage } from './pages/DataStatusPage';
import { GridPage } from './pages/GridPage';
import { AwarenessHomePage } from './pages/AwarenessHomePage';
import { ReportedDataPage } from './pages/ReportedDataPage';
import { LearnPage } from './pages/LearnPage';
import { initializeAuth, signOut } from './auth';
import type { User } from 'oidc-client-ts';
import './atlas.css';
import './pages/evidence-pages.css';

// Snapshot cards use an underscore in their internal IDs; the index uses a
// colon. Keep the index form as the selected identity across views.
function indexFacilityId(id: string): string {
  return id.replace(/^echo_/i, 'echo:');
}

export default function App() {
  const route = useRoute();
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  useEffect(() => {
    let mounted = true;
    initializeAuth().then((account) => { if (mounted) setUser(account); })
      .catch(() => { if (mounted) setAuthError('Sign-in could not be completed.'); })
      .finally(() => { if (mounted) setAuthLoading(false); });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    if (!user?.expires_at) return;
    const delay = Math.max(0, user.expires_at * 1000 - Date.now());
    const timer = window.setTimeout(() => setUser(null), delay);
    return () => window.clearTimeout(timer);
  }, [user]);
  const routeFacility = route.name === 'public' || route.name === 'operator'
    ? indexFacilityId(route.facilityId) : null;
  // Undefined means the atlas hasn't chosen its initial hero yet; null is an
  // intentional empty selection (including clearing the selection on the map).
  const [selectedId, setSelectedId] = useState<string | null | undefined>(() => routeFacility ?? undefined);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
  }, [route]);
  useEffect(() => {
    if (routeFacility) setSelectedId(routeFacility);
  }, [routeFacility]);

  const active = route.name === 'search' ? 'map' : route.name === 'notFound' ? null : route.name;
  let content;
  if (route.name === 'home') content = <AwarenessHomePage />;
  else if (route.name === 'learn') content = <LearnPage key={route.topic ?? 'index'} topic={route.topic} />;
  else if (route.name === 'reported') content = <ReportedDataPage key={`${route.query}:${route.family}:${route.locationId}:${route.view}`} query={route.query} family={route.family} locationId={route.locationId} view={route.view} />;
  else if (route.name === 'public') content = <PublicCardPage key={route.facilityId} facilityId={route.facilityId} />;
  else if (route.name === 'operator') content = user && !user.expired
    ? <OperatorBenchPage key={route.facilityId} facilityId={route.facilityId} />
    : <OperatorSignIn loading={authLoading} error={authError} />;
  else if (route.name === 'search') content = <AtlasPage initialQuery={route.query} selectedId={selectedId} onSelectFacility={setSelectedId} />;
  else if (route.name === 'address') content = <SearchPage initialQuery={route.query} />;
  else if (route.name === 'data') content = <DataStatusPage />;
  else if (route.name === 'grid') content = <GridPage />;
  else content = (
    <main className="page">
      <div className="state">
        <div className="state__k">404</div>
        <h1 className="state__h">That route does not exist.</h1>
        <p className="state__p">
          Explore reported data, public records, regional grid observations, or the questions these sources can answer.
        </p>
        <div className="state__links">
          <a className="ghostbtn" href="#/">Return to WattDocket</a>
        </div>
      </div>
    </main>
  );

  return (
    <>
      <TopBar active={active} facilityId={routeFacility ?? selectedId ?? null} signedIn={Boolean(user && !user.expired)} onSignOut={() => void signOut()} />
      <div className="app-content" id="page-content" tabIndex={-1}>{content}</div>
      <SiteFooter facilityId={routeFacility ?? selectedId ?? null} />
    </>
  );
}
