#!/usr/bin/env python3
"""
TAOP Agent Workforce — Streamlit War Room Dashboard
Run: streamlit run interfaces/dashboard.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from core.engine import dispatch, dispatch_to_team
from core.agents import list_agents, get_agent, find_best_agent, AGENTS
from core.tasks import get_stats, get_queue, get_task, get_recent

# ── Page Config ─────────────────────────────────────────────
st.set_page_config(
    page_title="TAOP War Room",
    page_icon="🔴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ──────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
    
    .stApp { background-color: #0A0A0F; }
    
    .main-header {
        font-family: 'JetBrains Mono', monospace;
        color: #FF4444;
        font-size: 11px;
        letter-spacing: 6px;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .main-title {
        font-family: 'JetBrains Mono', monospace;
        color: #FFFFFF;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: -1px;
        margin-bottom: 24px;
    }
    
    .agent-card {
        background: #0D0D18;
        border: 1px solid #1E1E2E;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 8px;
    }
    .agent-name {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 2px;
    }
    .agent-title {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        color: #888;
    }
    
    .stat-box {
        background: #0D0D18;
        border: 1px solid #1E1E2E;
        border-radius: 8px;
        padding: 20px;
        text-align: center;
    }
    .stat-number {
        font-family: 'JetBrains Mono', monospace;
        font-size: 32px;
        font-weight: 700;
        color: #FFFFFF;
    }
    .stat-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10px;
        color: #666;
        letter-spacing: 3px;
        margin-top: 4px;
    }
    
    .task-row {
        background: #0D0D18;
        border: 1px solid #1E1E2E;
        border-radius: 4px;
        padding: 10px 14px;
        margin-bottom: 4px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
    }
    
    .output-box {
        background: #0A0A12;
        border: 1px solid #1E1E2E;
        border-radius: 8px;
        padding: 20px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        color: #CCC;
        white-space: pre-wrap;
        max-height: 600px;
        overflow-y: auto;
    }
    
    div[data-testid="stSidebar"] { background-color: #0D0D18; }
    .stSelectbox label, .stTextArea label, .stRadio label { 
        font-family: 'JetBrains Mono', monospace !important; 
        color: #888 !important;
        font-size: 11px !important;
        letter-spacing: 2px !important;
    }
</style>
""", unsafe_allow_html=True)


# ── Header ──────────────────────────────────────────────────
st.markdown('<div class="main-header">TAOP INC. // AGENT WORKFORCE</div>', unsafe_allow_html=True)
st.markdown('<div class="main-title">WAR ROOM</div>', unsafe_allow_html=True)


# ── Sidebar: Agent Roster ───────────────────────────────────
with st.sidebar:
    st.markdown("### 🤖 AGENT ROSTER")
    st.markdown("---")
    
    agents = list_agents()
    for a in agents:
        agent_data = get_agent(a["name"].lower())
        col1, col2 = st.columns([1, 3])
        with col1:
            st.markdown(f"### {a['emoji']}")
        with col2:
            st.markdown(f"**{a['name']}** `{a['badge']}`")
            st.caption(f"{a['title']} · {a['engine'].upper()}")
        st.markdown("---")
    
    st.markdown("### 📊 STATS")
    stats = get_stats()
    st.metric("Total Tasks", stats["total_tasks"])
    st.metric("Total Tokens", f"{stats['total_tokens']:,}")
    
    if stats["by_status"]:
        for status, count in stats["by_status"].items():
            emoji = {"done": "✅", "working": "⏳", "queued": "📋", "failed": "❌"}.get(status, "•")
            st.caption(f"{emoji} {status.upper()}: {count}")


# ── Main: Dispatch Panel ────────────────────────────────────
tab_dispatch, tab_queue, tab_output = st.tabs(["⚡ DISPATCH", "📋 QUEUE", "🔍 TASK OUTPUT"])

