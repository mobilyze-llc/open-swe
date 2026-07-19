# studio2 Tart and Orchard operator runbook

OSWE-14 installs one authenticated Orchard controller and one local Tart worker on `studio2`. The controller listens only on the host's Tailscale address, `https://100.107.128.12:6120`; it never listens on a wildcard interface. Orchard service-account authentication protects reads, VM mutation, and command execution.

## Supported baseline

The live pins are Tart 2.32.1 and Orchard 0.55.0. Both releases use Fair Source License 0.9. Tart 2.32.1 permits 100 CPU-core users and Orchard 0.55.0 permits four macOS worker devices; this installation uses 24 logical CPU cores and one worker. Softnet 0.20.1 remains installed from `openai/tools/softnet` for Tart network isolation.

Tart 2.33.0 and Orchard 0.56.0 were evaluated first. macOS 26.5.1 rejected Tart because its release signature and provisioning profile did not satisfy `com.apple.vm.networking`, and rejected Orchard with AMFI `Broken signature with Team ID fatal`. The pinned pair is the newest pair that passed upstream checksums, Gatekeeper/AMFI execution, and the live lifecycle on this host.

The redacted machine-readable baseline is [`config/mobilyze/studio2-orchard-baseline.json`](../../config/mobilyze/studio2-orchard-baseline.json). It records release URLs and SHA-256 checksums without credentials.

## Identity, paths, and limits

Launchd runs the controller as hidden, disabled-login `_opensweorchard` UID/GID 450. The worker starts as root only so Orchard can start its narrow macOS local-network helper, then Orchard drops worker privileges to `_opensweorchard` before Tart work. Eric's home and active repositories are never runtime roots.

- Software: `/opt/mobilyze/open-swe-orchard`
- Controller, worker, client context, Tart images, and VMs: `/var/db/mobilyze-open-swe-orchard`
- Root/service-readable secrets: `/Library/Application Support/Mobilyze/OpenSWEOrchard/secrets`
- Logs: `/var/log/mobilyze-open-swe-orchard`
- Controller backups: `/var/backups/mobilyze-open-swe-orchard`

Every admitted VM receives `oswe.vm-slots=1`; the worker advertises two slots, so a third VM remains unassigned and pending. The fixed create command applies 4 CPUs, 8192 MiB memory, and a 40 GiB disk. This initial baseline reports free capacity on the state volume through `status` and `manifest`, but it does not enforce an unproven disk threshold.

## Install and inspect

Run from a checkout at the reviewed OSWE-14 commit:

```bash
scp scripts/mobilyze/studio2-orchard studio2:/tmp/studio2-orchard
ssh studio2 'chmod 0755 /tmp/studio2-orchard && sudo /tmp/studio2-orchard install'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard status'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard manifest'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard unauthenticated-probe'
```

`install` refuses any host except `studio2`, verifies Tailscale address `100.107.128.12`, downloads exact upstream releases, verifies their SHA-256 values and executable signatures, creates the identity and roots, and sets Tart's recommended 600-second DHCP lease without replacing other `bootpd` settings. It records the prior lease value before the first change so confirmed uninstall can restore that key. Worker bootstrap credentials replace the active file only after retrieval, non-empty validation, ownership, and mode checks succeed. The installer then bootstraps least-privilege operator and worker service accounts and loads both launch daemons. It exits before account creation if UID or GID 450 belongs to another identity. The controller launch wrapper re-enables the host's existing enrolled Tailscale node and waits for its exact address before Orchard binds; it neither creates a Tailscale identity nor stores a Tailscale credential. The bootstrap administrator secret is retained only for Orchard credential recovery. Operator credentials have `compute:read`, `compute:write`, `compute:connect`, and `admin:read`; worker credentials have only `compute:read` and `compute:write`.

## VM lifecycle

The operator exposes fixed commands rather than an arbitrary command runner:

```bash
ssh studio2 'sudo /usr/local/sbin/studio2-orchard vm-create disposable-name'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard vm-list'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard vm-exec disposable-name'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard vm-stop disposable-name'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard vm-delete disposable-name'
```

`vm-exec` always runs the proof command (`oswe14-exec-ok` plus `uname -a`) through Orchard's authenticated port-forward. `vm-stop` resolves Orchard's generated local Tart name and stops it as the service identity. Orchard deletion removes the resource and its backing Tart VM on the next worker reconciliation. The combined verification is:

```bash
ssh studio2 'sudo /usr/local/sbin/studio2-orchard verify disposable-name'
```

`verify` installs an exit cleanup before creation, so an interrupted readiness, execution, or stop step still deletes the Orchard resource and releases its slot.

## Service lifecycle and diagnostics

