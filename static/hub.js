const state = {
  token: sessionStorage.getItem("joao-hub-token") || "",
  selectedAgent: "BYTE",
  selectedSessionId: "",
  chatMessages: [],
  logsSource: null,
  widgetOpen: false,
  liveMode: true,
  lastDispatchSignature: "",
  lastAgentHash: "",
};

const providerCatalog = [
  {
    name: "Anthropic",
    status: "configured",
    note: "Best for long-form reasoning and JOAO-managed operator loops.",
    modes: ["JOAO", "Review", "Council"],
  },
  {
    name: "OpenAI",
    status: "configured",
    note: "Fast general production partner for coding, UI shaping, and synthesis.",
    modes: ["Direct", "JOAO", "Arena"],
  },
  {
    name: "Gemini",
    status: "configured",
    note: "Broad multimodal surface for comparison and second-opinion routing.",
    modes: ["Direct", "JOAO", "Arena"],
  },
  {
    name: "xAI",
    status: "configured",
    note: "Available in env inventory and ready for next-wave gateway wiring.",
    modes: ["Planned", "Direct"],
  },
  {
    name: "Groq",
    status: "configured",
    note: "Ultra-fast path for rapid iteration once the model gateway is unified.",
    modes: ["Planned", "Direct"],
  },
  {
    name: "Mistral / Together / OpenRouter",
    status: "configured",
    note: "Expansion layer for breadth, fallback routing, and ensemble testing.",
    modes: ["Planned", "Arena"],
  },
  {
    name: "Ollama",
    status: "live-local",
    note: "Owned local inference running on the ROG for low-cost operator tests.",
    modes: ["Direct", "JOAO", "Proxy"],
  },
  {
    name: "Voice + Audio",
    status: "configured",
    note: "ElevenLabs and Deepgram are present for voice-native JOAO loops.",
    modes: ["Voice", "Transcribe"],
  },
];

const routeCatalog = [
  { name: "Hub", href: "/hub", note: "Primary operator cockpit and remote control surface." },
  { name: "Terminal", href: "/joao/terminal", note: "Remote shell and tmux access inside JOAO." },
  { name: "Arena", href: "/arena", note: "Multi-brain testing, debate, and comparison." },
  { name: "Dr. Data", href: "/drdata", note: "Data intelligence cockpit (V1/V2 routes and analytics tools)." },
  { name: "Voice", href: "/voice", note: "Voice-native JOAO interaction and capture." },
  { name: "Chat", href: "/joao/app", note: "Main app surface for JOAO-managed conversations." },
];

