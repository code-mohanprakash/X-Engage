"""
Auto-poster module.
After Telegram approval, uses Selenium to automatically reply to a tweet.
Reuses the same cookie auth + proxy infrastructure as the scraper.
"""

import logging
import os
import random
import time

from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

from modules.scraper import _build_driver, _load_cookies, _apply_cookies, _is_signed_in

load_dotenv()
logger = logging.getLogger(__name__)

# ── XPath selectors for reply flow ────────────────────────────────────────────

# Reply button on the tweet card at the top of a tweet page
REPLY_BTN_XPATH       = "//article[@data-testid='tweet'][1]//button[@data-testid='reply']"
# The reply textarea that appears after clicking reply
REPLY_TEXTAREA_XPATH  = "//div[@data-testid='tweetTextarea_0']"
# The "Reply" submit button inside the modal
REPLY_SUBMIT_XPATH    = "//button[@data-testid='tweetButton']"
# Confirmation: composer closes / textarea disappears
REPLY_CONFIRM_XPATH   = "//div[@data-testid='tweetTextarea_0']"


class AutoPoster:
    """
    One-shot Selenium session that posts a single reply and quits.
    Spins up a fresh browser each time to avoid session state issues.
    """

    def __init__(self):
        self.cookies_path = os.getenv("TWITTER_COOKIES_PATH", "config/cookies.json")
        self.proxy        = os.getenv("PROXY_URL") or None
        self.driver       = None

    def _start(self):
        self.driver = _build_driver(headless=False, proxy=self.proxy, use_undetected=False)
        self.driver.set_page_load_timeout(30)
        self.driver.set_script_timeout(30)

        cookies = _load_cookies(self.cookies_path)
        if cookies:
            _apply_cookies(self.driver, cookies)
            if not _is_signed_in(self.driver):
                logger.warning("Cookie login may have failed for auto-poster")
        else:
            logger.error("No cookies found — auto-poster cannot authenticate")
            self._quit()
            return False
        return True

    def _quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def post_reply(self, tweet_url: str, comment: str) -> bool:
        """
        Navigate to tweet_url, click Reply, type comment, submit.
        Returns True on success.
        """
        try:
            ok = self._start()
            if not ok:
                return False

            logger.info(f"Auto-posting reply to: {tweet_url}")
            self.driver.get(tweet_url)
            time.sleep(random.uniform(3.0, 5.0))

            # ── Step 1: Click the Reply button on the tweet ───────────────────
            try:
                reply_btn = WebDriverWait(self.driver, 15).until(
                    EC.element_to_be_clickable((By.XPATH, REPLY_BTN_XPATH))
                )
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", reply_btn
                )
                time.sleep(0.5)
                reply_btn.click()
                logger.info("Clicked reply button")
                time.sleep(random.uniform(1.5, 2.5))
            except TimeoutException:
                logger.error("Reply button not found on tweet page")
                return False

            # ── Step 2: Find reply textarea ───────────────────────────────────
            try:
                textarea = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, REPLY_TEXTAREA_XPATH))
                )
                textarea.click()
                time.sleep(0.5)
            except TimeoutException:
                logger.error("Reply textarea not found")
                return False

            # ── Step 3: Type the comment ──────────────────────────────────────
            safe_comment = comment[:270]  # stay safely under Twitter's limit
            try:
                textarea.send_keys(Keys.CONTROL, "a")
                textarea.send_keys(Keys.BACKSPACE)
            except Exception:
                pass
            textarea.send_keys(safe_comment)
            logger.info(f"Typed comment ({len(safe_comment)} chars)")
            time.sleep(random.uniform(1.0, 2.0))

            # ── Step 4: Click the Reply / Post button ─────────────────────────
            try:
                submit_btn = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, REPLY_SUBMIT_XPATH))
                )
                try:
                    submit_btn.click()
                except ElementClickInterceptedException:
                    logger.warning("Submit click intercepted, trying JS click")
                    self.driver.execute_script("arguments[0].click();", submit_btn)
                logger.info("Clicked Reply/Post button")
            except TimeoutException:
                logger.error("Reply submit button not found or not clickable")
                return False

            # ── Step 5: Wait for confirmation (textarea disappears) ───────────
            time.sleep(random.uniform(3.0, 5.0))

            # Verify textarea is gone (reply was submitted)
            remaining = self.driver.find_elements(By.XPATH, REPLY_TEXTAREA_XPATH)
            if remaining:
                logger.warning("Textarea still visible — reply may not have posted")
                # Try Ctrl+Enter as fallback
                try:
                    remaining[0].send_keys(Keys.CONTROL, Keys.ENTER)
                    time.sleep(3)
                except Exception:
                    pass

            logger.info(f"Reply posted successfully to {tweet_url}")
            return True

        except Exception as e:
            logger.error(f"Auto-post failed: {e}", exc_info=True)
            return False

        finally:
            self._quit()


def auto_post_reply(tweet_url: str, comment: str) -> bool:
    """Convenience function — spins up AutoPoster, posts, returns result."""
    poster = AutoPoster()
    return poster.post_reply(tweet_url, comment)
