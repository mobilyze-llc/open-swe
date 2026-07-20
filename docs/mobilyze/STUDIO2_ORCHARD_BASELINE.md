# studio2 Tart/Orchard baseline

This surface maintains only the dedicated Orchard substrate on `studio2`. It does not manage Open SWE control-plane services, scheduling policy, retries, VM lifecycle, or OSWE-29 deployment state.

## Pinned baseline

- Orchard `0.55.0`, installed executable SHA-256 `35a6ca9f1770a6da9ccc508cee46df21f962d4c5fb437161e4fe8daa34bb14fe`.
- Tart `2.32.1`, installed executable SHA-256 `05b65d5c14e8b41e8e44b6d9fd1278de4bedbc8b735d9b99f3c748f76f75862d`.
- Dedicated `_opensweorchard` UID/GID `450`, home `/var/db/mobilyze-open-swe-orchard`, shell `/usr/bin/false`, and controller and worker launchd labels from `config/mobilyze/studio2-orchard-baseline.json`.
- Generated wrappers export `HOME=/var/db/mobilyze-open-swe-orchard` and `ORCHARD_HOME='/Library/Application Support/Mobilyze/OpenSWEOrchard'`.
- The support root `/Library/Application Support/Mobilyze/OpenSWEOrchard` is `root:_opensweorchard` mode `0750`. Its `secrets` directory is `_opensweorchard:_opensweorchard` mode `0700`; `bootstrap-admin` and `worker-bootstrap` are regular files with the same owner/group and mode `0400`. Root-only status checks this metadata without reading secret contents.
- TLS listener `100.107.128.12:6120`; unauthenticated `GET /v1/controller/info` must return `401`. The controller wrapper directly activates Tailscale and checks the expected IPv4 address once before starting Orchard. A failed activation or readiness check exits for the existing launchd `KeepAlive` contract; there is no internal retry manager.
- Worker capacity is two slots. Orchard 0.55.0 enforces the 4 CPU and 8192 MiB worker defaults; VM callers use 40 GiB as the disk request default because this Orchard release has no worker disk-default flag. Host disk capacity is diagnostic-only and has no enforced floor.

## Operations

Run from the repository root with root privileges on the target macOS host; status is intentionally root-only. Stage an Orchard executable and a `tart.app` whose executable hashes match the pins. On first install, also stage the existing bootstrap-admin and worker bootstrap tokens in protected files. Reinstallation may use the installed release and protected secret paths; the installer reuses identical destinations, preserves the root-owned traversable support boundary, normalizes secret modes and ownership, and does not copy a path onto itself.

```bash
python -m agent.mobilyze.orchard_baseline.install   --orchard-source /trusted/orchard   --tart-app-source /trusted/tart.app   --admin-token-source '/Library/Application Support/Mobilyze/OpenSWEOrchard/secrets/bootstrap-admin'   --worker-token-source '/Library/Application Support/Mobilyze/OpenSWEOrchard/secrets/worker-bootstrap'
python -m agent.mobilyze.orchard_baseline.status
python -m agent.mobilyze.orchard_baseline.backup /protected/orchard-state.tar
python -m agent.mobilyze.orchard_baseline.restore /protected/orchard-state.tar
python -m agent.mobilyze.orchard_baseline.rollback --confirm
python -m agent.mobilyze.orchard_baseline.uninstall
python -m agent.mobilyze.orchard_baseline.uninstall --confirm
```

Fresh install and update preserve the executable links under `/opt/mobilyze/open-swe-orchard/current`, including the compatibility link `current/tart.app`. `status` emits all diagnostics and exits non-zero when any required check is degraded. Backup archives are private from creation and contain only `/var/db/mobilyze-open-swe-orchard`; they deliberately exclude the protected support root and bootstrap secrets. Restore replaces only that data root and leaves the protected secrets unchanged. Backup and restore stop only the two dedicated launchd jobs, attempt service restoration once, and preserve the primary failure if restoration also fails.

Failed install/update and rollback activation attempts restore the prior release links and loaded-job set while preserving the original error. Rollback accepts only the recorded checksum-verified previous release pair. Uninstall previews or removes only the dedicated account, labels, wrappers, logs, state, `/opt/mobilyze/open-swe-orchard`, and the exact protected support root `/Library/Application Support/Mobilyze/OpenSWEOrchard`; it does not remove either parent directory.

## Validation and live proof

Render the generated wrappers and run `/bin/sh -n` against each, then parse both generated plists and run the focused tests and repository-required validation. Repository changes must not reinstall, restart, roll back, restore, uninstall, or otherwise mutate the preserved live services.