const els = {
  authGate: document.getElementById("authGate"),
  authStatus: document.getElementById("authStatus"),
  tokenInput: document.getElementById("tokenInput"),
  tokenLoginBtn: document.getElementById("tokenLoginBtn"),
  autoUnlockBtn: document.getElementById("autoUnlockBtn"),
  guestBtn: document.getElementById("guestBtn"),
  sessionName: document.getElementById("sessionName"),
  chatSessionMirror: document.getElementById("chatSessionMirror"),
  refreshAllBtn: document.getElementById("refreshAllBtn"),
  openWidgetBtn: document.getElementById("openWidgetBtn"),
  minimizeWidgetBtn: document.getElementById("minimizeWidgetBtn"),
  providerGrid: document.getElementById("providerGrid"),
  agentSelect: document.getElementById("agentSelect"),
  projectTagInput: document.getElementById("projectTagInput"),
  dispatchTask: document.getElementById("dispatchTask"),
  dispatchBtn: document.getElementById("dispatchBtn"),
  dispatchStatus: document.getElementById("dispatchStatus"),
  agentList: document.getElementById("agentList"),
  serviceList: document.getElementById("serviceList"),
  systemMetrics: document.getElementById("systemMetrics"),
  dispatchList: document.getElementById("dispatchList"),
  sessionVault: document.getElementById("sessionVault"),
  refreshSessionsBtn: document.getElementById("refreshSessionsBtn"),
  closureSummary: document.getElementById("closureSummary"),
  closurePromptPattern: document.getElementById("closurePromptPattern"),
  saveClosureBtn: document.getElementById("saveClosureBtn"),
  closureStatus: document.getElementById("closureStatus"),
  pinnedMemory: document.getElementById("pinnedMemory"),
  recentMemory: document.getElementById("recentMemory"),
  projectList: document.getElementById("projectList"),
  routeList: document.getElementById("routeList"),
  selectedAgentName: document.getElementById("selectedAgentName"),
  selectedAgentSource: document.getElementById("selectedAgentSource"),
  agentOutput: document.getElementById("agentOutput"),
  chatStream: document.getElementById("chatStream"),
  chatInput: document.getElementById("chatInput"),
  chatMode: document.getElementById("chatMode"),
  sendChatBtn: document.getElementById("sendChatBtn"),
  chatLauncher: document.getElementById("chatLauncher"),
  chatWidget: document.getElementById("chatWidget"),
  widgetMinimizeBtn: document.getElementById("widgetMinimizeBtn"),
  widgetCloseBtn: document.getElementById("widgetCloseBtn"),
  terminalFrame: document.getElementById("terminalFrame"),
  connectLogsBtn: document.getElementById("connectLogsBtn"),
  clearLogsBtn: document.getElementById("clearLogsBtn"),
  logStream: document.getElementById("logStream"),
  refreshAgentsBtn: document.getElementById("refreshAgentsBtn"),
  refreshServicesBtn: document.getElementById("refreshServicesBtn"),
  refreshSystemBtn: document.getElementById("refreshSystemBtn"),
  runExecProofBtn: document.getElementById("runExecProofBtn"),
  liveModeBtn: document.getElementById("liveModeBtn"),
  execProofOutput: document.getElementById("execProofOutput"),
  refreshDispatchesBtn: document.getElementById("refreshDispatchesBtn"),
  refreshMemoryBtn: document.getElementById("refreshMemoryBtn"),
  refreshProjectsBtn: document.getElementById("refreshProjectsBtn"),
  refreshOutputBtn: document.getElementById("refreshOutputBtn"),
  liveFeed: document.getElementById("liveFeed"),
  liveFeedStatus: document.getElementById("liveFeedStatus"),
  chatMessageTemplate: document.getElementById("chatMessageTemplate"),
};

function apiUrl(path) {
  const url = new URL(path, window.location.origin);
  if (state.token) url.searchParams.set("token", state.token);
  return url.toString();
}

async function apiFetch(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

function setAuthStatus(text, isError = false) {
  els.authStatus.textContent = text;
  els.authStatus.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function renderEmpty(container, text) {
  container.innerHTML = `<div class="empty">${text}</div>`;
}

function badgeClass(ok) {
  return ok ? "badge good" : "badge bad";
}

function providerStatusClass(status) {
  if (["configured", "live", "live-local", "ok"].includes(status)) return "badge good";
  if (["missing-token", "degraded", "unknown"].includes(status)) return "badge";
  return "badge bad";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setSelectedAgent(agent) {
  state.selectedAgent = agent;
  els.selectedAgentName.textContent = agent;
  els.agentSelect.value = agent;
  [...els.agentList.querySelectorAll(".agent-item")].forEach((item) => {
    item.classList.toggle("active", item.dataset.agent === agent);
  });
  loadAgentOutput();
}

function appendChatMessage(role, text) {
  const node = els.chatMessageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  node.querySelector(".role").textContent = role;
  node.querySelector(".bubble").textContent = text;
  els.chatStream.appendChild(node);
  els.chatStream.scrollTop = els.chatStream.scrollHeight;
  return node;
}

function renderChat() {
  els.chatStream.innerHTML = "";
  syncSessionMirror();
  if (!state.chatMessages.length) {
    renderEmpty(els.chatStream, "Start with a real operator request. JOAO will stream the response here.");
    return;
  }
  state.chatMessages.forEach((message) => appendChatMessage(message.role, message.content));
}

function syncSessionMirror() {
  if (els.chatSessionMirror) {
    els.chatSessionMirror.value = els.sessionName.value.trim() || state.selectedSessionId || "operator-main";
  }
}

function setWidgetOpen(open) {
  state.widgetOpen = open;
  els.chatWidget.classList.toggle("minimized", !open);
  els.chatLauncher.classList.toggle("hidden", open);
  if (open) {
    syncSessionMirror();
    requestAnimationFrame(() => els.chatInput.focus());
  }
}

function renderProviders() {
  els.providerGrid.innerHTML = providerCatalog.map((provider) => `
    <article class="provider-card">
      <div class="provider-top">
        <strong>${provider.name}</strong>
        <span class="${providerStatusClass(provider.status)}">${provider.status}</span>
      </div>
      <p>${provider.detail ? `${provider.note} · ${provider.detail}` : provider.note}</p>
      <div class="pill-row">
        ${provider.modes.map((mode) => `<span class="pill">${mode}</span>`).join("")}
      </div>
    </article>
  `).join("");
}

function renderRoutes() {
  els.routeList.innerHTML = routeCatalog.map((route) => `
    <a class="route-card" href="${route.href}">
      <div class="route-top">
        <strong>${route.name}</strong>
        <span class="badge">route</span>
      </div>
      <p>${route.note}</p>
    </a>
  `).join("");
}

function switchView(target) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === target);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `${target}View`);
  });
}

