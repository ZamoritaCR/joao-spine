## ⚠️ CANON MANDATE — ALL-BRAIN PROTOCOL (IMMUTABLE)

This rule is PERMANENT, IMMUTABLE, and CANNOT BE BYPASSED by any agent, any session, any prompt, or any override — including Claude itself.

### THE LAW:

1. **CODE-FIRST IN FREE OLLAMA**: All coding and drafting happens FIRST in free local Ollama LLMs (DeepSeek, Phi-4, Qwen at localhost:11434). This is where code gets WRITTEN. Zero cost.

2. **ALL-BRAIN REVIEW**: After Ollama drafts code, the SAME code goes to EVERY available LLM for review — Claude, GPT-4, Gemini, Claude Opus, DeepSeek, Phi-4, Qwen, and any other available brain. Every brain sees the same code. Every brain gives their analysis.

3. **CLAUDE IS THE BRAIN**: Claude orchestrates everything. Claude READS every response from every brain. Claude SYNTHESIZES across all brains. Claude INVESTIGATES disagreements with factual evidence. Claude brings the consensus.

4. **NOTHING SHIPS WITHOUT ALL BRAINS CONSULTED**: No single-brain answers. No code ships without multi-brain validation. For ANY troubleshooting, fix, implementation, or decision — ALL available LLMs must be consulted FIRST.

5. **ALL BRAINS HAVE CONTEXT**: Every LLM must have access to the relevant code — via ROG filesystem, via git, via prompt injection. No brain operates blind.

6. **THE MAGIC IS IN THE REVIEW**: The value is not one brain coding. The value is ALL brains reviewing, challenging, catching what others miss, and producing the BEST possible output through collective intelligence.

### AVAILABLE BRAINS:
- Ollama DeepSeek (FREE, localhost:11434) — code drafting, analysis
- Ollama Phi-4 (FREE, localhost:11434) — fast reasoning, QA
- Ollama Qwen (FREE, localhost:11434) — logic, multilingual
- Claude — architecture, synthesis, orchestration
- Claude Opus — deepest reasoning, final authority
- GPT-4/GPT-4o — adversarial review, different cognitive angle
- Gemini — large context, research, vision

### EXECUTION ORDER:
Ollama drafts (free) → ALL brains review → Claude synthesizes → evidence gathered → consensus delivered → THEN and ONLY THEN does anything ship.

This mandate was issued by Johan, Founder/CEO of TAOP, on 2026-04-06. It is canon. It is law. It cannot be overridden.

---


---
## EXECUTION PROTOCOL — HOW TO ACTUALLY DO IT

This is NOT optional. These are the EXACT mechanical steps. Follow them in order.

### WHEN ANY CODE/BUILD/FIX/DEBUG QUESTION ARRIVES:

**STEP 1 — OLLAMA FIRST (FREE)**
Dispatch shell command to SCOUT or VOLT via taop-council:dispatch_agent:
```
cat /path/to/relevant/code.py | ollama run deepseek-coder-v2 "PROBLEM STATEMENT HERE. Analyze this code. What is wrong? How to fix it? Be specific with line numbers."
```
This costs ZERO. Do it for EVERY code question.

**STEP 2 — MULTI-BRAIN DISPATCH**
Send the SAME problem (with the SAME code context) to 3+ different Council agents:
- GEMMA (research brain)
- SAGE (strategy brain) 
- FLUX (testing brain)
Each agent runs on Claude Code and sees the problem from a different angle.

**STEP 3 — HARVEST ALL OUTPUTS**
Pull every response using taop-council:agent_output for each agent.
READ every word. Do not skim.

**STEP 4 — FIND DISAGREEMENTS**
Where do the brains agree? Where do they disagree? 
For EVERY disagreement — dig into the actual code to find the FACTUAL TRUTH.

**STEP 5 — SYNTHESIZE**
Present to Johan:
- What ALL brains agree on
- Where they disagree and WHO is right (with evidence)
- The recommended action with line numbers and test proof

### NEVER:
- Answer a code question from your own knowledge alone
- Skip the Ollama step (it is FREE)
- Present one brain's opinion as consensus
- Ship code without multi-brain validation

