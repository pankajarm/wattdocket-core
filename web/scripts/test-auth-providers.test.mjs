import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createRequire } from 'node:module';

const oidcStub = {
  name: 'oidc-stub',
  setup(builder) {
    builder.onResolve({ filter: /^oidc-client-ts$/ }, () => ({ path: 'oidc', namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({ loader: 'js', contents: `
      export class ErrorResponse extends Error { constructor(state) { super('cancelled'); this.state = state; } }
      export class WebStorageStateStore { constructor() {} }
      export class UserManager {
        constructor(settings) { globalThis.managerSettings = settings; }
        async signinRedirect(args) { globalThis.lastSignInArgs = args; }
        async signinRedirectCallback() {
          if (globalThis.callbackErrorState) throw new ErrorResponse(globalThis.callbackErrorState);
          return { state: globalThis.callbackUserState };
        }
        async getUser() { return null; }
        async removeUser() {}
      }
    ` }));
  },
};

async function loadAuth({ facebook = 'false', google = 'false', search = '', callbackState } = {}) {
  globalThis.window = {
    location: { origin: 'https://wattdocket.com', pathname: '/app/index.html', hash: '#/operator/echo%3A123', search },
    sessionStorage: {},
    history: { replaceState(_a, _b, url) { globalThis.restoredUrl = url; } },
    dispatchEvent() {},
  };
  globalThis.HashChangeEvent = class { constructor(name) { this.type = name; } };
  globalThis.callbackErrorState = callbackState;
  globalThis.lastSignInArgs = undefined;
  globalThis.restoredUrl = undefined;
  const bundle = await build({
    entryPoints: [new URL('../src/auth.ts', import.meta.url).pathname],
    bundle: true, write: false, platform: 'node', format: 'cjs', plugins: [oidcStub],
    define: {
      'import.meta.env.VITE_COGNITO_ISSUER': JSON.stringify('https://issuer.example'),
      'import.meta.env.VITE_COGNITO_CLIENT_ID': JSON.stringify('client'),
      'import.meta.env.VITE_COGNITO_DOMAIN': JSON.stringify('https://auth.example'),
      'import.meta.env.VITE_FACEBOOK_LOGIN_ENABLED': JSON.stringify(facebook),
      'import.meta.env.VITE_GOOGLE_LOGIN_ENABLED': JSON.stringify(google),
    },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
  return module.exports;
}

test('provider selection preserves the facility and uses OIDC state and nonce', async () => {
  const auth = await loadAuth({ facebook: 'true', google: 'true' });
  assert.equal(managerSettings.authority, 'https://issuer.example');
  assert.equal(managerSettings.metadataSeed.authorization_endpoint, 'https://auth.example/oauth2/authorize');
  assert.equal(managerSettings.metadataSeed.token_endpoint, 'https://auth.example/oauth2/token');
  for (const provider of ['Facebook', 'Google', 'COGNITO']) {
    await auth.signIn(provider);
    assert.equal(lastSignInArgs.extraQueryParams.identity_provider, provider);
    assert.equal(lastSignInArgs.state.returnTo, '#/operator/echo%3A123');
    assert.ok(lastSignInArgs.nonce);
  }
});

test('disabled social providers cannot be started', async () => {
  const auth = await loadAuth();
  await assert.rejects(auth.signIn('Facebook'), /Provider unavailable/);
  await assert.rejects(auth.signIn('Google'), /Provider unavailable/);
  assert.equal(lastSignInArgs, undefined);
});

test('provider cancellation restores the selected facility and removes the callback query', async () => {
  const auth = await loadAuth({ search: '?error=access_denied&state=opaque', callbackState: { returnTo: '#/operator/echo%3A123' } });
  await assert.rejects(auth.initializeAuth());
  assert.equal(restoredUrl, '/app/index.html#/operator/echo%3A123');
});