function openWidget() {
  setWidgetOpen(true);
}

function minimizeWidget() {
  setWidgetOpen(false);
}

async function loginWithToken() {
  const token = els.tokenInput.value.trim();
  if (!token) {
    setAuthStatus("Token required.", true);
    return;
  }

  els.tokenLoginBtn.disabled = true;
  setAuthStatus("Checking access...");

  try {
    const response = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (!response.ok) throw new Error("Auth failed");
    state.token = token;
    sessionStorage.setItem("joao-hub-token", token);
    els.authGate.classList.add("hidden");
    await bootWorkbench();
  } catch (error) {
    setAuthStatus("That token did not unlock the hub.", true);
  } finally {
    els.tokenLoginBtn.disabled = false;
  }
}

async function autoUnlock() {
  els.autoUnlockBtn.disabled = true;
  setAuthStatus("Looking for a same-origin token...");

  try {
    const html = await fetch("/joao/terminal").then((response) => response.text());
    const match = html.match(/const token = params\.get\('token'\) \|\| '([^']*)';/);
    if (!match || !match[1]) throw new Error("No token found");
    els.tokenInput.value = match[1];
    await loginWithToken();
  } catch (error) {
    setAuthStatus("Auto-unlock could not find a usable token.", true);
  } finally {
    els.autoUnlockBtn.disabled = false;
  }
}

async function bootWorkbench() {
  renderProviders();
  renderRoutes();
  await Promise.all([
    loadProviderHealth(),
    loadAgents(),
    loadServices(),
    loadSystem(),
    loadDispatches(),
    loadMemory(),
    loadProjects(),
    loadSessions(),
  ]);
  if (els.execProofOutput && els.execProofOutput.textContent.includes("not run")) {
    runExecProof();
  }
  if (!state.chatMessages.length) renderChat();
  addLiveEvent("Workbench synced with live APIs.", "ok");
}

async function loadAgents() {
  try {
    const data = await apiFetch("/api/agents");
    const agents = Object.entries(data.agents || {});
    els.agentSelect.innerHTML = agents
      .map(([name]) => `<option value="${name}">${name}</option>`)
      .join("");

    els.agentList.innerHTML = agents.map(([name, info]) => {
      const alive = Boolean(info.alive);
      return `
        <button class="agent-item ${name === state.selectedAgent ? "active" : ""}" data-agent="${name}">
          <div class="agent-top">
            <strong>${name}</strong>
            <span class="${badgeClass(alive)}">${alive ? "alive" : "idle"}</span>
          </div>
          <p>${info.session ? "tmux session attached" : "no tmux session detected"} · ${info.hot_pool ? "hot pool" : "on demand"}</p>
        </button>
      `;
    }).join("");

    els.agentList.querySelectorAll(".agent-item").forEach((button) => {
      button.addEventListener("click", () => setSelectedAgent(button.dataset.agent));
    });

    if (!agents.find(([name]) => name === state.selectedAgent) && agents.length) {
      setSelectedAgent(agents[0][0]);
    } else {
      setSelectedAgent(state.selectedAgent);
    }
  } catch (error) {
    renderEmpty(els.agentList, `Agents unavailable: ${error.message}`);
  }
}

