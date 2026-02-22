"""
Telegram approval bot — 4-option layout.

Message shows:
  A — Challenge   B — Expand   C — Nuanced   D — Question
  + top existing reply for context

Button layout:
  Row 1: [🟢 A]  [🔵 B]  [🟠 C]  [🟣 D]      ← manual
  Row 2: [🚀 A]  [🚀 B]  [🚀 C]  [🚀 D]      ← auto-post
  Row 3: [✏️ Edit]  [🔴 Skip]  [➕ Watch @x]
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
ACCOUNTS_JSON = Path(__file__).parent.parent / "config" / "accounts.json"

TONE_LABEL = {"challenge": "Challenge", "expand": "Expand", "nuanced": "Nuanced", "question": "Question"}
TONE_LETTER = {"challenge": "A", "expand": "B", "nuanced": "C", "question": "D"}
TONE_DESC = {
    "challenge": "disputes a core claim with technical backing",
    "expand":    "adds an angle or data point the post missed",
    "nuanced":   "validates the insight but adds a key caveat",
    "question":  "expert question that invites the author to reply",
}
TONES = ["challenge", "expand", "nuanced", "question"]


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt(n) -> str:
    try:
        n = int(n)
    except Exception:
        return "?"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


def _ago(created_at) -> str:
    if not created_at:
        return "?"
    try:
        from datetime import timezone
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        s = (now - created_at).total_seconds()
        return f"{int(s/60)}m" if s < 3600 else f"{int(s/3600)}h"
    except Exception:
        return "?"


def _trunc(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[:n-3] + "..."


def _h(text: str) -> str:
    """HTML-escape user-generated text for Telegram HTML mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(post: dict, comments: dict) -> str:
    handle   = post.get("author_handle", "unknown")
    follows  = _fmt(post.get("author_followers", 0))
    views    = _fmt(post.get("views", 0))
    likes    = _fmt(post.get("likes", 0))
    replies  = _fmt(post.get("replies", 0))
    ago      = _ago(post.get("created_at"))
    score    = post.get("score", 0)
    verified = " ✓" if post.get("author_verified") else ""
    url      = post.get("url", "")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Score: {score:.1f}  |  @{_h(handle)}{verified} ({follows})  |  ⚡ {ago} ago",
        f"👁 {views} views · {likes} likes · {replies} replies",
        "",
        f"📝 <b>POST:</b>",
        _h(post.get("text", "")),   # full text, no truncation
        "",
        "─────────────────────────────",
    ]

    for tone in TONES:
        text, _ = comments.get(tone, ("", []))
        if text:
            letter = TONE_LETTER[tone]
            label  = TONE_LABEL[tone]
            desc   = TONE_DESC[tone]
            lines.append(f"<b>{letter} · {label}</b>  <i>— {desc}</i>")
            lines.append(f"<code>{_h(text)}</code>")
            lines.append("")

    # Show top existing reply if available
    top_replies = post.get("top_replies", [])
    if top_replies:
        r = top_replies[0]
        lines.append("─────────────────────────────")
        lines.append(f"🗣 <i>Top reply @{_h(r['handle'])}: \"{_h(_trunc(r['text'], 120))}\"</i>")
        lines.append("")

    lines.append(f"🔗 {url}")
    lines.append("")
    lines.append("📋 copy  ·  🚀 auto-post")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_buttons(post_id: str, author_handle: str) -> InlineKeyboardMarkup:
    h = (author_handle or "?")[:12]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 A", callback_data=f"manual_challenge|{post_id}"),
            InlineKeyboardButton("📋 B", callback_data=f"manual_expand|{post_id}"),
            InlineKeyboardButton("📋 C", callback_data=f"manual_nuanced|{post_id}"),
            InlineKeyboardButton("📋 D", callback_data=f"manual_question|{post_id}"),
        ],
        [
            InlineKeyboardButton("🚀 Auto A", callback_data=f"auto_challenge|{post_id}"),
            InlineKeyboardButton("🚀 Auto B", callback_data=f"auto_expand|{post_id}"),
            InlineKeyboardButton("🚀 Auto C", callback_data=f"auto_nuanced|{post_id}"),
            InlineKeyboardButton("🚀 Auto D", callback_data=f"auto_question|{post_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Edit",  callback_data=f"edit|{post_id}"),
            InlineKeyboardButton("🔴 Skip",  callback_data=f"skip|{post_id}"),
            InlineKeyboardButton(f"➕ @{h}", callback_data=f"watch|{post_id}"),
        ],
    ])


