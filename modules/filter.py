"""
Post filtering and ranking module.
Scores posts by reach potential and removes already-seen/commented posts.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def score_post(post: dict) -> float:
    """
    Score a post by reach potential (0–20 scale).
    Higher = more likely to get visibility for a comment.
    """
    score = 0.0
    now = datetime.now(timezone.utc)

    # ── Author credibility ────────────────────────────────────────────────────
    followers = post.get("author_followers", 0)
    if followers > 50_000:
        score += 5
    elif followers > 10_000:
        score += 3
    elif followers > 1_000:
        score += 1

    # ── Current engagement ────────────────────────────────────────────────────
    views = post.get("views", 0)
    if views > 10_000:
        score += 5
    elif views > 5_000:
        score += 3
    elif views > 1_000:
        score += 2

    # ── Engagement velocity (views per hour since posted) ─────────────────────
    created_at = post.get("created_at")
    hours_old: Optional[float] = None
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        delta = now - created_at
        hours_old = delta.total_seconds() / 3600
        if hours_old > 0:
            velocity = views / hours_old
            if velocity > 1_000:
                score += 4
            elif velocity > 500:
                score += 2

    # ── Verified account ──────────────────────────────────────────────────────
    if post.get("author_verified"):
        score += 2

    # ── Reply / like activity (already getting attention) ────────────────────
    if post.get("likes", 0) > 50:
        score += 2
    if post.get("replies", 0) > 10:
        score += 2

    # ── Recency bonus (first-comment advantage) ───────────────────────────────
    if hours_old is not None:
        if hours_old < 3:
            score += 3
        elif hours_old < 12:
            score += 1

    # ── Priority boost from monitored accounts ────────────────────────────────
    score += post.get("priority_boost", 0)

    # ── Viral potential signals ───────────────────────────────────────────────
    post_text = post.get("text", "").lower()

    # First-mover advantage: high views but few replies = jump in early
    if views > 5_000 and post.get("replies", 0) < 20:
        score += 3

    # Debatable/hot-take signals = good for controversial comments
    debatable = [
        "vs ", " vs", "dying", "dead", "overhyped", "wrong", "actually",
        "controversial", "unpopular opinion", "hot take", "disagree",
        "overrated", "underrated", "nobody talks about", "stop using",
        "replace", "killed", "extinct", "broken",
    ]
    if any(s in post_text for s in debatable):
        score += 2

    # Comparison posts = great for nuanced technical takes (exclude "vs" — already in debatable)
    comparison = ["better than", "worse than", "compared to", "difference between"]
    if any(s in post_text for s in comparison):
        score += 1

    return score


def filter_and_rank_posts(posts: list, db, config: dict) -> list:
    """
    Filter posts and return the top N ranked by score.
    Removes:
      - Duplicates (same tweet ID)
      - Already seen/commented posts (from DB)
      - Posts older than max_post_age_hours
      - Posts below minimum thresholds
    """
    filtering = config.get("filtering", {})
    min_score  = filtering.get("min_score", 8)
    min_views  = filtering.get("min_views", 1_000)
    min_followers = filtering.get("min_author_followers", 1_000)
    top_n      = filtering.get("top_n_posts", 10)
    max_age_h  = config.get("scraping", {}).get("max_post_age_hours", 24)

    seen_db_ids = db.get_already_seen_ids()
    now = datetime.now(timezone.utc)

    seen_this_run: set = set()
    candidates = []

    for post in posts:
        pid = post.get("id")
        if not pid:
            continue

        # Deduplicate within this run
        if pid in seen_this_run:
            continue
        seen_this_run.add(pid)

        # Skip already-commented posts
        if pid in seen_db_ids:
            continue

        # Age filter
        created_at = post.get("created_at")
        if created_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            hours_old = (now - created_at).total_seconds() / 3600
            if hours_old > max_age_h:
                continue

        # Minimum thresholds (only apply if data available)
        if post.get("views", 0) > 0 and post["views"] < min_views:
            continue
        if post.get("author_followers", 0) > 0 and post["author_followers"] < min_followers:
            continue

        post_score = score_post(post)
        post["score"] = post_score
        post["source"] = post.get("source", "topic_search")

        if post_score >= min_score:
            candidates.append(post)

    # Sort by score descending
    candidates.sort(key=lambda p: p["score"], reverse=True)
    top = candidates[:top_n]

    logger.info(f"Filtered: {len(posts)} → {len(candidates)} scored → top {len(top)}")
    return top