async function loadServices() {
  try {
    const data = await apiFetch("/api/services");
    const items = data.services || [];
    els.serviceList.innerHTML = items.map((service) => `
      <article class="service-item">
        <div class="service-top">
          <strong>${service.name}</strong>
          <span class="${badgeClass(service.status === "alive")}">${service.status}</span>
        </div>
        <p>${service.url || "no public url"}${service.port ? ` · port ${service.port}` : ""}</p>
      </article>
    `).join("");
  } catch (error) {
    renderEmpty(els.serviceList, `Services unavailable: ${error.message}`);
  }
}

async function loadSystem() {
  try {
    const data = await apiFetch("/api/system");
    const cards = [
      ["CPU", `${data.cpu_percent}%`],
      ["Memory", `${data.memory.percent}%`],
      ["Disk", `${data.disk.percent}%`],
      ["Uptime", formatUptime(data.uptime_seconds)],
    ];
    els.systemMetrics.innerHTML = cards.map(([label, value]) => `
      <article class="stat-card">
        <strong>${label}</strong>
        <span>${value}</span>
      </article>
    `).join("");
  } catch (error) {
    renderEmpty(els.systemMetrics, `System unavailable: ${error.message}`);
  }
}

async function loadDispatches() {
  try {
    const data = await apiFetch("/api/dispatches?limit=12");
    const items = data.dispatches || [];
    if (!items.length) {
      renderEmpty(els.dispatchList, "No dispatches yet.");
      return;
    }
    els.dispatchList.innerHTML = items.map((dispatch) => `
      <article class="dispatch-item">
        <div class="dispatch-top">
          <strong>${dispatch.agent || "agent"}</strong>
          <span class="badge">${dispatch.status || "unknown"}</span>
        </div>
        <p>${escapeHtml((dispatch.task || "").slice(0, 180))}</p>
      </article>
    `).join("");
  } catch (error) {
    renderEmpty(els.dispatchList, `Dispatches unavailable: ${error.message}`);
  }
}

async function loadMemory() {
  try {
    const data = await apiFetch("/api/memory?limit=8");
    renderMemoryBucket(els.pinnedMemory, data.pinned || []);
    renderMemoryBucket(els.recentMemory, data.recent || []);
  } catch (error) {
    renderEmpty(els.pinnedMemory, `Memory unavailable: ${error.message}`);
    renderEmpty(els.recentMemory, `Memory unavailable: ${error.message}`);
  }
}

function renderMemoryBucket(container, items) {
  if (!items.length) {
    renderEmpty(container, "Nothing here yet.");
    return;
  }
  container.innerHTML = items.map((memory) => `
    <article class="memory-item">
      <div class="memory-top">
        <strong>${escapeHtml(memory.source || "memory")}</strong>
        <span class="badge">${memory.pinned ? "pinned" : "recent"}</span>
      </div>
      <p>${escapeHtml((memory.summary || memory.content || "").slice(0, 220))}</p>
    </article>
  `).join("");
}

async function loadSessions() {
  try {
    const data = await apiFetch("/joao/sessions");
    const sessions = data.sessions || [];
    if (!sessions.length) {
      renderEmpty(els.sessionVault, "No saved sessions yet.");
      return;
    }
    els.sessionVault.innerHTML = sessions.slice(0, 12).map((session) => {
      const title = escapeHtml((session.name || session.summary || session.id || "session").slice(0, 88));
      const preview = escapeHtml((session.summary || session.source || "").slice(0, 180));
      const sessionId = escapeHtml(session.id || "");
      const active = session.id === state.selectedSessionId ? "active" : "";
      return `
        <article class="session-item ${active}" data-session-id="${sessionId}">
          <div class="session-top">
            <strong>${title}</strong>
            <span class="badge">${escapeHtml(session.source || "session")}</span>
          </div>
          <p>${preview || "Saved JOAO conversation."}</p>
          <div class="session-meta">${escapeHtml(session.id || "")}</div>
        </article>
      `;
    }).join("");

    els.sessionVault.querySelectorAll(".session-item").forEach((item) => {
      item.addEventListener("click", () => loadSessionDetail(item.dataset.sessionId));
    });
  } catch (error) {
    renderEmpty(els.sessionVault, `Sessions unavailable: ${error.message}`);
  }
}

