#!/usr/bin/env python3
"""
TAOP Agent Workforce — Telegram Bot
Dispatch tasks to agents from your phone.

Commands:
    /start          - Welcome message
    /agents         - List all agents
    /ask aria task  - Send task to specific agent
    /auto task      - Auto-route task to best agent
    /team task      - Send to aria, max, gemma
    /status         - Task statistics
    /queue          - View recent tasks
    /task 42        - View full task output
"""
import os
import sys
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.engine import dispatch, dispatch_to_team
from core.agents import list_agents, find_best_agent, get_agent, AGENTS
from core.tasks import get_stats, get_recent, get_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Max message length for Telegram ─────────────────────────
MAX_MSG = 4000


def truncate(text: str, limit: int = MAX_MSG) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 50] + "\n\n⚠️ Output truncated. Use /task <id> for full output."


# ── Bot Handlers ────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔴 *TAOP AGENT WORKFORCE*\n"
        "_theartofthepossible.io_\n\n"
        "Your AI army is online. Commands:\n\n"
        "🤖 `/agents` — List all agents\n"
        "⚡ `/ask <agent> <task>` — Assign task\n"
        "🎯 `/auto <task>` — Auto-route to best agent\n"
        "📡 `/team <task>` — Broadcast to team\n"
        "📊 `/status` — Task stats\n"
        "📋 `/queue` — Recent tasks\n"
        "🔍 `/task <id>` — Full task output\n\n"
        "_Or just type a message and I'll auto-route it._",
        parse_mode="Markdown",
    )


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🤖 *AGENT ROSTER*\n"]
    for a in list_agents():
        lines.append(f"{a['emoji']} *{a['name']}* `[{a['badge']}]` — {a['title']} _({a['engine']})_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: `/ask <agent> <task>`\nExample: `/ask aria Build a landing page`", parse_mode="Markdown")
        return
    
    agent_name = args[0].lower()
    task = " ".join(args[1:])
    
    agent = get_agent(agent_name)
    if not agent:
        await update.message.reply_text(f"❌ Unknown agent: `{agent_name}`\nUse `/agents` to see available agents.", parse_mode="Markdown")
        return
    
    msg = await update.message.reply_text(f"{agent['emoji']} *{agent['name']}* is working on it...", parse_mode="Markdown")
    
    result = dispatch(agent_name, task)
    
    if result["status"] == "done":
        response = (
            f"✅ *Task #{result['task_id']} — DONE*\n"
            f"Agent: {agent['emoji']} {result['agent']}\n"
            f"Tokens: {result['tokens_used']:,}\n\n"
            f"{truncate(result['output'])}"
        )
    else:
        response = f"❌ *Task #{result['task_id']} — FAILED*\n\n{result.get('error', 'Unknown error')}"
    
    await msg.edit_text(response, parse_mode="Markdown")


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/auto <task>`\nI'll pick the best agent.", parse_mode="Markdown")
        return
    
    task = " ".join(args)
    best = find_best_agent(task)
    agent = get_agent(best)
    
    msg = await update.message.reply_text(
        f"🎯 Auto-routing to {agent['emoji']} *{agent['name']}*...",
        parse_mode="Markdown",
    )
    
    result = dispatch("auto", task)
    
    if result["status"] == "done":
        response = (
            f"✅ *Task #{result['task_id']} — DONE*\n"
            f"Agent: {agent['emoji']} {result['agent']} _(auto-routed)_\n"
            f"Tokens: {result['tokens_used']:,}\n\n"
            f"{truncate(result['output'])}"
        )
    else:
        response = f"❌ *Task #{result['task_id']} — FAILED*\n\n{result.get('error', 'Unknown error')}"
    
    await msg.edit_text(response, parse_mode="Markdown")


async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/team <task>`\nSends to Aria, Max, and Gemma.", parse_mode="Markdown")
        return
    
    task = " ".join(args)
    msg = await update.message.reply_text("📡 *Broadcasting to team...*", parse_mode="Markdown")
    
    results = dispatch_to_team(task)
    
    lines = ["📡 *TEAM RESPONSE*\n"]
    for r in results:
        agent = get_agent(r.get("agent_key", ""))
        emoji = agent["emoji"] if agent else "?"
        if r["status"] == "done":
            preview = r["output"].strip().split("\n")[0][:100]
            lines.append(f"✅ {emoji} *{r['agent']}* — Task #{r['task_id']}\n_{preview}_\n")
        else:
            lines.append(f"❌ {emoji} *{r['agent']}* — FAILED\n")
    
    lines.append(f"\n_Use /task <id> for full output._")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_stats()
    lines = [
        "📊 *TASK STATS*\n",
        f"Total tasks: *{stats['total_tasks']}*",
        f"Total tokens: *{stats['total_tokens']:,}*\n",
    ]
    
    status_emoji = {"done": "✅", "working": "⏳", "queued": "📋", "failed": "❌"}
    for status, count in stats["by_status"].items():
        lines.append(f"{status_emoji.get(status, '•')} {status.upper()}: {count}")
    
    if stats["by_agent"]:
        lines.append("\n*BY AGENT*")
        for agent, data in stats["by_agent"].items():
            a = get_agent(agent)
            emoji = a["emoji"] if a else "•"
            lines.append(f"{emoji} {agent.upper()}: {data['total']} total, {data['done']} done")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = get_recent(10)
    if not tasks:
        await update.message.reply_text("📋 No tasks yet. Send `/ask <agent> <task>` to get started.", parse_mode="Markdown")
        return
    
    lines = ["📋 *RECENT TASKS*\n"]
    status_emoji = {"done": "✅", "working": "⏳", "queued": "📋", "failed": "❌"}
    for t in tasks:
        a = get_agent(t["agent"])
        emoji = a["emoji"] if a else "•"
        se = status_emoji.get(t["status"], "•")
        lines.append(f"{se} `#{t['id']}` {emoji} *{t['agent'].upper()}* — {t['title'][:40]}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_task_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/task <id>`", parse_mode="Markdown")
        return
    
    try:
        task_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Task ID must be a number.", parse_mode="Markdown")
        return
    
    t = get_task(task_id)
    if not t:
        await update.message.reply_text(f"❌ Task #{task_id} not found.", parse_mode="Markdown")
        return
    
    a = get_agent(t["agent"])
    emoji = a["emoji"] if a else "•"
    status_emoji = {"done": "✅", "working": "⏳", "queued": "📋", "failed": "❌"}
    
    lines = [
        f"{status_emoji.get(t['status'], '•')} *Task #{t['id']}*\n",
        f"Agent: {emoji} *{t['agent'].upper()}*",
        f"Priority: `{t['priority']}`",
        f"Status: `{t['status']}`",
    ]
    if t["tokens_used"]:
        lines.append(f"Tokens: {t['tokens_used']:,}")
    
    lines.append(f"\n*Task:* {t['description'][:200]}")
    
    if t["output"]:
        lines.append(f"\n*Output:*\n{truncate(t['output'], 3000)}")
    
    if t["error"]:
        lines.append(f"\n❌ *Error:* {t['error'][:500]}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-route any plain message to the best agent."""
    text = update.message.text.strip()
    if not text:
        return
    
    best = find_best_agent(text)
    agent = get_agent(best)
    
    msg = await update.message.reply_text(
        f"🎯 Auto-routing to {agent['emoji']} *{agent['name']}*...",
        parse_mode="Markdown",
    )
    
    result = dispatch(best, text)
    
    if result["status"] == "done":
        response = (
            f"✅ *Task #{result['task_id']}* — {agent['emoji']} *{result['agent']}*\n"
            f"_Tokens: {result['tokens_used']:,}_\n\n"
            f"{truncate(result['output'])}"
        )
    else:
        response = f"❌ *FAILED*\n\n{result.get('error', 'Unknown error')}"
    
    await msg.edit_text(response, parse_mode="Markdown")


# ── Main ────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)
    
    print("🤖 TAOP Agent Workforce — Telegram Bot starting...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("task", cmd_task_detail))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Bot is live. Listening for commands...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
