# tribe-sourcing

**Sourcing Dashboard** for Tribe.xyz sourcing leadership.

Deployed at: `tribe-sourcing.tribe-bamboohr.workers.dev` (TBD)
Iframed into: `overview.tribe.xyz` (Bubble) for the 8-email allowlist:
Jacopo, Andrea, Sanja, Gustavo, Martin, Kristjana, Salem, Blake.

## Architecture (mirrors `tribe-circle` + `tribe-dashboard`)

- **Static site** (`index.html`) — no build step. Tailwind via CDN.
- **Data source:** Keboola table `out.c-WBRMBR-weekly-aggregations.sourcing_dashboard_per_sourcer` (added 2026-05-28, code block `b0.c13` of transformation `01kpr0tr0dt5ryf96a5zk85bx7`).
- **Refresh:** TODO — GitHub Actions workflow pulling Keboola Storage API into `data/data.json` (next step).
- **Hosting:** Cloudflare Workers (no Cloudflare Access in front — must be iframable inside Bubble).
- **Auth:** App-side email allowlist. Mikhail passes `?member={email}` from Bubble; client-side check against the 8-email list.
- **Direct visitors:** redirect to overview.tribe.xyz login (or show "open this inside the Tribe Tool" message).

## Phase 1 status

**TS-Summary quarterly rollup** — single tab showing quarterly funnel (Contacted → Hired) + team size + hires per sourcer.

Methodology locked 2026-05-28 (Option C cross-client filter):
- Include sourcer events where (a) sourcer is on Bench/Internal in BambooHR, OR (b) sourcer is on a client but sourced for a *different* client (cross-client work).
- Exclude work where the sourcer's division = the job's client (that's TA work for the client, not internal sourcing).
- Archived jobs included. Tribe internal jobs included. Test clients excluded.

## Future phases

2. Cost of Sourcing Team (join Finance dashboard `actual_spend`)
3. WBR Comments for Context (per-sourcer export)
4. Closing Rates (% of archived roles closed via sourcing hire)
5. Internal vs External TS view (on-client vs off-client breakdown)
6. TS Job Ratio (coverage %)
7. Allocation automation (Monday morning capacity check + Slack alert)

See `SOURCING_DASHBOARD_PLAN.md` in the recruiting workspace for the full plan.
