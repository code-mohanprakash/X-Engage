# 🐦 X-Engage: Twitter AI Engagement Agent

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Automated AI-powered Twitter engagement system that discovers high-value AI/ML tweets, generates contextual comments using LLMs, and routes them for approval via Telegram.

## 🎯 Features

- **🔍 Intelligent Scraping** — Monitor specific Twitter accounts and keywords in real-time
- **📊 Smart Filtering** — Score tweets by reach potential and engagement metrics
- **🤖 LLM-Powered Comments** — Generate 2 comment options per tweet using Groq/Gemini
- **💬 Telegram Approval Flow** — Review and approve comments before posting
- **🔄 Automated Posting** — One-click posting or manual edits before publishing
- **📈 Analytics** — Track engagement metrics and posting statistics
- **⏰ Scheduled Automation** — Run via cron every 2 hours for continuous engagement

## 🏗️ Architecture

```
┌─ Cron Scheduler (every 2h) ─┐
│                             │
├─ [1] Scraper               │
│   └─> Selenium + Cookies   │
│   └─> Keywords & Accounts  │
│                             │
├─ [2] Filter                │
│   └─> Score by reach       │
│   └─> Pick top 10          │
│                             │
├─ [3] Generator             │
│   └─> Groq llama-3.3-70b   │
│   └─> 2 comment variants   │
│                             │
├─ [4] Telegram              │
│   └─> Send options A & B   │
│   └─> Track user response  │
│                             │
└─ [5] Autoposter            │
    └─> Post on approval     │
    └─> Log metrics          │
```

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Active Twitter/X account
- Telegram bot token (@BotFather)
- API keys (Groq free tier recommended)

### Installation

```bash
# Clone the repository
git clone https://github.com/code-mohanprakash/X-Engage.git
cd twitter-agent

# Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run setup
python setup.py
```

### Configuration

1. **Create `.env` file** with your credentials:
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys
   nano .env
   ```

2. **Add Twitter Cookies**:
   - Install "Cookie-Editor" browser extension
   - Visit https://x.com and log in
   - Use Cookie-Editor → Export as JSON
   - Save to `config/cookies.json`

3. **Configure Keywords and Accounts**:
   - Edit `config/keywords.json` for search terms
   - Edit `config/accounts.json` to monitor specific accounts
   - Edit `config/config.yaml` for global settings

### Running

```bash
# Test mode (no Telegram notifications)
python main.py --test

# Full mode (sends to Telegram)
python main.py

# Start Telegram bot (separate terminal)
python -m modules.telegram_bot
```

## 🔑 Required API Keys

| Service | Purpose | Link | Free Tier |
|---------|---------|------|-----------|
| **Groq** | LLM for comments (Primary) | https://console.groq.com | ✅ Yes |
| **Gemini** | LLM fallback | https://makersuite.google.com | ✅ Yes |
| **Telegram** | Bot notifications | https://t.me/BotFather | ✅ Yes |
| **Twitter** | Authentication | https://x.com | ✅ Cookie-based |

## 📱 Telegram Commands

- `🟢 Post A` — Approve comment option A
- `🔵 Post B` — Approve comment option B
- `✏️ Edit` — Write your own comment
- `🔴 Skip` — Skip this tweet
- `/report` — View daily statistics

## ⏰ Deployment

### Local Cron Job

```bash
# Edit crontab
crontab -e

# Add this line (runs every 2 hours)
0 */2 * * * cd /path/to/twitter-agent && python main.py >> data/logs/cron.log 2>&1
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

### Cloud Deployment

- **Heroku**: Use `Procfile` + environment variables
- **AWS Lambda**: Trigger via EventBridge
- **Google Cloud**: Use Cloud Scheduler + Cloud Run
- **Oracle Cloud**: Free tier VM (always-on)

## 🔐 Security

**⚠️ IMPORTANT**: Never commit API keys or credentials!

- `.env` file is automatically excluded (in `.gitignore`)
- Cookies stored locally in `config/cookies.json` (excluded from git)
- Use environment variables in production
- See [SECURITY.md](../SECURITY.md) for detailed security guidelines

### Before First Run

```bash
# Verify .env is not tracked
git check-ignore -v .env

# Should output: .env
```

## 📁 Project Structure

```
twitter-agent/
├── main.py                 # Entry point
├── setup.py               # Initial setup script
├── requirements.txt       # Python dependencies
├── .env.example           # Template for environment variables
├── config/
│   ├── config.yaml        # Global settings
│   ├── keywords.json      # Search keywords
│   ├── accounts.json      # Accounts to monitor
│   └── cookies.json       # Twitter auth (local only)
├── modules/
│   ├── scraper.py         # Twitter scraping
│   ├── filter.py          # Post filtering & scoring
│   ├── generator.py       # LLM comment generation
│   ├── telegram_bot.py    # Telegram interface
│   ├── autoposter.py      # Auto-posting logic
│   ├── database.py        # Local db operations
│   └── on_demand.py       # Manual posting
└── data/
    └── logs/              # Application logs
```

## 🛠️ Modules

- **scraper.py** — Selenium-based Twitter/X automation
- **filter.py** — ML-based tweet relevance scoring
- **generator.py** — LLM (Groq/Gemini) powered comment generation
- **telegram_bot.py** — Telegram bot for approvals
- **autoposter.py** — Automated posting after approval
- **database.py** — SQLite for tracking engagement
- **on_demand.py** — Manual one-off posting

## 📊 Example Workflow

```
2:00 AM (Cron trigger)
  ↓
Scrape 30 accounts + keywords
  ↓
Find 50 relevant tweets
  ↓
Score and filter to top 10
  ↓
Generate 2 comment options each
  ↓
Send to Telegram (20 options total)
  ↓
User reviews on phone in spare time
  ↓
Click to post → Tweet is published
  ↓
Log engagement metrics
```

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

This tool is for educational and personal use. Comply with:
- Twitter's Terms of Service
- Local laws and regulations
- Platform automation policies

Misuse could result in account suspension.

## 🙋 Support

- 📖 [Security Guide](../SECURITY.md)
- 💬 [Issues](https://github.com/code-mohanprakash/X-Engage/issues)
- 🔗 [Twitter](https://x.com/mohanp_ai)

---

**Built with ❤️ for AI/ML Twitter community**