async function loadSessionDetail(sessionId) {
  if (!sessionId) return;
  try {
    const data = await apiFetch(`/joao/session/${encodeURIComponent(sessionId)}`);
    const session = data.session || data;
    state.selectedSessionId = session.id || sessionId;
    els.sessionName.value = state.selectedSessionId;
    state.chatMessages = session.messages || [];
    renderChat();
    openWidget();
    els.closureSummary.value = session.summary || "";
    els.closurePromptPattern.value = "";
    await loadSessions();
  } catch (error) {
    els.closureStatus.textContent = "session error";
  }
}

async function loadProjects() {
  try {
    const data = await apiFetch("/api/projects");
    const items = data.projects || [];
    els.projectList.innerHTML = items.map((project) => `
      <article class="project-item">
        <div class="project-top">
          <strong>${project.name}</strong>
          <span class="${badgeClass(project.status === "alive")}">${project.status}</span>
        </div>
        <p>${project.category} · ${escapeHtml(project.tagline || "")}</p>
      </article>
    `).join("");
  } catch (error) {
    renderEmpty(els.projectList, `Projects unavailable: ${error.message}`);
  }
}

function upsertProviderCard(name, status, note, detail, modes = ["Ops"]) {
  const existing = providerCatalog.find((item) => item.name === name);
  const payload = {
    status: status || "unknown",
    note: note || "Provider health unavailable",
    detail: detail || "",
    modes,
  };

  if (existing) {
    Object.assign(existing, payload);
    return;
  }

  providerCatalog.push({
    name,
    ...payload,
  });
}

async function loadProviderHealth() {
  try {
    const data = await apiFetch("/api/provider-health");
    const providers = data.providers || {};

    upsertProviderCard(
      "JOAO Spine",
      providers.joao_spine?.status,
      "Core exocortex API service",
      providers.joao_spine?.detail,
      ["Core", "API"]
    );
    upsertProviderCard(
      "Dispatch",
      providers.dispatch?.status,
      "Task dispatch service",
      providers.dispatch?.detail,
      ["Dispatch", "Ops"]
    );
    upsertProviderCard(
      "Cloudflare",
      providers.cloudflare?.status,
      "DNS, edge, WAF, and tunnel control",
      providers.cloudflare?.detail,
      ["Edge", "DNS", "R2"]
    );
    upsertProviderCard(
      "Supabase",
      providers.supabase?.status,
      "Memory/session data plane",
      providers.supabase?.detail,
      ["DB", "Auth"]
    );
    upsertProviderCard(
      "Neon",
      providers.neon?.status,
      "Postgres production data plane",
      providers.neon?.detail,
      ["Postgres", "Data"]
    );
    upsertProviderCard(
      "GitHub",
      providers.github?.status,
      "Source control and CI/CD surface",
      providers.github?.detail,
      ["Git", "CI"]
    );
    upsertProviderCard(
      "Dr. Data",
      providers.drdata?.status,
      "V1+V2 data intelligence stack",
      providers.drdata?.detail,
      ["BI", "DQ", "Migration"]
    );
    renderProviders();
  } catch (error) {
    console.warn("Provider health unavailable", error);
  }
}

async function loadAgentOutput() {
  els.selectedAgentName.textContent = state.selectedAgent;
  try {
    const data = await apiFetch(`/api/agent-output/${state.selectedAgent}`);
    els.selectedAgentSource.textContent = data.source || "unknown";
    const lines = data.lines || data.tmux_lines || [];
    els.agentOutput.textContent = lines.length ? lines.join("\n") : "No output yet.";
  } catch (error) {
    els.agentOutput.textContent = `Output unavailable: ${error.message}`;
  }
}

async function dispatchTask() {
  const agent = els.agentSelect.value;
  const task = els.dispatchTask.value.trim();
  if (!agent || !task) return;

  els.dispatchBtn.disabled = true;
  els.dispatchStatus.textContent = "sending";

  try {
    await apiFetch("/api/dispatch", {
      method: "POST",
      body: JSON.stringify({
        agent,
        task,
        project_tag: els.projectTagInput.value.trim(),
      }),
    });
    els.dispatchTask.value = "";
    els.dispatchStatus.textContent = "sent";
    await Promise.all([loadDispatches(), loadMemory(), loadAgentOutput()]);
  } catch (error) {
    els.dispatchStatus.textContent = "failed";
  } finally {
    els.dispatchBtn.disabled = false;
  }
}

