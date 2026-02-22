"""
Twitter scraper using Selenium with cookie auth and proxy support.
Architecture adapted from github.com/ihuzaifashoukat/twitter-automation-ai
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, quote_plus

from dotenv import load_dotenv
import os

load_dotenv()

logger = logging.getLogger(__name__)

# ── XPath selectors (from reference repo) ────────────────────────────────────
TWEET_ARTICLE_XPATH = "//article[@data-testid='tweet']"
USER_NAME_XPATH     = ".//div[@data-testid='User-Name']//span[1]//span"
USER_HANDLE_XPATH   = ".//div[@data-testid='User-Name']//span[contains(text(), '@')]"
TWEET_TEXT_XPATH    = ".//div[@data-testid='tweetText']//span | .//div[@data-testid='tweetText']//a"
STATUS_LINK_XPATH   = ".//a[contains(@href, '/status/') and .//time]"
TIME_TAG_XPATH      = ".//time"
ENGAGEMENT_BTN_XPATH = ".//button[@data-testid='{testid}']//span[@data-testid='app-text-transition-container']//span"
ANALYTICS_XPATH     = ".//a[contains(@href, '/analytics')]//span[@data-testid='app-text-transition-container']//span"
VERIFIED_XPATH      = ".//*[local-name()='svg' and @data-testid='icon-verified']"

THREAD_INDICATORS = [r"\(\d+/\d+\)", r"\d+/\d+", "thread", "🧵"]


def _parse_count(text: str) -> int:
    if not text:
        return 0
    text = text.strip()
    try:
        if "K" in text:
            return int(float(text.replace("K", "")) * 1_000)
        if "M" in text:
            return int(float(text.replace("M", "")) * 1_000_000)
        return int(text)
    except Exception:
        return 0


# ── Browser setup ─────────────────────────────────────────────────────────────

def _build_driver(headless: bool = True, proxy: Optional[str] = None, use_undetected: bool = False):
    """Build a Chrome WebDriver with anti-detection settings."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    logger.info("Started standard Chrome WebDriver")
    return driver


def _load_cookies(path: str) -> list:
    p = Path(path)
    if not p.exists():
        logger.error(f"Cookie file not found: {p.resolve()}")
        return []
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cookies = []
    for c in raw:
        sc = {}
        for k, v in c.items():
            if k in ("expires", "expirationDate"):
                try:
                    sc["expiry"] = int(v)
                except Exception:
                    pass
            elif k == "httpOnly":
                sc["httpOnly"] = bool(v)
            elif k == "sameSite":
                s = str(v).strip().lower()
                if s in ("none", "no_restriction"):
                    sc["sameSite"] = "None"
                elif s == "lax":
                    sc["sameSite"] = "Lax"
                elif s == "strict":
                    sc["sameSite"] = "Strict"
            elif k in ("name", "value", "path", "domain", "secure"):
                sc[k] = v
        # remap twitter.com → x.com
        dom = sc.get("domain", "")
        if "twitter.com" in dom:
            sc["domain"] = dom.replace("twitter.com", "x.com")
        if "name" in sc and "value" in sc:
            cookies.append(sc)
    return cookies


def _apply_cookies(driver, cookies: list):
    try:
        driver.get("https://x.com")
        time.sleep(2)
    except Exception:
        pass
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception as e:
            logger.debug(f"Could not add cookie {c.get('name')}: {e}")
    try:
        driver.refresh()
        time.sleep(3)
    except Exception:
        pass


def _is_signed_in(driver) -> bool:
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//a[@data-testid='AppTabBar_Profile_Link']"))
        )
        return True
    except Exception:
        return False


# ── Tweet parsing ─────────────────────────────────────────────────────────────