# ── Post-only format (on-demand search — no AI comments) ──────────────────────

def format_post_only(post: dict) -> str:
    handle   = post.get("author_handle", "unknown")
    follows  = _fmt(post.get("author_followers", 0))
    views    = _fmt(post.get("views", 0))
    likes    = _fmt(post.get("likes", 0))
    replies  = _fmt(post.get("replies", 0))
    ago      = _ago(post.get("created_at"))
    verified = " ✓" if post.get("author_verified") else ""
    url      = post.get("url", "")

    return "\n".join([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"@{_h(handle)}{verified} ({follows})  |  ⚡ {ago} ago",
        f"👁 {views} views · {likes} likes · {replies} replies",
        "",
        _h(post.get("text", "")),
        "",
        f"🔗 {url}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ])


def build_post_only_buttons(post_id: str, author_handle: str) -> InlineKeyboardMarkup:
    h = (author_handle or "?")[:12]
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 Skip",  callback_data=f"skip|{post_id}"),
        InlineKeyboardButton(f"➕ @{h}", callback_data=f"watch|{post_id}"),
    ]])


def send_post_only(post: dict) -> bool:
    import asyncio

    async def _send():
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=format_post_only(post),
            parse_mode="HTML",
            reply_markup=build_post_only_buttons(post["id"], post.get("author_handle", "")),
            disable_web_page_preview=True,
        )

    try:
        asyncio.run(_send())
        return True
    except Exception as e:
        logger.error(f"Failed to send post-only message: {e}")
        return False


# ── Send ──────────────────────────────────────────────────────────────────────

def send_post(post: dict, comments: dict) -> bool:
    import asyncio

    async def _send():
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=format_message(post, comments),
            parse_mode="HTML",
            reply_markup=build_buttons(post["id"], post.get("author_handle", "")),
            disable_web_page_preview=True,
        )

    try:
        asyncio.run(_send())
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def send_message(text: str) -> bool:
    import asyncio

    async def _send():
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )

    try:
        asyncio.run(_send())
        return True
    except Exception as e:
        logger.error(f"send_message failed: {e}")
        return False


# ── DB ────────────────────────────────────────────────────────────────────────

_DB = None


def _get_db():
    global _DB
    if _DB is None:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from modules.database import Database
        _DB = Database()
    return _DB


# ── Watchlist ─────────────────────────────────────────────────────────────────

def _add_to_accounts_json(handle: str) -> bool:
    try:
        with open(ACCOUNTS_JSON) as f:
            accounts = json.load(f)
    except Exception:
        accounts = []
    if any(a["handle"].lower() == handle.lower() for a in accounts):
        return False
    accounts.append({"handle": handle, "priority": "medium", "check_every_hours": 6})
    with open(ACCOUNTS_JSON, "w") as f:
        json.dump(accounts, f, indent=2)
    return True


# ── Auto-post runner ──────────────────────────────────────────────────────────

