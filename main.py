"""
Twitter AI Engagement Agent — Main Orchestrator
Runs every 2 hours via cron (or manually).

Usage:
  python main.py              # Single run
  python main.py --loop       # Keep running every 2 hours
  python main.py --test       # Scrape 1 keyword, generate 1 comment, don't send
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CONFIG_DIR  = BASE_DIR / "config"
DATA_DIR    = BASE_DIR / "data"
LOG_DIR     = DATA_DIR / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "agent.log"),
    ],
)
logger = logging.getLogger("main")

# ── Config loader ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_DIR / "config.yaml", "r") as f:
        return yaml.safe_load(f)


def load_keywords() -> dict:
    with open(CONFIG_DIR / "keywords.json", "r") as f:
        return json.load(f)


def load_accounts() -> list:
    with open(CONFIG_DIR / "accounts.json", "r") as f:
        return json.load(f)


# ── Main run ──────────────────────────────────────────────────────────────────

def run_once(config: dict, test_mode: bool = False):
    from modules.database import Database
    from modules.scraper  import TwitterScraper, discover_posts_by_topic, check_monitored_accounts
    from modules.filter   import filter_and_rank_posts
    from modules.generator import generate_comments
    from modules.telegram_bot import send_post, send_message

    logger.info("=" * 60)
    logger.info("Twitter Engagement Agent — Starting Run")
    logger.info("=" * 60)

    db       = Database()
    keywords = load_keywords()
    accounts = load_accounts()

    # Only primary + viral_potential per hourly run (secondary adds too much time)
    primary_kws = keywords.get("primary", []) + keywords.get("viral_potential", [])
    if test_mode:
        primary_kws = primary_kws[:3]  # only 3 keywords in test mode

    try:
        with TwitterScraper(config) as scraper:
            seen_ids = db.get_already_seen_ids()

            # ── STEP 1: Discover posts ────────────────────────────────────────────
            logger.info("STEP 1: Discovering posts...")

            topic_posts = discover_posts_by_topic(scraper, primary_kws, seen_ids)
            logger.info(f"  Topic search: {len(topic_posts)} posts")

            account_posts = []
            if not test_mode:
                account_posts = check_monitored_accounts(scraper, accounts, db, seen_ids)
                logger.info(f"  Account monitoring: {len(account_posts)} posts")

            all_posts = topic_posts + account_posts
            logger.info(f"  Total discovered: {len(all_posts)} posts")

            if not all_posts:
                logger.warning("No posts discovered. Check cookie auth or keywords.")
                return

            # ── STEP 2: Filter & rank ─────────────────────────────────────────────
            logger.info("STEP 2: Filtering and ranking posts...")
            ranked = filter_and_rank_posts(all_posts, db, config)
            logger.info(f"  Top {len(ranked)} posts selected")

            if not ranked:
                logger.info("No posts passed the filter threshold. Done.")
                return

            if test_mode:
                ranked = ranked[:1]

            # ── STEP 3: Research existing replies ────────────────────────────────
            logger.info("STEP 3: Researching existing replies on top posts...")
            for post in ranked[:10]:
                try:
                    replies = scraper.scrape_tweet_replies(post["url"], max_replies=3)
                    post["top_replies"] = replies
                    if replies:
                        logger.info(f"  @{post.get('author_handle')}: {len(replies)} existing replies found")
                    else:
                        logger.info(f"  @{post.get('author_handle')}: no replies yet — first-mover opportunity")
                except Exception as e:
                    post["top_replies"] = []
                    logger.warning(f"  Could not fetch replies for {post.get('id')}: {e}")

            # ── STEP 4: Generate 4 comments per post ─────────────────────────────
            logger.info("STEP 4: Generating comments (4 tones per post)...")
            posts_with_comments = []

            for post in ranked[:10]:
                db.insert_post(post)
                try:
                    comments = generate_comments(post, config)
                    for tone, (text, issues) in comments.items():
                        if text:
                            db.insert_comment(post["id"], tone, text, issues)
                    posts_with_comments.append({"post": post, "comments": comments})
                    logger.info(f"  @{post.get('author_handle')} — score {post.get('score',0):.1f} — {len(comments)} options")
                except Exception as e:
                    logger.error(f"  Error generating for {post.get('id')}: {e}", exc_info=True)

            # ── STEP 5: Send to Telegram ──────────────────────────────────────────
            if test_mode:
                logger.info("TEST MODE: Preview (no Telegram send):")
                for item in posts_with_comments:
                    p = item["post"]
                    logger.info(f"\n  POST: {p.get('url')}")
                    for tone, (text, _) in item["comments"].items():
                        logger.info(f"  {tone.upper()}: {text[:80]}...")
            else:
                logger.info(f"STEP 5: Sending {len(posts_with_comments)} posts to Telegram...")
                sent_count = 0
                for item in posts_with_comments:
                    ok = send_post(item["post"], item["comments"])
                    if ok:
                        sent_count += 1
                        logger.info(f"  Sent: {item['post'].get('url')}")
                    else:
                        logger.warning(f"  Failed: {item['post'].get('url')}")
                    time.sleep(1)
                logger.info(f"Run complete: {sent_count}/{len(posts_with_comments)} posts sent to Telegram")

    finally:
        db.close()
        logger.info("=" * 60)


def daily_report(config: dict):
    from modules.database import Database
    from modules.telegram_bot import send_message

    db = Database()
    stats = db.daily_stats()
    db.close()

    rate = (
        f"{stats['approved']/stats['posts_discovered']*100:.1f}%"
        if stats["posts_discovered"] > 0 else "N/A"
    )

    msg = (
        f"📊 *Daily Report — {datetime.now().strftime('%Y-%m-%d')}*\n\n"
        f"🔍 Posts discovered: {stats['posts_discovered']}\n"
        f"✅ Comments approved: {stats['approved']}\n"
        f"📤 Comments posted: {stats['posted']}\n"
        f"📈 Approval rate: {rate}\n"
    )
    send_message(msg)
    logger.info("Daily report sent.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Twitter Engagement Agent")
    parser.add_argument("--loop",   action="store_true", help="Run every 2 hours")
    parser.add_argument("--test",   action="store_true", help="Test mode (no Telegram send)")
    parser.add_argument("--report", action="store_true", help="Send daily report and exit")
    args = parser.parse_args()

    config = load_config()

    if args.report:
        daily_report(config)
        return

    if args.loop:
        interval_h = config.get("scraping", {}).get("check_interval_hours", 2)
        interval_s = interval_h * 3600
        logger.info(f"Loop mode: running every {interval_h} hours")
        while True:
            try:
                run_once(config)
            except Exception as e:
                logger.error(f"Run failed: {e}", exc_info=True)
            logger.info(f"Sleeping {interval_h}h until next run...")
            time.sleep(interval_s)
    else:
        run_once(config, test_mode=args.test)


if __name__ == "__main__":
    main()