def _parse_tweet_card(card, driver) -> Optional[dict]:
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException

    try:
        # Author
        user_name, user_handle = None, None
        try:
            user_name = card.find_element(By.XPATH, USER_NAME_XPATH).text
        except Exception:
            pass
        try:
            user_handle = card.find_element(By.XPATH, USER_HANDLE_XPATH).text.lstrip("@")
        except Exception:
            pass

        # Text
        parts = []
        try:
            els = card.find_elements(By.XPATH, TWEET_TEXT_XPATH)
            for el in els:
                try:
                    parts.append(el.text)
                except StaleElementReferenceException:
                    continue
        except StaleElementReferenceException:
            return None
        text = "".join(parts).strip()
        if not text:
            return None

        # URL / ID
        tweet_id, tweet_url = None, None
        try:
            link = card.find_element(By.XPATH, STATUS_LINK_XPATH)
            href = link.get_attribute("href")
            if href and "/status/" in href:
                tweet_url = href.split("?")[0]
                tweet_id = href.split("/status/")[-1].split("?")[0]
        except Exception:
            return None

        # Timestamp
        created_at = None
        try:
            t = card.find_element(By.XPATH, TIME_TAG_XPATH)
            dt_str = t.get_attribute("datetime")
            if dt_str:
                created_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            pass

        # Engagement counts
        def get_count(testid):
            try:
                el = card.find_element(By.XPATH, ENGAGEMENT_BTN_XPATH.format(testid=testid))
                return _parse_count(el.text)
            except Exception:
                return 0

        replies  = get_count("reply")
        retweets = get_count("retweet")
        likes    = get_count("like")

        views = 0
        try:
            v = card.find_element(By.XPATH, ANALYTICS_XPATH)
            views = _parse_count(v.text)
        except Exception:
            pass

        # Verified
        verified = False
        try:
            card.find_element(By.XPATH, VERIFIED_XPATH)
            verified = True
        except Exception:
            pass

        return {
            "id": tweet_id,
            "url": tweet_url,
            "author_handle": user_handle or "",
            "author_name": user_name or "",
            "author_verified": verified,
            "author_followers": 0,  # not visible in card view
            "text": text,
            "views": views,
            "likes": likes,
            "replies": replies,
            "retweets": retweets,
            "created_at": created_at,
        }
    except StaleElementReferenceException:
        return None
    except Exception as e:
        logger.error(f"Error parsing tweet card: {e}")
        return None


# ── Main scraper class ────────────────────────────────────────────────────────

class TwitterScraper:
    def __init__(self, config: dict):
        self.config = config
        scraping_cfg = config.get("scraping", {})
        self.headless        = scraping_cfg.get("headless", True)
        self.use_undetected  = scraping_cfg.get("use_undetected", True)
        self.posts_per_kw    = scraping_cfg.get("posts_per_keyword", 20)
        self.cookies_path    = os.getenv("TWITTER_COOKIES_PATH", "config/cookies.json")
        self.proxy           = os.getenv("PROXY_URL") or None
        self.driver          = None

    # ── Driver lifecycle ──────────────────────────────────────────────────────

    def start(self):
        if self.driver:
            return
        self.driver = _build_driver(
            headless=self.headless,
            proxy=self.proxy,
            use_undetected=self.use_undetected,
        )
        self.driver.set_page_load_timeout(30)
        self.driver.set_script_timeout(30)

        cookies = _load_cookies(self.cookies_path)
        if cookies:
            _apply_cookies(self.driver, cookies)
            if _is_signed_in(self.driver):
                logger.info("Signed in to x.com via cookies")
            else:
                logger.warning("Cookie login may have failed — some data might be missing")
        else:
            logger.warning("No cookies loaded — running without auth")

    def stop(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # ── Scraping helpers ──────────────────────────────────────────────────────

    def _scrape_url(self, url: str, max_tweets: int) -> list:
        from selenium.webdriver.common.by import By

        self.driver.get(url)
        time.sleep(5)

        tweets, seen_ids = [], set()
        no_new_count = 0
        MAX_NO_NEW = 5

        while len(tweets) < max_tweets:
            cards = self.driver.find_elements(By.XPATH, TWEET_ARTICLE_XPATH)
            new_this_scroll = 0

            for card in cards:
                if len(tweets) >= max_tweets:
                    break
                try:
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", card
                    )
                    time.sleep(0.15)
                except Exception:
                    pass
                parsed = _parse_tweet_card(card, self.driver)
                if parsed and parsed["id"] and parsed["id"] not in seen_ids:
                    tweets.append(parsed)
                    seen_ids.add(parsed["id"])
                    new_this_scroll += 1

            if new_this_scroll == 0:
                no_new_count += 1
                if no_new_count >= MAX_NO_NEW:
                    break
            else:
                no_new_count = 0

            if len(tweets) >= max_tweets:
                break

            # Scroll down
            self.driver.execute_script("window.scrollBy(0, window.innerHeight * 2)")
            time.sleep(random.uniform(1.5, 3.0))

        return tweets

    # ── Reply research ────────────────────────────────────────────────────────

    def scrape_tweet_replies(self, tweet_url: str, max_replies: int = 3) -> list:
        """
        Visit a tweet's page and extract top existing replies.
        Returns list of {"handle": str, "text": str, "likes": int}
        Used to give LLM context so it never duplicates what's already been said.
        """
        from selenium.webdriver.common.by import By

        try:
            self.driver.get(tweet_url)
            time.sleep(4)

            # All article elements on the page — first is the original tweet, rest are replies
            articles = self.driver.find_elements(By.XPATH, TWEET_ARTICLE_XPATH)
            replies = []

            for article in articles[1:]:  # skip first = the original tweet
                if len(replies) >= max_replies:
                    break
                try:
                    handle_el = article.find_elements(By.XPATH, USER_HANDLE_XPATH)
                    handle = handle_el[0].text.lstrip("@") if handle_el else ""

                    text_parts = []
                    for el in article.find_elements(By.XPATH, TWEET_TEXT_XPATH):
                        try:
                            text_parts.append(el.text)
                        except Exception:
                            continue
                    text = "".join(text_parts).strip()
                    if not text:
                        continue

                    likes = 0
                    try:
                        like_el = article.find_element(
                            By.XPATH, ENGAGEMENT_BTN_XPATH.format(testid="like")
                        )
                        likes = _parse_count(like_el.text)
                    except Exception:
                        pass

                    replies.append({"handle": handle, "text": text[:200], "likes": likes})
                except Exception:
                    continue

            # Sort by likes so best replies come first
            replies.sort(key=lambda r: r["likes"], reverse=True)
            return replies

        except Exception as e:
            logger.warning(f"Could not scrape replies for {tweet_url}: {e}")
            return []

    # ── Public scraping methods ───────────────────────────────────────────────

    def scrape_keyword(self, keyword: str, max_tweets: Optional[int] = None) -> list:
        if max_tweets is None:
            max_tweets = self.posts_per_kw
        encoded = quote_plus(keyword)
        # &f=live → live/recent tweets, not top
        url = f"https://x.com/search?q={encoded}&f=live&src=typed_query"
        logger.info(f"Scraping keyword: '{keyword}'")
        return self._scrape_url(url, max_tweets)

    def scrape_profile(self, handle: str, max_tweets: int = 5) -> list:
        handle = handle.lstrip("@")
        url = f"https://x.com/{handle}"
        logger.info(f"Scraping profile: @{handle}")
        return self._scrape_url(url, max_tweets)