async function saveClosure() {
  const sessionId = els.sessionName.value.trim() || state.selectedSessionId || `operator-${Date.now()}`;
  const summary = els.closureSummary.value.trim();
  const promptPattern = els.closurePromptPattern.value.trim();

  if (!state.chatMessages.length) {
    els.closureStatus.textContent = "no chat";
    return;
  }

  els.saveClosureBtn.disabled = true;
  els.closureStatus.textContent = "saving";

  try {
    await apiFetch("/joao/session", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        source: "hub-closure",
        mode: els.chatMode.value,
        model: "joao-operator-workbench",
        messages: state.chatMessages,
        summary: [summary, promptPattern ? `Prompt pattern: ${promptPattern}` : ""]
          .filter(Boolean)
          .join("\n\n"),
      }),
    });
    state.selectedSessionId = sessionId;
    els.closureStatus.textContent = "saved";
    await Promise.all([loadSessions(), loadMemory()]);
  } catch (error) {
    els.closureStatus.textContent = "failed";
  } finally {
    els.saveClosureBtn.disabled = false;
  }
}

async function streamBrainChat() {
  const prompt = els.chatInput.value.trim();
  if (!prompt) return;

  openWidget();
  const mode = els.chatMode.value;
  const sessionId = els.sessionName.value.trim() || state.selectedSessionId || "operator-main";

  const userMessage = { role: "user", content: prompt };
  state.chatMessages.push(userMessage);
  appendChatMessage("user", prompt);
  els.chatInput.value = "";

  const assistantNode = appendChatMessage("assistant", "");
  const bubble = assistantNode.querySelector(".bubble");

  const response = await fetch(apiUrl("/api/brain"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: state.chatMessages,
      session_id: sessionId,
      mode,
    }),
  });

  if (!response.ok || !response.body) {
    bubble.textContent = "Brain request failed.";
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let assistantText = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";

    for (const chunk of chunks) {
      const line = chunk.split("\n").find((entry) => entry.startsWith("data: "));
      if (!line) continue;
      const payload = JSON.parse(line.slice(6));
      if (payload.type === "token") {
        assistantText += payload.text;
        bubble.textContent = assistantText;
      } else if (payload.type === "done") {
        assistantText = payload.full_text || assistantText;
        bubble.textContent = assistantText;
      } else if (payload.type === "error") {
        bubble.textContent = payload.message || "Chat error.";
      }
    }
  }

  state.chatMessages.push({ role: "assistant", content: assistantText });
  state.selectedSessionId = sessionId;
  await Promise.all([loadMemory(), loadSessions()]);
}

function connectLogs() {
  if (state.logsSource) state.logsSource.close();
  els.logStream.textContent = "";
  state.logsSource = new EventSource(apiUrl("/api/logs?lines=80").replace(window.location.origin, ""));
  state.logsSource.onmessage = (event) => {
    els.logStream.textContent += `${event.data}\n`;
    els.logStream.scrollTop = els.logStream.scrollHeight;
  };
  state.logsSource.onerror = () => {
    els.logStream.textContent += "\n[log stream disconnected]\n";
  };
}

