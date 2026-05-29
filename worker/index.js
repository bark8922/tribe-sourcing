// Cloudflare Worker — tribe-sourcing
// Serves index.html for all paths. Email-allowlist gate is enforced client-side
// inside index.html using the ?member= URL param Mikhail passes from Bubble.
//
// Pattern mirrors tribe-circle.tribe-bamboohr.workers.dev: no Cloudflare Access
// at the edge (would break iframe embedding inside overview.tribe.xyz).

export default {
  async fetch(request, env, ctx) {
    return env.ASSETS.fetch(request);
  },
};
