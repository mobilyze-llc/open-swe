from __future__ import annotations

import posixpath

from agent.mobilyze.review.contracts import FinderAdapterInput, ReviewSubject


def build_finder_adapter_input(
    subject: ReviewSubject,
    *,
    artifact_root: str,
    checkout_path: str,
    output_path: str,
    include_untrusted_run_trace: bool = False,
) -> FinderAdapterInput:
    """Build the bounded external-helper input for an initial finder lane."""
    trace = subject.run_trace_digest if include_untrusted_run_trace else None
    return FinderAdapterInput(
        checkout_path=checkout_path,
        checkout_head_sha=subject.review_range.head_sha,
        base_sha=subject.review_range.base_sha,
        diff_base_sha=subject.review_range.diff_base_sha,
        diff_head_sha=subject.review_range.diff_head_sha,
        diff_uses_merge_base=subject.review_range.diff_uses_merge_base,
        diff_path=posixpath.join(artifact_root.rstrip("/"), subject.diff.uri),
        diff_sha256=subject.diff.sha256,
        trusted_context_path=posixpath.join(artifact_root.rstrip("/"), subject.trusted_context.uri),
        trusted_context_sha256=subject.trusted_context.sha256,
        output_path=output_path,
        subject_hash=subject.subject_hash,
        run_trace_digest=trace,
    )
