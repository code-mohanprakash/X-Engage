"""
On-demand search triggered from Telegram.
User sends a topic → bot asks how many posts (1–5) → scrapes Twitter →
returns top N posts sorted by traction (views). No AI comment generation.
Runs in a background thread so it never blocks Telegram polling.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from telegram import Bot

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent


def _load_config() -> dict:
    import yaml
    with open(BASE_DIR / "config" / "config.yaml") as f:
        return yaml.safe_load(f)


def run_search(topic: str, count: int, bot_token: str, chat_id: int, status_message_id: int):
    """
    Background thread entry point.
    Searches Twitter for `topic`, returns top `count` posts sorted by views.
    No comment generation — just raw posts with full text.
    """
    import sys
    sys.path.insert(0, str(BASE_DIR))

    from modules.scraper import TwitterScraper
    from modules.telegram_bot import send_post_only
    from modules.database import Database

    import copy
    config = copy.deepcopy(_load_config())
    config["scraping"]["max_post_age_hours"] = 48
    config["filtering"]["min_score"] = 0  # no score gate for on-demand

    db = Database()

    async def _edit(text: str):
        try:
            bot = Bot(token=bot_token)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    def _update(text: str):
        asyncio.run(_edit(text))

    try:
        _update(f"🔍 Searching Twitter for <b>{topic}</b>...\n\nOpening browser...")

        with TwitterScraper(config) as scraper:
            _update(f"🔍 Searching <b>{topic}</b>...\n\nScraping tweets...")

            raw_posts = scraper.scrape_keyword(topic, max_tweets=50)
            logger.info(f"[OnDemand] '{topic}': {len(raw_posts)} raw posts")

            if not raw_posts:
                _update(f"😕 No posts found for <b>{topic}</b>.\n\nTry a different keyword.")
                db.close()
                return

            # Filter: last 48h, not already seen
            seen_ids = db.get_already_seen_ids()
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=48)

            candidates = []
            for post in raw_posts:
                if post.get("id") in seen_ids:
                    continue
                created = post.get("created_at")
                if created:
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created < cutoff:
                        continue
                post["source"] = f"on_demand:{topic}"
                candidates.append(post)

            # Deduplicate within this batch
            seen_this = set()
            unique = []
            for p in candidates:
                if p["id"] not in seen_this:
                    seen_this.add(p["id"])
                    unique.append(p)

            # Sort by views (traction), take top `count`
            unique.sort(key=lambda p: (p.get("views") or 0), reverse=True)
            top_posts = unique[:count]

            if not top_posts:
                _update(
                    f"😕 Found posts for <b>{topic}</b> but none passed filters.\n"
                    f"(All were older than 48h or already seen.)"
                )
                db.close()
                return

            _update(
                f"✅ Found <b>{len(top_posts)} posts</b> for <b>{topic}</b>\n\n"
                f"Sending now..."
            )

        # Send post-only cards (outside browser context)
        sent = 0
        for i, post in enumerate(top_posts, 1):
            try:
                db.insert_post(post)
                ok = send_post_only(post)
                if ok:
                    sent += 1
                    logger.info(f"[OnDemand] Sent post {i}/{len(top_posts)}: @{post.get('author_handle')}")
            except Exception as e:
                logger.error(f"[OnDemand] Error on post {i}: {e}")

        _update(
            f"✅ <b>{sent}/{len(top_posts)} posts for \"{topic}\"</b>\n\n"
            f"Sorted by highest views. Tap ➕ to watch an author."
        )
        db.close()

    except Exception as e:
        logger.error(f"[OnDemand] Search failed for '{topic}': {e}", exc_info=True)
        _update(f"❌ Search failed: {e}")
        try:
            db.close()
        except Exception:
            pass
