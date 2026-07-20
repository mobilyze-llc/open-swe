from __future__ import annotations

from agent.mobilyze.behavior.observations import (
    CliObservation,
    FilesystemObservation,
    FilesystemState,
    GeneratedArtifactObservation,
    HttpObservation,
    ObservationType,
    Presence,
    ProcessObservation,
    ProcessState,
)
from agent.mobilyze.behavior.probes import (
    CliProbe,
    FilesystemAssertion,
    FilesystemProbe,
    Fixture,
    GeneratedArtifactAssertion,
    GeneratedArtifactProbe,
    HttpProbe,
    ProcessAssertion,
    ProcessProbe,
)


def assert_probe(fixture: Fixture, observation: ObservationType) -> bool:
    probe = fixture.probe
    if isinstance(probe, CliProbe) and isinstance(observation, CliObservation):
        return (
            observation.exit_code == probe.expected_exit_code
            and (
                probe.stdout_contains is None or probe.stdout_contains in observation.public_stdout
            )
            and (
                probe.stderr_contains is None or probe.stderr_contains in observation.public_stderr
            )
        )
    if isinstance(probe, HttpProbe) and isinstance(observation, HttpObservation):
        return observation.status_code == probe.expected_status and (
            probe.body_contains is None or probe.body_contains in observation.public_body
        )
    if isinstance(probe, GeneratedArtifactProbe) and isinstance(
        observation, GeneratedArtifactObservation
    ):
        if probe.assertion is GeneratedArtifactAssertion.EXISTS:
            return observation.presence is Presence.PRESENT
        if probe.assertion is GeneratedArtifactAssertion.ABSENT:
            return observation.presence is Presence.ABSENT
        return (
            observation.presence is Presence.PRESENT and observation.sha256 == probe.expected_sha256
        )
    if isinstance(probe, FilesystemProbe) and isinstance(observation, FilesystemObservation):
        if probe.assertion is FilesystemAssertion.EXISTS:
            return observation.state is not FilesystemState.ABSENT
        if probe.assertion is FilesystemAssertion.ABSENT:
            return observation.state is FilesystemState.ABSENT
        if probe.assertion is FilesystemAssertion.FILE:
            return observation.state is FilesystemState.FILE
        if probe.assertion is FilesystemAssertion.DIRECTORY:
            return observation.state is FilesystemState.DIRECTORY
        return (
            observation.state is FilesystemState.FILE
            and observation.sha256 == probe.expected_sha256
        )
    if isinstance(probe, ProcessProbe) and isinstance(observation, ProcessObservation):
        if probe.assertion is ProcessAssertion.RUNNING:
            return observation.state is ProcessState.RUNNING
        if probe.assertion is ProcessAssertion.STOPPED:
            return observation.state is ProcessState.STOPPED
        if probe.assertion is ProcessAssertion.EXITED_ZERO:
            return observation.state is ProcessState.EXITED and observation.exit_code == 0
        if probe.assertion is ProcessAssertion.EXITED_CODE:
            return (
                observation.state is ProcessState.EXITED
                and observation.exit_code == probe.expected_exit_code
            )
        expected = probe.public_log_contains
        return expected is not None and expected in observation.public_log
    return False
