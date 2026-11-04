import { ErrorResponse, UserManager, WebStorageStateStore, type User } from 'oidc-client-ts';

// These are public identifiers for a Cognito public client, never credentials.
const issuer = import.meta.env.VITE_COGNITO_ISSUER as string | undefined;
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined;
const loginDomain = import.meta.env.VITE_COGNITO_DOMAIN as string | undefined;
const callback = `${window.location.origin}${window.location.pathname}`;
export const authConfigured = Boolean(issuer && clientId && loginDomain);
export type SignInProvider = 'COGNITO' | 'Google' | 'Facebook';
// Enable only after that provider has completed its own live verification.
export const socialProviders = {
  Facebook: import.meta.env.VITE_FACEBOOK_LOGIN_ENABLED === 'true',
  Google: import.meta.env.VITE_GOOGLE_LOGIN_ENABLED === 'true',
};

function operatorReturnTo(state: unknown): string {
  if (state && typeof state === 'object' && 'returnTo' in state) {
    const path = (state as { returnTo?: unknown }).returnTo;
    if (typeof path === 'string' && path.startsWith('#/operator/')) return path;
  }
  return '#/';
}
function restoreRoute(path: string): void {
  window.history.replaceState(null, '', `${window.location.pathname}${path}`);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

// Keep an existing auth domain stable during Cognito custom-domain rollout:
// issuer/JWKS still come from discovery, but login endpoints follow the
// explicitly configured domain until the new hostname is ready and tested.
const authBase = loginDomain
  ? (loginDomain.startsWith('https://') ? loginDomain : `https://${loginDomain}`)
  : null;

const manager = authConfigured ? new UserManager({
  authority: issuer!,
  metadataSeed: {
    authorization_endpoint: new URL('/oauth2/authorize', authBase!).href,
    token_endpoint: new URL('/oauth2/token', authBase!).href,
    userinfo_endpoint: new URL('/oauth2/userInfo', authBase!).href,
    revocation_endpoint: new URL('/oauth2/revoke', authBase!).href,
  },
  client_id: clientId!,
  redirect_uri: callback,
  response_type: 'code',
  scope: 'openid email',
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  automaticSilentRenew: false,
}) : null;

// A single initialization also handles React StrictMode's repeated effects.
let initialization: Promise<User | null> | undefined;
export function initializeAuth(): Promise<User | null> {
  if (!manager) return Promise.resolve(null);
  if (!initialization) initialization = (async () => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('state') && (params.has('code') || params.has('error'))) {
      try {
        const user = await manager.signinRedirectCallback();
        restoreRoute(operatorReturnTo(user.state));
        return user;
      } catch (error) {
        restoreRoute(operatorReturnTo(error instanceof ErrorResponse ? error.state : null));
        throw error;
      }
    }
    const user = await manager.getUser();
    return user && !user.expired ? user : null;
  })();
  return initialization;
}

export async function signIn(provider: SignInProvider = 'COGNITO'): Promise<void> {
  if (!manager) throw new Error('Sign-in has not been configured.');
  if (provider !== 'COGNITO' && !socialProviders[provider]) throw new Error('Provider unavailable.');
  await manager.signinRedirect({
    nonce: crypto.randomUUID(),
    state: { returnTo: window.location.hash },
    extraQueryParams: { identity_provider: provider },
  });
}

export async function signOut(): Promise<void> {
  if (!manager || !loginDomain || !clientId) return;
  await manager.removeUser();
  const url = new URL('/logout', loginDomain.startsWith('https://') ? loginDomain : `https://${loginDomain}`);
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('logout_uri', callback);
  window.location.assign(url.toString());
}
