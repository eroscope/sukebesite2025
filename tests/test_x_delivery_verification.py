from datetime import datetime, timedelta

from article_studio import JST
from indanya_desktop.social_x import (
    _complete_elapsed_x_schedules,
    _eligible_x_rows,
    _x_action_time,
    list_x_posts,
    reconcile_x_delivery_observations,
    save_x_posts,
    schedule_x_posts,
    x_reply_schedule_status,
)
from indanya_desktop.x_account_health import apply_x_health_limits, _classify_health


NOW = datetime(2026, 9, 15, 23, 0, tzinfo=JST)
DUE = NOW - timedelta(hours=1)
TEXT = "A unique article about a specific subject\nContinue here\nhttps://example.com/article.html"


def reservation(**changes):
    return {
        "post_id": "test", "delivery_mode": "post", "status": "scheduled",
        "account_handle": "example", "scheduled_for": DUE.isoformat(),
        "post_text": TEXT, **changes,
    }


def observation(**changes):
    return {
        "url": "https://x.com/example/status/1234",
        "published_at": DUE.isoformat(),
        "text": TEXT, **changes,
    }


def test_old_time_only_success_is_migrated_without_resending(tmp_path):
    save_x_posts(tmp_path, [reservation(
        status="posted", delivery_verification="x_reservation_elapsed",
        posted_at=DUE.isoformat(),
    )])
    result = reconcile_x_delivery_observations(tmp_path, "example", [], now=NOW)
    row = list_x_posts(tmp_path)[0]
    assert result == {"matched": 0, "unverified": 1}
    assert row["status"] == "delivery_unverified"
    assert row["posted_at"] == ""
    assert _x_action_time(row) == DUE
    assert _eligible_x_rows(tmp_path, [row], NOW) == []
    assert schedule_x_posts(tmp_path, ["test"]) == {"posted": [], "scheduled": [], "failed": []}


def test_unique_owner_text_and_time_match_confirms_delivery(tmp_path):
    save_x_posts(tmp_path, [reservation()])
    result = reconcile_x_delivery_observations(tmp_path, "example", [observation()] * 2, now=NOW)
    row = list_x_posts(tmp_path)[0]
    assert result == {"matched": 1, "unverified": 0}
    assert row["status"] == "posted"
    assert row["x_post_url"] == observation()["url"]
    assert row["delivery_verification"] == "profile_text_and_time"
    assert reconcile_x_delivery_observations(tmp_path, "example", [observation()], now=NOW)["matched"] == 0


def test_ambiguous_or_incorrect_observations_do_not_confirm(tmp_path):
    bad_observations = [
        [observation(url="https://x.com/other/status/1234")],
        [observation(text="A different article")],
        [observation(published_at=(DUE - timedelta(days=1)).isoformat())],
        [observation(), observation(url="https://x.com/example/status/5678")],
    ]
    for candidates in bad_observations:
        save_x_posts(tmp_path, [reservation()])
        assert reconcile_x_delivery_observations(tmp_path, "example", candidates, now=NOW)["matched"] == 0


def test_new_reservation_and_verified_success_are_not_downgraded():
    rows = [
        reservation(scheduled_for=NOW.isoformat()),
        reservation(status="posted", x_post_url=observation()["url"], delivery_verification="profile_text_and_time"),
    ]
    assert not _complete_elapsed_x_schedules(rows, NOW)


def test_actual_platform_warning_blocks_even_with_adaptive_pacing_disabled():
    limits = apply_x_health_limits({"adaptive_pacing_enabled": False}, {
        "first_party": {"account_warning": "temporarily locked"},
    })
    assert limits["daily_post_limit"] == 0
    assert limits["global_daily_action_limit"] == 0
    assert limits["normal_pacing_basis"] == "platform_warning"


def test_visibility_only_override_requires_matching_accessible_account():
    config = {"account_handle": "example", "daily_post_limit": 5, "global_daily_action_limit": 8}
    state = {
        "risk_level": 3, "status": "checked", "classification": "restricted",
        "external": {"banned_signals": ["search"]},
        "first_party": {"active_handle": "example", "profile_accessible": True, "profile_post_count": 3},
    }
    assert apply_x_health_limits(config, state)["daily_post_limit"] == 5
    assert apply_x_health_limits(config, {**state, "status": "partial"})["daily_post_limit"] == 1
    assert apply_x_health_limits(config, {**state, "first_party": {}})["daily_post_limit"] == 1
    assert apply_x_health_limits(config, {**state, "external": {"banned_signals": ["ghost"]}})["daily_post_limit"] == 1
    assert apply_x_health_limits({**config, "daily_post_limit": 2}, state)["daily_post_limit"] == 2


def test_inaccessible_profile_without_warning_is_unknown_not_a_ban():
    assert _classify_health({}, {"profile_accessible": False}) == "unknown"


def test_unverified_reply_counts_as_pending_and_reserves_its_action(tmp_path):
    from unittest.mock import patch
    row = reservation(delivery_mode="reply", status="delivery_unverified", scheduled_at=DUE.isoformat())
    assert _x_action_time(row) == DUE
    with patch("indanya_desktop.social_x.list_x_posts", return_value=[row]):
        status = x_reply_schedule_status(tmp_path, NOW)
    assert status["pending_count"] == 1
    assert not status["due"]