def _run_auto_post(tweet_url: str, comment: str, post_id: str,
                   approval_id: int, tone: str):
    import asyncio
    from modules.autoposter import auto_post_reply

    success = auto_post_reply(tweet_url, comment)
    db = _get_db()

    if success:
        db.mark_posted(approval_id)
        db.update_post_status(post_id, "posted")
        msg = (
            f"✅ <b>Auto-posted! ({TONE_LABEL[tone]})</b>\n\n"
            f"🔗 {tweet_url}\n💬 `{comment}`"
        )
    else:
        msg = (
            f"❌ <b>Auto-post failed — post manually:</b>\n\n"
            f"🔗 {tweet_url}\n\n💬 Copy & paste:\n`{comment}`"
        )

    async def _notify():
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID, text=msg,
            parse_mode="HTML", disable_web_page_preview=True,
        )

    try:
        asyncio.run(_notify())
    except Exception as e:
        logger.error(f"Auto-post notification failed: {e}")


# ── Button handler ────────────────────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    if "|" not in query.data:
        return

    action, post_id = query.data.split("|", 1)

    # ── Search count — must be handled BEFORE the DB post lookup ──────────────
    if action == "scount":
        try:
            count = int(post_id)
        except ValueError:
            return
        topic = context.user_data.pop("pending_search_topic", None)
        if not topic:
            await query.answer("Search expired — type the topic again.", show_alert=True)
            return
        await query.edit_message_text(
            f"🔍 Searching <b>{_h(topic)}</b> — top {count} posts by views...\n\nOpening browser...",
            parse_mode="HTML",
        )
        from modules.on_demand import run_search
        threading.Thread(
            target=run_search,
            args=(topic, count, BOT_TOKEN, CHAT_ID, query.message.message_id),
            daemon=True,
        ).start()
        return

    db   = _get_db()
    post = db.get_post(post_id)
    if not post:
        await query.edit_message_text(f"❌ Post <code>{post_id}</code> not found.")
        return

    comments_rows = db.get_comments_for_post(post_id)
    comment_map   = {r["comment_type"]: r for r in comments_rows}

    # ── Manual post ───────────────────────────────────────────────────────────
    if action.startswith("manual_"):
        tone = action.replace("manual_", "")
        row  = comment_map.get(tone)
        if not row:
            await query.answer(f"No {tone} comment found.", show_alert=True)
            return
        db.insert_approval(post_id, row["id"], f"manual_{tone}")
        db.update_post_status(post_id, "approved")
        await query.edit_message_text(
            f"✅ <b>📋 {TONE_LETTER[tone]} — {TONE_LABEL[tone]}</b>\n\n"
            f"🔗 {post['url']}\n\n"
            f"💬 <b>Tap to copy:</b>\n<code>{_h(row['text'])}</code>\n\n"
            f"📋 Open link → Reply → Paste → Post",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    # ── Auto post ─────────────────────────────────────────────────────────────
    elif action.startswith("auto_"):
        tone = action.replace("auto_", "")
        row  = comment_map.get(tone)
        if not row:
            await query.answer(f"No {tone} comment found.", show_alert=True)
            return
        approval_id = db.insert_approval(post_id, row["id"], f"auto_{tone}")
        db.update_post_status(post_id, "approved")
        await query.edit_message_text(
            f"🚀 <b>Auto-posting {TONE_LABEL[tone]}...</b>\n\n"
            f"🔗 {post['url']}\n💬 `{row['text']}`\n\n"
            f"_You'll get a confirmation when done._",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        threading.Thread(
            target=_run_auto_post,
            args=(post["url"], row["text"], post_id, approval_id, tone),
            daemon=True,
        ).start()

    # ── Edit ──────────────────────────────────────────────────────────────────
    elif action == "edit":
        context.user_data["editing_post_id"] = post_id
        await query.edit_message_text(
            f"✏️ <b>Edit mode</b>\n\nReply with your comment for:\n{post['url']}\n\nOr /cancel",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    # ── Skip ──────────────────────────────────────────────────────────────────
    elif action == "skip":
        db.update_post_status(post_id, "skipped")
        await query.edit_message_text(
            f"🔴 Skipped — _{post['url']}_",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    # ── Watch ─────────────────────────────────────────────────────────────────
    elif action == "watch":
        handle = post.get("author_handle", "")
        if not handle:
            await query.answer("No handle found.", show_alert=True)
            return
        json_added = _add_to_accounts_json(handle)
        db.add_to_watchlist(handle)
        note = f"➕ _Watching @{handle}_"
        try:
            await query.edit_message_text(
                query.message.text + f"\n\n{note}",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        status = "added to watchlist" if json_added else "already on watchlist"
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"{'➕' if json_added else '👀'} <b>@{handle} {status}</b>",
            parse_mode="HTML",
        )


# ── Custom comment handler ────────────────────────────────────────────────────

async def handle_custom_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post_id = context.user_data.get("editing_post_id")
    if not post_id:
        # Treat plain text as an on-demand search — ask count first
        topic = update.message.text.strip()
        if not topic:
            return
        await _ask_search_count(topic, update, context)
        return
    text = update.message.text.strip()
    db   = _get_db()
    post = db.get_post(post_id)
    if not post:
        await update.message.reply_text("❌ Post not found.")
        return
    cid = db.insert_comment(post_id, "custom", text, [])
    db.insert_approval(post_id, cid, "custom", text)
    db.update_post_status(post_id, "approved")
    context.user_data.pop("editing_post_id", None)
    await update.message.reply_text(
        f"✅ <b>Custom comment saved</b>\n\n🔗 {post['url']}\n\n💬 <code>{_h(text)}</code>\n\n📋 Open → Reply → Paste → Post",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("editing_post_id", None)
    await update.message.reply_text("Cancelled.")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db    = _get_db()
    stats = db.daily_stats()
    rate  = (
        f"{stats['approved']/stats['posts_discovered']*100:.1f}%"
        if stats["posts_discovered"] > 0 else "N/A"
    )
    await update.message.reply_text(
        f"📊 <b>Today's Stats</b>\n\n"
        f"🔍 Discovered: {stats['posts_discovered']}\n"
        f"✅ Approved: {stats['approved']}\n"
        f"📤 Posted: {stats['posted']}\n"
        f"📈 Approval rate: {rate}",
        parse_mode="HTML",
    )


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open(ACCOUNTS_JSON) as f:
            accounts = json.load(f)
        by_priority = {"high": [], "medium": [], "low": []}
        for a in accounts:
            by_priority.get(a.get("priority", "medium"), []).append(a["handle"])
        msg = f"👀 <b>Watchlist ({len(accounts)} accounts)</b>\n\n"
        for p, handles in by_priority.items():
            if handles:
                msg += f"{'🔴' if p=='high' else '🟡' if p=='medium' else '⚪'} {p.capitalize()}: {', '.join('@'+h for h in handles)}\n\n"
    except Exception as e:
        msg = f"❌ {e}"
    await update.message.reply_text(msg, parse_mode="HTML")


async def _ask_search_count(topic: str, update, context):
    """Store topic and ask user how many posts (1–5) they want."""
    context.user_data["pending_search_topic"] = topic
    await update.message.reply_text(
        f"🔍 <b>{_h(topic)}</b>\n\nHow many posts? (sorted by highest views)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("1", callback_data="scount|1"),
            InlineKeyboardButton("2", callback_data="scount|2"),
            InlineKeyboardButton("3", callback_data="scount|3"),
            InlineKeyboardButton("4", callback_data="scount|4"),
            InlineKeyboardButton("5", callback_data="scount|5"),
        ]]),
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip() if context.args else ""
    if not topic:
        await update.message.reply_text(
            "Just type any topic directly — no command needed.\n\n"
            "Example: <code>transformer new architecture</code>",
            parse_mode="HTML",
        )
        return
    await _ask_search_count(topic, update, context)


# ── Run ───────────────────────────────────────────────────────────────────────

def run_bot():
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("cancel",    cmd_cancel))
    app.add_handler(CommandHandler("report",    cmd_report))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("search",    cmd_search))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_comment))
    logger.info("Telegram bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    run_bot()
