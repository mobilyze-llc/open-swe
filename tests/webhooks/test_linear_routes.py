"""Tests for Linear mention routing and visible failures."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.webhooks import linear as linear_service
from agent.webhooks.linear_routes import linear_webhook


def _payload(body: str = "@openswe please continue") -> dict:
    return {
        "type": "Comment",
        "action": "create",
        "data": {
            "id": "comment-123",
            "body": body,
            "issue": {"id": "issue-456", "title": "Test issue"},
            "user": {"id": "user-1", "name": "Test User", "email": "test@example.com"},
        },
    }


def _full_issue() -> dict:
    return {
        "id": "issue-456",
        "title": "Test issue",
        "identifier": "TEST-1",
        "url": "https://linear.app/test/issue/TEST-1",
        "team": {"id": "team-1", "name": "Test Team", "key": "TEST"},
        "project": None,
        "comments": {"nodes": []},
    }


async def _invoke(payload: dict) -> tuple[dict[str, str], MagicMock]:
    request = AsyncMock()
    request.body.return_value = json.dumps(payload).encode()
    request.headers = {"Linear-Signature": "valid"}
    background_tasks = MagicMock()
    result = await linear_webhook(request, background_tasks)
    return result, background_tasks


@pytest.mark.asyncio
async def test_explicit_comment_repo_skips_thread_inheritance() -> None:
    thread_repo = AsyncMock(return_value={"owner": "stored", "name": "repo"})
    with (
        patch("agent.webhooks.common.verify_linear_signature", return_value=True),
        patch(
            "agent.webhooks.common.fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=_full_issue(),
        ),
        patch("agent.webhooks.linear.get_linear_thread_repo_config", thread_repo),
        patch("agent.webhooks.common._is_repo_allowed", return_value=True),
    ):
        result, background_tasks = await _invoke(
            _payload("@openswe use repo explicit-owner/explicit-repo")
        )

    assert result["status"] == "accepted"
    thread_repo.assert_not_awaited()
    assert background_tasks.add_task.call_args.args[2] == {
        "owner": "explicit-owner",
        "name": "explicit-repo",
    }


@pytest.mark.asyncio
async def test_existing_thread_repo_precedes_profile_and_team_defaults() -> None:
    profile_repo = AsyncMock(return_value={"owner": "profile", "name": "repo"})
    with (
        patch("agent.webhooks.common.verify_linear_signature", return_value=True),
        patch(
            "agent.webhooks.common.fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=_full_issue(),
        ),
        patch(
            "agent.webhooks.linear.get_linear_thread_repo_config",
            new_callable=AsyncMock,
            return_value={"owner": "stored", "name": "repo"},
        ),
        patch("agent.webhooks.common.get_profile_default_repo", profile_repo),
        patch("agent.webhooks.common._is_repo_allowed", return_value=True),
    ):
        result, background_tasks = await _invoke(_payload())

    assert result["status"] == "accepted"
    profile_repo.assert_not_awaited()
    assert background_tasks.add_task.call_args.args[2] == {
        "owner": "stored",
        "name": "repo",
    }


@pytest.mark.asyncio
async def test_missing_thread_preserves_first_dispatch_profile_fallback() -> None:
    with (
        patch("agent.webhooks.common.verify_linear_signature", return_value=True),
        patch(
            "agent.webhooks.common.fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=_full_issue(),
        ),
        patch(
            "agent.webhooks.linear.get_linear_thread_repo_config",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.webhooks.common.resolve_login_from_email_async",
            new_callable=AsyncMock,
            return_value="test-user",
        ),
        patch(
            "agent.webhooks.common.get_profile_default_repo",
            new_callable=AsyncMock,
            return_value={"owner": "profile", "name": "repo"},
        ),
        patch("agent.webhooks.common._is_repo_allowed", return_value=True),
    ):
        result, background_tasks = await _invoke(_payload())

    assert result["status"] == "accepted"
    assert background_tasks.add_task.call_args.args[2] == {
        "owner": "profile",
        "name": "repo",
    }


@pytest.mark.asyncio
async def test_unroutable_mention_posts_visible_reason_and_returns_200() -> None:
    post_failure = AsyncMock()
    with (
        patch("agent.webhooks.common.verify_linear_signature", return_value=True),
        patch(
            "agent.webhooks.common.fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=_full_issue(),
        ),
        patch(
            "agent.webhooks.linear.get_linear_thread_repo_config",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.webhooks.common.resolve_login_from_email_async",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.webhooks.common.get_profile_default_repo",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("agent.webhooks.common.get_repo_config_from_team_mapping", return_value=None),
        patch(
            "agent.webhooks.common.get_team_default_repo",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("agent.webhooks.linear.post_linear_routing_failure", post_failure),
    ):
        result, background_tasks = await _invoke(_payload())

    assert result == {"status": "ignored", "reason": "No default repository configured"}
    background_tasks.add_task.assert_not_called()
    post_failure.assert_awaited_once_with(
        "issue-456",
        "comment-123",
        "Couldn't determine the target repository. Specify it as `repo owner/name`.",
    )


@pytest.mark.asyncio
async def test_allowlist_rejection_posts_visible_reason_and_returns_200() -> None:
    post_failure = AsyncMock()
    with (
        patch("agent.webhooks.common.verify_linear_signature", return_value=True),
        patch(
            "agent.webhooks.common.fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=_full_issue(),
        ),
        patch("agent.webhooks.common._is_repo_allowed", return_value=False),
        patch("agent.webhooks.linear.post_linear_routing_failure", post_failure),
    ):
        result, background_tasks = await _invoke(_payload("@openswe repo blocked/repo"))

    assert result == {"status": "ignored", "reason": "Repository not in allowlist"}
    background_tasks.add_task.assert_not_called()
    post_failure.assert_awaited_once_with(
        "issue-456",
        "comment-123",
        "The target repository `blocked/repo` is not enabled. "
        "Specify an allowed repository as `repo owner/name`.",
    )


@pytest.mark.asyncio
async def test_non_mention_comment_remains_silent() -> None:
    post_failure = AsyncMock()
    with (
        patch("agent.webhooks.common.verify_linear_signature", return_value=True),
        patch("agent.webhooks.linear.post_linear_routing_failure", post_failure),
    ):
        result, background_tasks = await _invoke(_payload("please continue"))

    assert result == {"status": "ignored", "reason": "Comment doesn't mention @openswe"}
    background_tasks.add_task.assert_not_called()
    post_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_repo_helper_extracts_persisted_metadata() -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value={"metadata": {"repo": {"owner": "stored", "name": "repo"}}})
        )
    )
    with (
        patch.object(
            linear_service.common, "generate_thread_id_from_issue", return_value="thread-1"
        ),
        patch.object(linear_service.common, "get_client", return_value=client),
    ):
        repo_config = await linear_service.get_linear_thread_repo_config("issue-456")

    assert repo_config == {"owner": "stored", "name": "repo"}
    client.threads.get.assert_awaited_once_with("thread-1")


@pytest.mark.asyncio
async def test_thread_repo_helper_falls_through_on_lookup_error() -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("unavailable")))
    )
    with (
        patch.object(
            linear_service.common, "generate_thread_id_from_issue", return_value="thread-1"
        ),
        patch.object(linear_service.common, "get_client", return_value=client),
    ):
        repo_config = await linear_service.get_linear_thread_repo_config("issue-456")

    assert repo_config is None


@pytest.mark.asyncio
async def test_routing_failure_reply_is_guarded_and_mention_free() -> None:
    comment = AsyncMock(return_value=True)
    with patch.object(linear_service, "comment_on_linear_issue", comment):
        await linear_service.post_linear_routing_failure(
            "issue-456",
            "comment-123",
            "Couldn't determine the target repository. Specify it as `repo owner/name`.",
        )

    comment.assert_awaited_once()
    issue_id, body = comment.call_args.args
    assert issue_id == "issue-456"
    assert body.startswith("❌ **Agent Error**")
    assert "@openswe" not in body.lower()
    assert comment.call_args.kwargs == {"parent_id": "comment-123"}
