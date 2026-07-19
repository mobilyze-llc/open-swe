# Studio2 Open SWE control plane

This runbook installs upstream Open SWE at the exact commit recorded in
`config/mobilyze/studio2-control-plane.json`. The runtime uses Open SWE's existing
LangGraph API, dashboard, store, thread IDs, webhook handlers, and local sandbox provider. The
deployment adds no executor, scheduler, dashboard, or state store.

## Security boundary

The LaunchDaemons run as `_openswectl`, whose login is disabled and whose home is `/var/empty`.
Release files live below `/opt/mobilyze/open-swe-control-plane`, mutable runtime state and the
in-memory runtime's durable `.langgraph_api` files live below
`/var/db/mobilyze-open-swe-control-plane`, logs live below
`/var/log/mobilyze-open-swe-control-plane`, and the root-owned environment file lives at
`/Library/Application Support/MobilyzeOpenSWEControlPlane/env`. These names do not overlap the
Apple execution service identity or roots.

Both processes bind only to localhost. Tailscale Serve exposes the dashboard and its backend
API to authenticated tailnet members. A separate Funnel listener exposes only signed webhook
paths; the GitHub and Linear handlers reject missing or invalid signatures.

The GitHub App installation must use **Only select repositories** with
`mobilyze-llc/open-swe` as its sole repository. Configure exactly the permissions and events in
the checked-in manifest. Set the dashboard OAuth callback to
`https://studio2.tail062eee.ts.net/dashboard/api/auth/callback` and the webhook URL to the
manifest's `github_webhook` endpoint. GitHub's organization Members permission is read-only and
supports the `ALLOWED_GITHUB_ORGS=mobilyze-llc` login gate.

Create a Linear webhook for `Comment.create` at the manifest's `linear_webhook` endpoint, scoped
to the Open SWE team (`OSWE`). The declared upstream seam in
`agent/utils/linear_team_repo_map.py` maps that exact team name to `mobilyze-llc/open-swe`.

## Build and install the pinned release

Run these commands from a clean checkout whose `HEAD` equals the manifest commit:

```bash
SHA=f4e2a6833e403184ee710b102ee9d31bd12a0387
test "$(git rev-parse HEAD)" = "$SHA"
python scripts/mobilyze/studio2_control_plane.py validate
git archive --format=tar.gz --output="/tmp/open-swe-$SHA.tar.gz" "$SHA"
scp "/tmp/open-swe-$SHA.tar.gz" studio2:/tmp/
rsync -aR \
  config/mobilyze/studio2-control-plane.json \
  scripts/mobilyze/studio2_control_plane.py \
  scripts/mobilyze/install_studio2_control_plane.sh \
  scripts/mobilyze/run_studio2_control_plane.sh \
  studio2:/tmp/oswe-29-deploy/
ssh studio2 "sudo /tmp/oswe-29-deploy/scripts/mobilyze/install_studio2_control_plane.sh install /tmp/open-swe-$SHA.tar.gz $SHA"
```

The installer verifies both lockfile hashes before running `uv sync --frozen --no-dev` and
`pnpm install --frozen-lockfile`. It never pulls a branch.

Fill every empty name in the generated environment file without printing it. Generate independent
values for `DASHBOARD_JWT_SECRET`, `GITHUB_WEBHOOK_SECRET`, `LINEAR_WEBHOOK_SECRET`, and
`TOKEN_ENCRYPTION_KEY`; copy the GitHub App identifiers, private key, OAuth secret, and OSWE-scoped
Linear API key from their respective control planes. Keep the file `0640 root:_openswectl`.
The non-secret deployment values are:

```dotenv
ALLOWED_GITHUB_ORGS=mobilyze-llc
DASHBOARD_ALLOWED_ORIGINS=https://studio2.tail062eee.ts.net
DASHBOARD_API_BASE_URL=https://studio2.tail062eee.ts.net
DASHBOARD_BASE_URL=https://studio2.tail062eee.ts.net
DEFAULT_REPO_OWNER=mobilyze-llc
DEFAULT_REPO_NAME=open-swe
LANGGRAPH_URL=http://127.0.0.1:2029
LOCAL_SANDBOX_ROOT_DIR=/var/db/mobilyze-open-swe-control-plane/sandboxes
SANDBOX_TYPE=local
```

`start` fails closed and prints missing names only while any template entry remains empty. An
install with empty entries disables both launchd labels, so a reboot cannot start a partially
configured service.

## Network and service operations

Configure private same-origin access and the signed webhook-only public listener as root:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:3029
tailscale serve --bg --https=443 --set-path=/dashboard/api http://127.0.0.1:2029
tailscale funnel --bg --https=8443 --set-path=/webhooks http://127.0.0.1:2029
sudo scripts/mobilyze/install_studio2_control_plane.sh start
sudo scripts/mobilyze/install_studio2_control_plane.sh status
curl --fail --silent http://127.0.0.1:2029/health
```

Use these managed operations without reading the environment file:

```bash
sudo scripts/mobilyze/install_studio2_control_plane.sh restart
sudo scripts/mobilyze/install_studio2_control_plane.sh stop
sudo scripts/mobilyze/install_studio2_control_plane.sh start
sudo tail -n 100 /var/log/mobilyze-open-swe-control-plane/backend.error.log
sudo tail -n 100 /var/log/mobilyze-open-swe-control-plane/dashboard.error.log
```

Rotate one credential by changing only its named line in the root-owned environment file, then
restart both services. Redact values, authorization headers, cookies, signatures, and private-key
material from captured logs.

## Trigger, persistence, and rollback proof

Send `@openswe report the repository and stop without changing files` once from a GitHub issue,
once from a Linear OSWE issue comment, and once from dashboard chat. Record each deterministic
thread ID and completed run ID from the dashboard API. Restart the services, query those exact
thread IDs again, and record that the completed runs remain visible.

Install a second already-built release before testing rollback. The rollback command only accepts
a release already present below `releases/`; it never fetches code:

```bash
sudo scripts/mobilyze/install_studio2_control_plane.sh rollback <previous-full-sha>
readlink /opt/mobilyze/open-swe-control-plane/current
curl --fail --silent http://127.0.0.1:2029/health
```

Rollback is complete only after both LaunchDaemons are running, health is clean, and the three
recorded threads remain visible.
