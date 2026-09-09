from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from article_studio import JST
from indanya_desktop.x_account_health import (
    apply_x_health_limits,
    load_x_health_state,
    run_due_x_health_check,
    save_x_health_state,
    x_health_schedule_status,
)
from indanya_desktop.social_x import run_due_x_follow_cycle


def settings() -> dict:
    return {
        "account_handle": "hentai596",
        "health_check_enabled": True,
        "health_check_interval_hours": 12,
        "adaptive_pacing_enabled": True,
        "daily_post_limit": 5,
        "reply_daily_limit": 2,
        "follow_daily_limit": 2,
        "global_daily_action_limit": 8,
        "global_min_interval_minutes": 90,
        "reply_min_interval_minutes": 360,
        "follow_min_interval_hours": 6,
    }


def healthy_first_party() -> dict:
    return {
        "active_handle": "hentai596",
        "profile_accessible": True,
        "account_warning": "",
        "profile_post_count": 5,
        "search_post_count": 5,
        "profile_posts_found_in_search": 5,
        "search_ratio": 1.0,
        "search_available": True,
        "median_visible_views": 500,
    }


def test_health_check_runs_every_twelve_hours_and_keeps_normal_limits() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        now = datetime(2026, 9, 9, 8, 0, tzinfo=JST)
        with patch(
            "indanya_desktop.x_account_health._external_shadowban_check",
            return_value={"status": "clean", "checks": {"search": "ok"}},
        ), patch(
            "indanya_desktop.x_account_health._first_party_visibility",
            return_value=healthy_first_party(),
        ):
            result = run_due_x_health_check(root, settings(), now=now)
        assert result["classification"] == "healthy"
        assert result["effective_limits"]["daily_posts"] == 5
        assert result["effective_limits"]["daily_follows"] == 2
        assert not x_health_schedule_status(
            root, settings(), now + timedelta(hours=11, minutes=59)
        )["due"]
        assert x_health_schedule_status(
            root, settings(), now + timedelta(hours=12)
        )["due"]


def test_restriction_signal_reduces_replies_and_follows_before_posts() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        now = datetime(2026, 9, 9, 8, 0, tzinfo=JST)
        first_party = {**healthy_first_party(), "search_ratio": 0.0}
        external = {
            "status": "banned",
            "checks": {"search": "banned", "ghost": "unknown"},
            "banned_signals": ["search"],
        }
        with patch(
            "indanya_desktop.x_account_health._external_shadowban_check",
            return_value=external,
        ), patch(
            "indanya_desktop.x_account_health._first_party_visibility",
            return_value=first_party,
        ):
            result = run_due_x_health_check(root, settings(), now=now)
        assert result["classification"] == "restricted"
        assert result["risk_level"] == 2
        assert result["effective_limits"]["daily_posts"] == 1
        assert result["effective_limits"]["daily_replies"] == 0
        assert result["effective_limits"]["daily_follows"] == 0


def test_three_clean_checks_restore_only_one_level() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        initial = load_x_health_state(root, "hentai596")
        initial.update({"risk_level": 2, "classification": "restricted"})
        save_x_health_state(root, initial)
        with patch(
            "indanya_desktop.x_account_health._external_shadowban_check",
            return_value={"status": "clean", "checks": {"search": "ok"}},
        ), patch(
            "indanya_desktop.x_account_health._first_party_visibility",
            return_value=healthy_first_party(),
        ):
            for index in range(3):
                result = run_due_x_health_check(
                    root,
                    settings(),
                    now=datetime(2026, 9, 9, 8, 0, tzinfo=JST)
                    + timedelta(hours=12 * index),
                    force=True,
                )
        assert result["risk_level"] == 1
        assert result["effective_limits"]["daily_posts"] == 3
        assert result["effective_limits"]["daily_follows"] == 1


def test_follow_limit_rises_in_clean_visibility_stages() -> None:
    early = apply_x_health_limits(settings(), {
        "risk_level": 0,
        "classification": "healthy",
        "healthy_streak": 6,
    })
    assert early["follow_daily_limit"] == 3
    assert early["follow_min_interval_hours"] == 6

    state = {
        "risk_level": 0,
        "classification": "healthy",
        "healthy_streak": 14,
    }
    result = apply_x_health_limits(settings(), state)
    assert result["follow_daily_limit"] == 4
    assert result["follow_min_interval_hours"] == 5
    assert result["daily_post_limit"] == 5

    mature = apply_x_health_limits(settings(), {
        "risk_level": 0,
        "classification": "healthy",
        "healthy_streak": 28,
    })
    assert mature["follow_daily_limit"] == 5
    assert mature["follow_min_interval_hours"] == 4


def test_account_change_does_not_reuse_old_health_penalty() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        previous = load_x_health_state(root, "indanya_sns")
        previous.update({"risk_level": 3, "classification": "restricted"})
        save_x_health_state(root, previous)
        current = load_x_health_state(root, "hentai596")
        assert current["account_handle"] == "hentai596"
        assert current["risk_level"] == 0
        assert current["classification"] == "unknown"


def test_forced_follow_still_stops_during_health_pause() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        state = load_x_health_state(root, "hentai596")
        state.update({"risk_level": 2, "classification": "restricted"})
        save_x_health_state(root, state)
        result = run_due_x_follow_cycle(root, force=True)
        assert result["result"] == "health_paused"
