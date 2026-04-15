"""
Nightly JOAO summary using LLMRouter. Fixes the broken 12-day nightly summarizer.
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.llm_router import complete

logger = logging.getLogger("joao.nightly_summary")

async def run_nightly_summary():
    session_log = ""
    log_path = "/home/zamoritacr/joao-spine/JOAO_SESSION_LOG.md"
    try:
        with open(log_path, "r") as f:
            session_log = f.read()[-3000:]
    except Exception as e:
        logger.warning(f"Could not read session log: {e}")

    messages = [
        {"role":"system","content":"You are JOAO, Johan Zamora's AI exocortex. Generate precise daily intelligence briefings."},
        {"role":"user","content":f"""Generate a concise daily briefing for Johan.
Date: {datetime.now().strftime('%Y-%m-%d')}

SESSION LOG (last 3000 chars):
{session_log}

Write exactly:
1. KEY ACCOMPLISHMENTS TODAY (max 5 bullets)
2. ACTIVE ISSUES (max 3 bullets)
3. TOMORROW PRIORITIES (max 3 bullets)

Direct. No filler. Facts only."""}
    ]

    result = await complete(messages, task_type="summarization", max_tokens=1024)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    output = f"\n\n## NIGHTLY SUMMARY - {timestamp}\n\n{result}\n"

    with open(log_path, "a") as f:
        f.write(output)

    print(f"[OK] Summary written: {len(result)} chars")
    return result

if __name__ == "__main__":
    asyncio.run(run_nightly_summary())
