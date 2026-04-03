# 🔴 TAOP Agent Workforce

**The Art of the Possible — AI Army Command System**

8 specialized AI agents. 3 AI engines (Claude, OpenAI, Gemini). 3 interfaces (Dashboard, CLI, Telegram). 1 CEO.

---

## Agent Roster

| Agent | Badge | Title | Engine | Domain |
|-------|-------|-------|--------|--------|
| 🧠 Aria | TA-001 | Chief Architect | Claude | Architecture, full-stack, frontend, design |
| ⚙️ Max | TA-002 | Senior Engineer | OpenAI | Backend, APIs, testing, deployment |
| 🔮 Gemma | TA-003 | Research Director | Gemini | Research, market intel, long-doc analysis |
| 📋 CJ | TA-005 | Product Owner | Claude | Specs, task breakdown, sprint planning |
| 🎨 Sofia | TA-004 | Head of UX | Claude | UX research, accessibility, emotional design |
| 🛡️ Dex | TA-009 | Support Lead | Claude | Customer comms, emails, tickets (EN/ES) |
| ✨ Luna | TA-007 | Content Lead | OpenAI | Social, blogs, marketing, copywriting |
| 🔧 Rafael | TA-010 | DevOps Lead | Claude | Docker, nginx, infra, monitoring, CI/CD |

---

## 5-Minute Setup

```bash
# 1. Clone or copy to your Ubuntu server
cd /home/johan
git clone <your-repo> taop-agents
cd taop-agents

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API keys
cp .env.example .env
nano .env   # Fill in your keys

# 4. Make launcher executable
chmod +x run.sh

# 5. Test it
./run.sh cli agents
./run.sh cli ask aria "Say hello and confirm you're online"
```

---

## Three Ways to Command

### 🖥️ Streamlit Dashboard (Visual War Room)
```bash
./run.sh dashboard
# Opens at http://localhost:8501
```

### 💻 CLI (Terminal — fast)
```bash
# Ask a specific agent
./run.sh cli ask aria "Build a React dashboard for revenue tracking"
./run.sh cli ask max "Write a FastAPI auth endpoint with JWT"
./run.sh cli ask gemma "Research LATAM streaming market size 2026"
./run.sh cli ask dex "Draft a welcome email for new clients in Spanish"
./run.sh cli ask luna "Write 3 LinkedIn posts about AI workforce"

# Auto-route (system picks the best agent)
./run.sh cli ask auto "We need a pricing page for dopamine.watch"

# Send to multiple agents at once
./run.sh cli team "Review our go-to-market strategy" aria gemma cj

# View tasks
./run.sh cli status
./run.sh cli queue
./run.sh cli task 1
```

### 📱 Telegram Bot (from your phone)
```bash
./run.sh bot
```
Then in Telegram:
- `/ask aria Build a hero section for theartofthepossible.io`
- `/auto Research competitor pricing for analytics platforms`
- `/team Brainstorm 5 product ideas for ADHD users`
- `/status` `/queue` `/task 1`
- Or just send any message — auto-routes to the best agent

### 🚀 Run Everything
```bash
./run.sh all   # Dashboard + Telegram Bot simultaneously
```

---

## Run as Background Service (Ubuntu)

```bash
# Split the systemd-services.conf into two files:
# /etc/systemd/system/taop-dashboard.service
# /etc/systemd/system/taop-bot.service

sudo systemctl daemon-reload
sudo systemctl enable taop-dashboard taop-bot
sudo systemctl start taop-dashboard taop-bot

# Check status
sudo systemctl status taop-dashboard taop-bot

# View logs
journalctl -u taop-dashboard -f
journalctl -u taop-bot -f
```

---

## Architecture

```
taop-agents/
├── .env                    ← Your API keys (never commit)
├── run.sh                  ← One launcher for everything
├── requirements.txt
├── taop_tasks.db           ← Auto-created SQLite task queue
├── core/
│   ├── config.py           ← Settings + API key loader
│   ├── agents.py           ← 8 agent definitions + auto-router
│   ├── tasks.py            ← SQLite task queue (persistent)
│   └── engine.py           ← API dispatch (Claude/OpenAI/Gemini)
├── interfaces/
│   ├── dashboard.py        ← Streamlit War Room
│   ├── cli.py              ← Terminal interface
│   └── telegram_bot.py     ← Mobile Telegram bot
└── systemd-services.conf   ← Ubuntu service configs
```

---

## Task Flow

1. **You dispatch** → CLI, Dashboard, or Telegram
2. **Router picks agent** → or you specify one directly
3. **Task created** → SQLite queue with status tracking
4. **Agent executes** → Calls Claude/OpenAI/Gemini with specialized prompt
5. **Output stored** → Full response + token count in DB
6. **You review** → via any of the 3 interfaces

---

## The Philosophy

Every agent has a personality, a skill set, and operating rules. They don't just "call an API" — they think like the role they play. Aria thinks like an architect. Max thinks like a production engineer. Gemma thinks like a researcher. Dex writes emails that make customers feel heard.

This is not a chatbot wrapper. This is a workforce.

---

*Built by The Art of the Possible · theartofthepossible.io*
