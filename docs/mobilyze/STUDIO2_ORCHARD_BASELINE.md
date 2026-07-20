# studio2 Tart/Orchard baseline

This surface maintains only the dedicated Orchard substrate on `studio2`. It does not manage Open SWE control-plane services, scheduling policy, retries, VM lifecycle, or OSWE-29 deployment state.

## Pinned baseline

- Orchard `0.55.0`, installed executable SHA-256 `35a6ca9f1770a6da9ccc508cee46df21f962d4c5fb437161e4fe8daa34bb14fe`.
- Tart `2.32.1`, installed executable SHA-256 `05b65d5c14e8b41e8e44b6d9fd1278de4bedbc8b735d9b99f3c748f76f75862d`.
- Dedicated `_opensweorchard` UID/GID `450`, controller and worker launchd labels from `config/mobilyze/studio2-orchard-baseline.json`.
- TLS listener `100.107.128.12:6120`; unauthenticated `GET /v1/controller/info` must return `401`.
- Worker capacity is two slots. Orchard 0.55.0 enforces the 4 CPU and 8192 MiB worker defaults; VM callers use 40 GiB as the disk request default because this Orchard release has no worker disk-default flag. Host disk capacity is diagnostic-only and has no enforced floor.

## Operations

Run from the repository root with root privileges on the target macOS host. Stage an Orchard executable and a `tart.app` whose executable hashes match the pins. On first install, also stage the existing bootstrap-admin and worker bootstrap tokens in protected files. Reinstallation may use the installed release paths; the installer verifies and reuses identical destinations instead of copying a file onto itself.

```bash
python -m agent.mobilyze.orchard_baseline.install   --orchard-source /trusted/orchard   --tart-app-source /trusted/tart.app   --admin-token-source /protected/bootstrap-admin.token   --worker-token-source /protected/worker-bootstrap.token
python -m agent.mobilyze.orchard_baseline.status
python -m agent.mobilyze.orchard_baseline.backup /protected/orchard-state.tar
python -m agent.mobilyze.orchard_baseline.restore /protected/orchard-state.tar
python -m agent.mobilyze.orchard_baseline.rollback --confirm
python -m agent.mobilyze.orchard_baseline.uninstall
python -m agent.mobilyze.orchard_baseline.uninstall --confirm
```

`status` emits all diagnostics and exits non-zero when any required check is degraded. Backup archives are private from creation; backup and restore stop only the two dedicated launchd jobs, attempt service restoration once, and preserve the primary failure if restoration also fails. Failed install/update and rollback activation attempts restore the prior release links and loaded-job set while preserving the original error. Rollback accepts only the recorded checksum-verified previous release pair. Uninstall previews or removes only the dedicated account, labels, wrappers, logs, state, and `/opt/mobilyze/open-swe-orchard` root.

## Validation and live proof

Render the generated wrappers and run `/bin/sh -n` against each, then run the focused tests and repository-required validation. During repository-only replacement work, limit live proof to status, hashes, labels, process arguments, listener/authentication checks, ownership/permissions, and observed disk reporting. Do not reinstall, restart, roll back, restore, or uninstall the preserved services.