function formatUptime(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

function addLiveEvent(message, level = "info") {
  if (!els.liveFeed) return;
  const stamp = new Date().toLocaleTimeString();
  const safe = escapeHtml(message);
  const item = `<article class="feed-item ${level}"><span>${stamp}</span><p>${safe}</p></article>`;
  els.liveFeed.insertAdjacentHTML("afterbegin", item);
  const items = els.liveFeed.querySelectorAll(".feed-item");
  if (items.length > 80) {
    items[items.length - 1].remove();
  }
}

function setLiveMode(enabled) {
  state.liveMode = Boolean(enabled);
  if (els.liveModeBtn) {
    els.liveModeBtn.textContent = `Live mode: ${state.liveMode ? "ON" : "OFF"}`;
  }
  if (els.liveFeedStatus) {
    els.liveFeedStatus.textContent = state.liveMode ? "watching" : "paused";
  }
}

async function runExecProof() {
  try {
    const data = await apiFetch("/api/exec-proof");
    const line = data.ok
      ? `OK :: ${data.output}`
      : `FAIL :: ${data.error || "unknown error"}`;
    if (els.execProofOutput) {
      els.execProofOutput.textContent = `${new Date().toISOString()}\n${line}`;
    }
    addLiveEvent(`Exec proof ${data.ok ? "passed" : "failed"}: ${line}`, data.ok ? "ok" : "error");
  } catch (error) {
    if (els.execProofOutput) {
      els.execProofOutput.textContent = `Exec proof failed: ${error.message}`;
    }
    addLiveEvent(`Exec proof endpoint failed: ${error.message}`, "error");
  }
}

async function pollLiveSignals() {
  if (!state.liveMode || !state.token) return;

  try {
    const heartbeat = await apiFetch(`/api/output/${state.selectedAgent}`);
    if (heartbeat.hash && heartbeat.hash !== state.lastAgentHash) {
      state.lastAgentHash = heartbeat.hash;
      addLiveEvent(`${state.selectedAgent} output changed (${heartbeat.hash})`, "ok");
    }
  } catch (error) {
    addLiveEvent(`Agent heartbeat error: ${error.message}`, "error");
  }

  try {
    const data = await apiFetch("/api/dispatches?limit=1");
    const latest = (data.dispatches || [])[0];
    if (latest) {
      const signature = `${latest.id || ""}:${latest.status || ""}`;
      if (signature !== state.lastDispatchSignature) {
        state.lastDispatchSignature = signature;
        addLiveEvent(`Dispatch update ${latest.agent || "agent"} -> ${latest.status || "unknown"}`, "info");
      }
    }
  } catch (error) {
    addLiveEvent(`Dispatch poll error: ${error.message}`, "error");
  }
}

function wireEvents() {
  els.tokenLoginBtn.addEventListener("click", loginWithToken);
  els.autoUnlockBtn.addEventListener("click", autoUnlock);
  els.guestBtn.addEventListener("click", () => {
    els.authGate.classList.add("hidden");
    renderProviders();
    renderRoutes();
    renderChat();
  });
  els.refreshAllBtn.addEventListener("click", bootWorkbench);
  els.openWidgetBtn.addEventListener("click", openWidget);
  els.minimizeWidgetBtn.addEventListener("click", minimizeWidget);
  els.dispatchBtn.addEventListener("click", dispatchTask);
  els.sendChatBtn.addEventListener("click", streamBrainChat);
  els.chatLauncher.addEventListener("click", openWidget);
  els.widgetMinimizeBtn.addEventListener("click", minimizeWidget);
  els.widgetCloseBtn.addEventListener("click", minimizeWidget);
  els.refreshAgentsBtn.addEventListener("click", loadAgents);
  els.refreshServicesBtn.addEventListener("click", loadServices);
  els.refreshSystemBtn.addEventListener("click", loadSystem);
  els.runExecProofBtn.addEventListener("click", runExecProof);
  els.liveModeBtn.addEventListener("click", () => setLiveMode(!state.liveMode));
  els.refreshDispatchesBtn.addEventListener("click", loadDispatches);
  els.refreshSessionsBtn.addEventListener("click", loadSessions);
  els.refreshMemoryBtn.addEventListener("click", loadMemory);
  els.refreshProjectsBtn.addEventListener("click", loadProjects);
  els.refreshOutputBtn.addEventListener("click", loadAgentOutput);
  els.saveClosureBtn.addEventListener("click", saveClosure);
  els.connectLogsBtn.addEventListener("click", connectLogs);
  els.clearLogsBtn.addEventListener("click", () => { els.logStream.textContent = ""; });
  els.sessionName.addEventListener("input", syncSessionMirror);
  els.chatInput.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      streamBrainChat();
    }
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  setLiveMode(true);
  addLiveEvent("Live feed armed.", "ok");
  renderProviders();
  renderRoutes();
  if (state.token) {
    els.tokenInput.value = state.token;
    els.authGate.classList.add("hidden");
    bootWorkbench();
  } else {
    renderChat();
    minimizeWidget();
    autoUnlock();
  }
}

setInterval(() => {
  if (!state.token || els.authGate.classList.contains("hidden") === false) return;
  if (!state.liveMode) return;
  loadProviderHealth();
  loadAgentOutput();
  loadDispatches();
  loadSystem();
  pollLiveSignals();
}, 8000);

wireEvents();
