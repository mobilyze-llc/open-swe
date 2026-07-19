# OSWE-29 studio2 evidence

Captured on `2026-07-19`, with the final runtime snapshot at `2026-07-19T21:24:24Z`.
Secret values, authorization headers, cookies, webhook signatures, and private-key material are
omitted.

## Installed boundary

- Baseline and merged PR #1: `4c9201093851f54aba60cf6ec0e814aebb828076`.
- Live-tested tooling commit: `691fd8624b065698a58fc78b928245d0707eeeb7`; the deployed
  installer SHA-256 was
  `11be4ef4c7628385b06ce8c22c69a45aa6e2f9474de7749f3f77e1b3fbf75d75`, matching
  the checked-in script at that commit.
- The deployed manifest helper SHA-256 was
  `c8fd5a13a7c72ddbb0643b1f2e8bc71ce722b6604844fa86d17a8c96a00f3a9a`, also
  matching the checked-in script at that commit.
- Pinned application release: `f4e2a6833e403184ee710b102ee9d31bd12a0387`.
- Retained rollback release: `4c9201093851f54aba60cf6ec0e814aebb828076`.
- Dedicated identity: `_openswectl`, UID/GID 451, `/var/empty` home, `/usr/bin/false`
  shell, and hidden from interactive login surfaces.
- Deployment root: `/opt/mobilyze/open-swe-control-plane` (`root:wheel`, `0755`).
- State root: `/var/db/mobilyze-open-swe-control-plane` (`_openswectl:_openswectl`,
  `0700`).
- Log root: `/var/log/mobilyze-open-swe-control-plane`
  (`_openswectl:_openswectl`, `0750`).
- Configuration root: `/Library/Application Support/MobilyzeOpenSWEControlPlane`
  (`root:_openswectl`, `0750`). The environment file is `root:_openswectl`, `0640`.
- Installed runner SHA-256:
  `8773e59e871aa9a9eb75247997c7b378b68abe93dfd5a0dd6ad6e894c4ee44e5`, matching
  the checked-in runner.
- `uv.lock` SHA-256:
  `43aca3bf3c7ac2e975682b01039e10c5998d7a03d70790225c60b673abda770d`.
- `ui/pnpm-lock.yaml` SHA-256:
  `dc5638471a8fbbf0a0d0de4da0c0b4a7a664ac9a27edcf87234bdb3df3df0b6d`.

The final `current` symlink resolves to the pinned application release. Both launchd services are
enabled and running. The backend is healthy on `127.0.0.1:2029`; the dashboard returns HTTP 200
on `127.0.0.1:3029`.

## Authentication and routes

- GitHub App `mobilyze-open-swe-studio2` is installed on the `mobilyze-llc`
  organization with app ID `4340220`, installation ID `147646970`, and selected-repository
  access. The checked-in manifest restricts that selection to `mobilyze-llc/open-swe`.
- The GitHub App has no administration permission and only the declared repository permissions.
- The Linear webhook is scoped to `Comment.create` for team `Open SWE` (`OSWE`), whose checked-in
  mapping resolves to `mobilyze-llc/open-swe`.
- The existing authenticated `langsmith` CLI on the operator host and `studio2` both reported
  environment-backed authentication against `https://api.smith.langchain.com`. An authenticated
  organization-members query resolved `eric@litman.org` as an organization administrator with
  `ls_user_id` `83827766-2e52-4cdf-a9d5-85e1f39d13a3`.
- Tailscale Serve exposes the dashboard on private HTTPS port 443, with
  `/dashboard/api` proxied to the backend. Funnel exposes only `/webhooks` on HTTPS port 8443.

The root-owned service environment was inspected by name only. It contains the declared GitHub,
Linear, dashboard, local-sandbox, and encryption settings. It contains no direct model-provider
API key. The existing LangSmith credential was installed under the runtime-specific
`LANGSMITH_API_KEY_PROD` name; its value was never printed, copied into the manifest or repository,
or captured in this evidence.

## Native intake and terminal visibility

No successful model turn was required for this deployment proof. Each native surface reached a
deterministic thread identity and a visible terminal state without changing repository files.

| Surface | Native trigger | Deterministic thread | Terminal evidence |
| --- | --- | --- | --- |
| GitHub | PR #5 comment `5017316276` | `c3bb6ee0-e5f6-5bcc-8343-5770d2c0ab2b` | `idle`; authenticated intake created the thread, then the upstream GitHub-user email lookup skipped dispatch |
| Linear | OSWE-29 comment `9d4da0a2-a279-43a5-99b1-b92906adb3dc` | `263e4653-2d85-5f58-81db-2fb3a2b6df9b` | run `019f7c3f-2be5-77f1-bbf7-841b4f0d7305` reached terminal `error` at the expected direct-provider boundary; metadata records `mobilyze-llc/open-swe` |
| Dashboard | authenticated dashboard chat | `019f7bde-58d5-7049-9e80-00b7a5d90b92` | runs `019f7be4-136e-72d2-9c3c-0788655356d4` and `019f7bde-59ef-7641-bb97-d0c185fc2a79` are terminal `error`; OAuth and repository scope succeeded and a local sandbox was created |

GitHub Funnel delivery `3832165452409208832` was a successful redelivery of GUID
`b123b010-83a3-11f1-88c8-7b2ce94a40d9`: GitHub recorded `status=OK`, HTTP 200, and a 0.27 second
duration. Seven later fresh GitHub deliveries also returned HTTP 200 and were present in the
`studio2` backend log. GitHub creates a new delivery record for a redelivery; the immutable
original failed delivery `3832159166579867648` remains HTTP 502 and was not used as the success
record.

The final Linear canary authenticated as the canonical `eric@litman.org` LangSmith identity and
used the GitHub App installation token in bot-token-only mode. The earlier
`eric@mobilyze.com` lookup failure did not recur. The final run stopped only when the upstream
model construction required `OPENAI_API_KEY`; successful model execution is outside this
deployment acceptance, so no direct provider credential or alternate executor was introduced.

## Restart and persistence

Before restart, the backend/dashboard PIDs were `27079` and `27084`. The managed `restart`
operation replaced them with PIDs `49499` and `49507`; backend health and dashboard HTTP 200
recovered. Queries for all three exact thread IDs returned the same creation timestamps,
terminal statuses, and run IDs after restart.

After the review hardening patch, the deployed environment validator accepted every required
name exactly once without exposing values, and the managed restart replaced PIDs `58918` and
`58923` with `60244` and `60252`. Backend health and dashboard HTTP 200 recovered, the current
release remained `f4e2a6833e403184ee710b102ee9d31bd12a0387`, and all three exact threads remained
visible. The Linear thread retained final run `019f7c3f-2be5-77f1-bbf7-841b4f0d7305` as terminal
`error`.

## Rollback and restore

The first live rollback exposed a launchd unregister race: `bootout` could return while a service
was still present, allowing the immediate `print || bootstrap` check to skip bootstrap. Tooling
commit `f9cd2721f916b593835c08b7d50a972b15e7bf51` adds a bounded wait for each
service to disappear and fails before changing the release if unload does not finish.

With that tooling installed, rollback to `4c9201093851f54aba60cf6ec0e814aebb828076`
completed with backend PID `52265`, dashboard PID `52366`, healthy backend, and dashboard HTTP
200. The three exact thread IDs and their recorded runs remained visible. Restoring
`f4e2a6833e403184ee710b102ee9d31bd12a0387` completed with backend PID `52692`, dashboard PID
`52779`, healthy backend, dashboard HTTP 200, and the same persisted thread/run records. The final
`current` symlink points to the pinned application release.