# ── Module-level convenience functions ───────────────────────────────────────

def discover_posts_by_topic(scraper: TwitterScraper, keywords: list, seen_ids: set) -> list:
    all_posts = []
    for kw in keywords:
        try:
            posts = scraper.scrape_keyword(kw)
            new = [p for p in posts if p["id"] not in seen_ids]
            all_posts.extend(new)
            logger.info(f"  '{kw}': {len(new)} new posts")
            time.sleep(random.uniform(2, 5))  # polite delay between searches
        except Exception as e:
            logger.error(f"Error scraping keyword '{kw}': {e}")
    return all_posts


def check_monitored_accounts(scraper: TwitterScraper, accounts: list, db, seen_ids: set) -> list:
    from datetime import timedelta

    all_posts = []
    now = datetime.now(timezone.utc)

    for acct in accounts:
        handle = acct["handle"]
        interval_hours = acct.get("check_every_hours", 6)
        priority = acct.get("priority", "medium")

        last_check = db.get_last_check_time(handle)
        if last_check:
            last_check = last_check.replace(tzinfo=timezone.utc) if last_check.tzinfo is None else last_check
            if (now - last_check) < timedelta(hours=interval_hours):
                logger.debug(f"Skipping @{handle} (checked {interval_hours}h ago)")
                continue

        try:
            posts = scraper.scrape_profile(handle, max_tweets=5)
            new = [p for p in posts if p["id"] not in seen_ids]

            priority_boost = 3 if priority == "high" else (1 if priority == "medium" else 0)
            for p in new:
                p["priority_boost"] = priority_boost
                p["source"] = "account_monitor"

            all_posts.extend(new)
            db.update_last_check_time(handle)
            logger.info(f"  @{handle}: {len(new)} new posts")
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            logger.error(f"Error scraping @{handle}: {e}")

    return all_posts
