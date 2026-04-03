#!/bin/bash
# TAOP Agent Workforce — Launcher
# Usage: ./run.sh [dashboard|bot|cli|all]

set -e
cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${RED}  TAOP AGENT WORKFORCE${NC}"
echo -e "${YELLOW}  theartofthepossible.io${NC}"
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check .env
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  No .env file found. Copying from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}   Edit .env with your API keys before running.${NC}"
    exit 1
fi

case "${1:-help}" in
    dashboard)
        echo -e "${GREEN}🖥️  Starting Streamlit War Room...${NC}"
        streamlit run interfaces/dashboard.py --server.port 8501 --server.address 0.0.0.0
        ;;
    bot)
        echo -e "${GREEN}📱 Starting Telegram Bot...${NC}"
        python3 interfaces/telegram_bot.py
        ;;
    cli)
        shift
        python3 -m interfaces.cli "$@"
        ;;
    all)
        echo -e "${GREEN}🚀 Starting Dashboard + Telegram Bot...${NC}"
        streamlit run interfaces/dashboard.py --server.port 8501 --server.address 0.0.0.0 &
        DASH_PID=$!
        python3 interfaces/telegram_bot.py &
        BOT_PID=$!
        echo -e "${GREEN}✅ Dashboard: http://localhost:8501${NC}"
        echo -e "${GREEN}✅ Telegram Bot: running${NC}"
        echo -e "${YELLOW}   Press Ctrl+C to stop all.${NC}"
        trap "kill $DASH_PID $BOT_PID 2>/dev/null; exit" INT TERM
        wait
        ;;
    help|*)
        echo "Usage: ./run.sh [command]"
        echo ""
        echo "  dashboard    Start Streamlit War Room (port 8501)"
        echo "  bot          Start Telegram Bot"
        echo "  cli [args]   Run CLI commands"
        echo "  all          Start Dashboard + Bot together"
        echo ""
        echo "CLI examples:"
        echo "  ./run.sh cli agents"
        echo "  ./run.sh cli ask aria \"Build a landing page\""
        echo "  ./run.sh cli ask auto \"Research LATAM market\""
        echo "  ./run.sh cli team \"Review pricing\" aria gemma cj"
        echo "  ./run.sh cli status"
        echo "  ./run.sh cli queue"
        echo "  ./run.sh cli task 1"
        ;;
esac
