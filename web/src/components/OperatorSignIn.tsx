import { useState } from 'react';
import { authConfigured, signIn, socialProviders, type SignInProvider } from '../auth';
import './operator-auth.css';

const labels: Record<SignInProvider, string> = {
  COGNITO: 'Continue with email',
  Facebook: 'Continue with Facebook',
  Google: 'Continue with Google',
};

function ProviderIcon({ provider }: { provider: SignInProvider }) {
  if (provider === 'Facebook') return <span className="operator-access__facebook" aria-hidden="true">f</span>;
  if (provider === 'Google') return <svg className="operator-access__google" viewBox="0 0 48 48" aria-hidden="true"><path fill="#4285f4" d="M43.6 24.5c0-1.4-.1-2.7-.4-4H24v7.7h11c-.5 2.5-1.9 4.6-4 6v5h6.7c3.9-3.6 5.9-8.8 5.9-14.7Z"/><path fill="#34a853" d="M24 44c5.5 0 10.2-1.8 13.6-4.9l-6.7-5c-1.9 1.3-4.2 2-6.9 2-5.3 0-9.8-3.6-11.4-8.4H5.7v5.2A20.5 20.5 0 0 0 24 44Z"/><path fill="#fbbc05" d="M12.6 27.7a12.3 12.3 0 0 1 0-7.4v-5.2H5.7a20.5 20.5 0 0 0 0 17.8l6.9-5.2Z"/><path fill="#ea4335" d="M24 11.9c3 0 5.8 1 7.9 3.1l5.9-5.9A19.7 19.7 0 0 0 24 4a20.5 20.5 0 0 0-18.3 11.1l6.9 5.2c1.6-4.8 6.1-8.4 11.4-8.4Z"/></svg>;
  return <svg className="operator-access__mail" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.6"/><path d="m4 7 8 6 8-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>;
}

export function OperatorSignIn({ loading, error }: { loading: boolean; error: string | null }) {
  const [starting, setStarting] = useState<SignInProvider | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  async function begin(provider: SignInProvider) {
    setStarting(provider);
    setStartError(null);
    try { await signIn(provider); }
    catch { setStartError('We could not open sign-in. Please try again.'); setStarting(null); }
  }
  const providers: SignInProvider[] = [
    ...(socialProviders.Facebook ? ['Facebook' as const] : []),
    ...(socialProviders.Google ? ['Google' as const] : []),
    'COGNITO',
  ];
  return (
    <main className="operator-access">
      <div className="operator-access__intro">
        <h1>See the full picture for your facility.</h1>
        <p>Bring grid, emissions, water and permit context together in one evidence-led view.</p>
        <div className="operator-access__rule" />
        <p className="operator-access__note">Built on public records and clearly marked estimates. A WattDocket account does not verify facility ownership.</p>
      </div>
      <section className="operator-access__card" aria-labelledby="operator-access-title">
        <h2 id="operator-access-title">Welcome to the bench</h2>
        <p>Sign in or create an account to continue to the operator view.</p>
        {(error || startError) && <p className="operator-access__error" role="alert">{startError || 'Your sign-in could not be completed. Please try again.'}</p>}
        {!authConfigured && <p className="operator-access__error" role="status">Account access is being set up. Please check back soon.</p>}
        <div className="operator-access__providers" aria-label="Sign-in options">
          {providers.map((provider) => <button className={`operator-access__provider operator-access__provider--${provider.toLowerCase()}`} type="button" key={provider} onClick={() => void begin(provider)} disabled={!authConfigured || loading || starting !== null}>
            <ProviderIcon provider={provider} />
            <span>{loading ? 'Checking your session…' : starting === provider ? 'Opening sign-in…' : labels[provider]}</span>
            <svg className="operator-access__arrow" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 18 18 6M6 6h12v12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>)}
        </div>
        <p className="operator-access__fine">You'll complete sign-in securely with your chosen provider. If you already have an account, use the same sign-in method.</p>
        <a href="#/">← Return to the evidence atlas</a>
      </section>
    </main>
  );
}