```bash
ssh studio2 'sudo /usr/local/sbin/studio2-orchard stop'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard start'
ssh studio2 'sudo /usr/local/sbin/studio2-orchard restart'
ssh studio2 'sudo launchctl print system/com.mobilyze.open-swe.orchard.controller'
ssh studio2 'sudo launchctl print system/com.mobilyze.open-swe.orchard.worker'
ssh studio2 'sudo lsof -nP -iTCP:6120 -sTCP:LISTEN'
ssh studio2 'sudo tail -200 /var/log/mobilyze-open-swe-orchard/controller.log'
ssh studio2 'sudo tail -200 /var/log/mobilyze-open-swe-orchard/worker-launchd.log'
ssh studio2 'sudo -u _opensweorchard env HOME=/var/db/mobilyze-open-swe-orchard /opt/mobilyze/open-swe-orchard/current/tart list'
```

The expected listener is exactly `100.107.128.12:6120`. A wildcard listener, a missing worker heartbeat, or any credential text in logs blocks operation. `status` and `manifest` still return diagnostic output when either launchd job is absent, and `status` prints current free capacity for operator decisions without turning an unvalidated value into an admission rule.

## Authentication rotation

Perform rotation from a root shell with history disabled. Create the replacement Orchard service account first, build and verify a new `--no-pki` context pinned to the displayed controller certificate, atomically replace the corresponding root-owned secret, restart only the affected launch daemon, verify authenticated `list`/`ssh`, then delete the superseded account. Never print tokens or paste them into a transcript.

Controller recovery starts by replacing `secrets/bootstrap-admin` with `openssl rand -hex 32`, restarting the controller, and recreating the `bootstrap` context with that token. Operator rotation uses a replacement account with `compute:read`, `compute:write`, `compute:connect`, and `admin:read`. Worker rotation uses `compute:read` and `compute:write`, writes `orchard get bootstrap-token <replacement-worker-account>` to `secrets/worker-bootstrap` with mode `0400`, restarts the worker, verifies its heartbeat, and then deletes the old worker account.

## Backup, upgrade, and rollback

Create a consistent controller backup before changing binaries or credentials:

```bash
ssh studio2 'sudo /usr/local/sbin/studio2-orchard backup'
```

The backup command restores both launchd services on archive failure as well as success; a failed archive returns nonzero after service restoration.

Upgrade only through a reviewed change to the versions, release checksums, and redacted manifest in `scripts/mobilyze/studio2-orchard` and `config/mobilyze/studio2-orchard-baseline.json`. Before the upgrade, record the active `current-tart` and `current-orchard` release names; `install` retains versioned releases. Re-run `install`, both lifecycle proofs, the unauthenticated probe, log redaction check, and the repository validation gate. This keeps version selection and executable provenance in the PR instead of an untracked host command.

Rollback accepts the recorded pre-upgrade pair, verifies both binaries already exist under the dedicated release roots, re-points `current-tart` and `current-orchard`, and restarts both services:

```bash
ssh studio2 'sudo /usr/local/sbin/studio2-orchard rollback 2.32.1-r1 0.55.0'
```

If controller data also needs restoration, stop both services, move the failed controller directory aside, extract a named backup under `/var/db/mobilyze-open-swe-orchard/controller`, restore `_opensweorchard:_opensweorchard` ownership, and start the services. Never extract over a live BadgerDB directory.

## Image cleanup, stuck VMs, and disk recovery

Pause scheduling before manual recovery:

```bash
sudo -u _opensweorchard env HOME=/var/db/mobilyze-open-swe-orchard ORCHARD_HOME=/var/db/mobilyze-open-swe-orchard/operator /opt/mobilyze/open-swe-orchard/current/orchard pause worker studio2-open-swe --wait 600
sudo -u _opensweorchard env HOME=/var/db/mobilyze-open-swe-orchard /opt/mobilyze/open-swe-orchard/current/tart prune --entries caches --older-than 7
sudo -u _opensweorchard env HOME=/var/db/mobilyze-open-swe-orchard ORCHARD_HOME=/var/db/mobilyze-open-swe-orchard/operator /opt/mobilyze/open-swe-orchard/current/orchard resume worker studio2-open-swe
```

For a stuck VM, try `studio2-orchard vm-stop NAME`, then `vm-delete NAME`. Confirm the Orchard resource is absent and wait for worker reconciliation to remove the generated `orchard-NAME-...` Tart VM. If the reported free capacity is insufficient for the intended VM operation, leave scheduling paused, delete failed resources through Orchard, prune only caches or explicitly identified disposable local VMs, and inspect `df -h /var/db/mobilyze-open-swe-orchard` before resuming.

## Uninstall

Preview exact deletion targets first:

```bash
ssh studio2 'sudo /usr/local/sbin/studio2-orchard uninstall'
```

After copying any retained controller backup off the host, execute the complete removal with `uninstall --confirm`. It stops both daemons, restores the pre-install DHCP lease value (or removes only that key when it was previously absent), removes the two plists, installed wrappers, dedicated software/state/config/log/backup roots, and deletes the macOS role account through `sysadminctl` before removing its dedicated group.