with tab_dispatch:
    st.markdown("#### Assign a task to an agent")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        agent_options = ["🎯 AUTO (best match)"] + [f"{a['emoji']} {a['name']} — {a['title']}" for a in agents]
        selected_agent = st.selectbox("AGENT", agent_options, label_visibility="visible")
    
    with col2:
        priority = st.selectbox("PRIORITY", ["P0 — Do NOW", "P1 — Do today", "P2 — This week", "P3 — Backlog"])
    
    task_input = st.text_area(
        "TASK DESCRIPTION",
        placeholder="What do you need done? Be specific. The agent will execute immediately.",
        height=120,
    )
    
    context_input = st.text_area(
        "ADDITIONAL CONTEXT (optional)",
        placeholder="Paste code, data, URLs, or background info here...",
        height=80,
    )
    
    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 2])
    
    with col_btn1:
        send_single = st.button("⚡ DISPATCH", type="primary", use_container_width=True)
    with col_btn2:
        send_team = st.button("📡 TEAM BROADCAST", use_container_width=True)
    
    if send_single and task_input:
        # Parse agent selection
        if "AUTO" in selected_agent:
            agent_key = "auto"
        else:
            # Extract agent name from selection
            agent_name = selected_agent.split(" ")[1].lower()
            agent_key = agent_name
        
        pri = priority.split(" ")[0]
        
        with st.spinner(f"🔄 Agent working..."):
            result = dispatch(agent_key, task_input, priority=pri, context=context_input)
        
        if result["status"] == "done":
            st.success(f"✅ Task #{result['task_id']} — {result['agent']} — DONE ({result['tokens_used']:,} tokens)")
            st.markdown(f'<div class="output-box">{result["output"]}</div>', unsafe_allow_html=True)
        else:
            st.error(f"❌ Task #{result['task_id']} — FAILED: {result.get('error', 'Unknown')}")
    
    if send_team and task_input:
        pri = priority.split(" ")[0]
        
        with st.spinner("📡 Broadcasting to Aria, Max, Gemma..."):
            results = dispatch_to_team(task_input, priority=pri, context=context_input)
        
        for r in results:
            agent = get_agent(r.get("agent_key", ""))
            emoji = agent["emoji"] if agent else "•"
            if r["status"] == "done":
                with st.expander(f"✅ {emoji} {r['agent']} — Task #{r['task_id']} ({r['tokens_used']:,} tokens)", expanded=False):
                    st.markdown(f'<div class="output-box">{r["output"]}</div>', unsafe_allow_html=True)
            else:
                st.error(f"❌ {emoji} {r['agent']} — FAILED: {r.get('error', '')}")


with tab_queue:
    st.markdown("#### Recent Tasks")
    
    # Filter options
    col1, col2 = st.columns([1, 1])
    with col1:
        filter_status = st.selectbox("Filter by status", ["All", "queued", "working", "done", "failed"])
    with col2:
        filter_agent = st.selectbox("Filter by agent", ["All"] + [a["name"].lower() for a in agents])
    
    status_filter = None if filter_status == "All" else filter_status
    agent_filter = None if filter_agent == "All" else filter_agent
    
    tasks = get_queue(status=status_filter, agent=agent_filter, limit=30)
    
    if not tasks:
        st.info("No tasks found. Dispatch something!")
    else:
        for t in tasks:
            a = get_agent(t["agent"])
            emoji = a["emoji"] if a else "•"
            color = a["color"] if a else "#888"
            status_emoji = {"done": "✅", "working": "⏳", "queued": "📋", "failed": "❌"}.get(t["status"], "•")
            
            col1, col2, col3, col4, col5 = st.columns([0.5, 1, 1, 0.5, 3])
            with col1:
                st.caption(f"#{t['id']}")
            with col2:
                st.markdown(f"{emoji} **{t['agent'].upper()}**")
            with col3:
                st.caption(f"{status_emoji} {t['status'].upper()}")
            with col4:
                st.caption(t["priority"])
            with col5:
                st.caption(t["title"][:60])
    
    if st.button("🔄 Refresh Queue"):
        st.rerun()


with tab_output:
    st.markdown("#### View Task Output")
    
    task_id_input = st.number_input("Task ID", min_value=1, step=1, value=1)
    
    if st.button("🔍 Load Task"):
        t = get_task(int(task_id_input))
        if t:
            a = get_agent(t["agent"])
            emoji = a["emoji"] if a else "•"
            status_emoji = {"done": "✅", "working": "⏳", "queued": "📋", "failed": "❌"}.get(t["status"], "•")
            
            st.markdown(f"### {status_emoji} Task #{t['id']} — {emoji} {t['agent'].upper()}")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Status", t["status"].upper())
            with col2:
                st.metric("Priority", t["priority"])
            with col3:
                st.metric("Tokens", f"{t['tokens_used']:,}" if t["tokens_used"] else "—")
            
            st.markdown("**Task:**")
            st.info(t["description"])
            
            if t["output"]:
                st.markdown("**Output:**")
                st.markdown(f'<div class="output-box">{t["output"]}</div>', unsafe_allow_html=True)
            
            if t["error"]:
                st.error(f"Error: {t['error']}")
            
            st.caption(f"Created: {t['created_at']} | Completed: {t['completed_at'] or '—'}")
        else:
            st.warning(f"Task #{task_id_input} not found.")
