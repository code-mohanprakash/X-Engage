"""
One-time setup script for the Twitter Engagement Agent.
Run this before running main.py for the first time.
"""

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent


def check_python():
    if sys.version_info < (3, 10):
        print("❌ Python 3.10+ required")
        sys.exit(1)
    print(f"✅ Python {sys.version.split()[0]}")


def install_deps():
    print("\nInstalling dependencies...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(BASE / "requirements.txt")])
    print("✅ Dependencies installed")


def setup_env():
    env_file = BASE / ".env"
    example  = BASE / ".env.example"
    if env_file.exists():
        print("✅ .env already exists")
        return
    import shutil
    shutil.copy(example, env_file)
    print("✅ Created .env from .env.example — please fill in your API keys!")


def setup_db():
    from modules.database import Database
    db = Database()
    db.close()
    print("✅ SQLite database initialized")


def check_env_vars():
    from dotenv import load_dotenv
    load_dotenv()

    required = ["GROQ_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"\n⚠️  Missing env vars: {', '.join(missing)}")
        print("   Edit .env and add your keys, then run setup again.")
    else:
        print("✅ All required env vars present")


def check_cookies():
    cookies_path = os.getenv("TWITTER_COOKIES_PATH", "config/cookies.json")
    p = BASE / cookies_path
    if p.exists():
        print(f"✅ Cookie file found: {p}")
    else:
        print(f"\n⚠️  Cookie file not found: {p}")
        print("   Export your x.com cookies using 'Cookie-Editor' browser extension")
        print("   → Export as JSON → save to config/cookies.json")


def main():
    print("=" * 50)
    print("  Twitter Engagement Agent — Setup")
    print("=" * 50)

    check_python()
    install_deps()
    setup_env()

    sys.path.insert(0, str(BASE))
    from dotenv import load_dotenv
    load_dotenv()

    check_env_vars()
    check_cookies()
    setup_db()

    print("\n" + "=" * 50)
    print("Setup complete!\n")
    print("Next steps:")
    print("  1. Edit .env with your API keys")
    print("  2. Add x.com cookies to config/cookies.json")
    print("  3. Test run:  python main.py --test")
    print("  4. Full run:  python main.py")
    print("  5. Bot:       python modules/telegram_bot.py  (separate terminal)")
    print("=" * 50)


if __name__ == "__main__":
    main()
