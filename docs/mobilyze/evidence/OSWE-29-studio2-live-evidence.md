# OSWE-29 studio2 evidence

Captured at `2026-07-19T17:03:41Z`. Values, authorization headers, cookies, webhook
signatures, and private-key material are omitted.

## Proven

- Baseline and merged PR #1: `4c9201093851f54aba60cf6ec0e814aebb828076`.
- Dedicated identity: `_openswectl`, UID/GID 451, `/var/empty` home, `/usr/bin/false`
  shell, hidden, disabled authentication authority.
- Deployment root: `/opt/mobilyze/open-swe-control-plane` (`root:wheel`, `0755`).
- State root: `/var/db/mobilyze/open-swe-control-plane` (`_openswectl:_openswectl`,
  `0700`).
- Log root: `/var/log/mobilyze/open-swe-control-plane`
  (`_openswectl:_openswectl`, `0750`).
- Environment file:
  `/Library/Application Support/MobilyzeOpenSWEControlPlane/env`
  (`root:_openswectl`, `0640`); it contains names only at this checkpoint.
- Pinned application release: `f4e2a6833e403184ee710b102ee9d31bd12a0387`.
- Rollback release retained: `4c9201093851f54aba60cf6ec0e814aebb828076`.
- `uv.lock` SHA-256:
  `43aca3bf3c7ac2e975682b01039e10c5998d7a03d70790225c60b673abda770d`.
- `ui/pnpm-lock.yaml` SHA-256:
  `dc5638471a8fbbf0a0d0de4da0c0b4a7a664ac9a27edcf87234bdb3df3df0b6d`.
- Frozen backend dependency install and frozen dashboard dependency install/build
  completed on `studio2`.
- The installed release maps Linear team `Open SWE` (`OSWE`) to
  `mobilyze-llc/open-swe`.
- Launchd labels exist but are disabled and unloaded:
  `com.mobilyze.open-swe-control-plane.backend` and
  `com.mobilyze.open-swe-control-plane.dashboard`.
- The existing Tailscale Serve and Funnel configurations remain `{}`; no public or
  private route was exposed before webhook and dashboard authentication existed.

## Fail-closed correction

The first reboot after plist installation auto-loaded both labels while the generated
environment still contained empty names. The backend reached its local banner and the
dashboard repeatedly exited with `env: node: No such file or directory`; no Tailscale
route existed, so neither process was remotely exposed. Both labels were unloaded.

The installer now disables both labels while any environment entry is empty, `start`
validates the environment before launchd activation, and the dashboard invokes the pinned
Vite CLI through `/opt/homebrew/bin/node`. The corrected host proof is:

```text
$ sudo .../install_studio2_control_plane.sh start
environment values are missing:
<names only>
exit=78
com.mobilyze.open-swe-control-plane.backend => disabled, not-loaded
com.mobilyze.open-swe-control-plane.dashboard => disabled, not-loaded
```

## Blocking proof

`gh api orgs/mobilyze-llc/installations --paginate` shows no
`mobilyze-open-swe-studio2` GitHub App installation. None of
`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`,
`GITHUB_APP_CLIENT_ID`, or `GITHUB_APP_CLIENT_SECRET` exists in the source environment
or the root-owned host environment file. GitHub provides no REST endpoint for creating a
GitHub App, and this executor had no authenticated browser available for the required
organization settings flow (`No browser is available`). Linear webhook creation is also a
settings operation not exposed by the supported Printing Press Linear CLI.

The deployment therefore stopped before writing partial credential state, exposing network
routes, starting services, or sending synthetic events. GitHub, Linear, and dashboard
triggers; completed-run visibility; authenticated health; service restart persistence; and
live rollback health are unproven. No review or merge may treat this checkpoint as complete.
