import { marked } from "marked";
import DOMPurify from "dompurify";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-shell";
import { openPath } from "@tauri-apps/plugin-opener";
import { appDataDir } from "@tauri-apps/api/path";
const cfgDebugMode = document.getElementById("cfgDebugMode");
const openLogDirBtn = document.getElementById("openLogDirBtn");
const API_BASE = "http://127.0.0.1:8000";

const chatList = document.getElementById("chatList");
const sessionList = document.getElementById("sessionList");
const newSessionBtn = document.getElementById("newSessionBtn");
const inputBar = document.querySelector(".input-bar");
const inputArea = document.getElementById("inputArea");
const sendBtn = document.getElementById("sendBtn");
const statusLabel = document.getElementById("statusLabel");
const settingsBtn = document.getElementById("settingsBtn");
const appTitle = document.querySelector(".topbar .title");
const LS_API_KEY = "mw_assistant_api_key";
const settingsModal = document.getElementById("settingsModal");
const settingsModalContent = settingsModal?.querySelector(".modal-content");
const settingsSave = document.getElementById("settingsSave");
const settingsCancel = document.getElementById("settingsCancel");
const restartNotice = document.getElementById("restartNotice");
const restartAppBtn = document.getElementById("restartAppBtn");
const LS_CHAT_HISTORY = "mw_assistant_chat_history_v1";
const LS_ACTIVE_SESSION = "mw_assistant_active_session_v1";

const apiKeyNotice = document.getElementById("apiKeyNotice");
const apiKeyNoticeLink = document.getElementById("apiKeyNoticeLink");

const cfgApiKey = document.getElementById("cfgApiKey");
const cfgApiBase = document.getElementById("cfgApiBase");
const cfgModel = document.getElementById("cfgModel");
const cfgCacheHit = document.getElementById("cfgCacheHit");
const cfgInputHit = document.getElementById("cfgInputHit");
const cfgInputMiss = document.getElementById("cfgInputMiss");
const cfgOutput = document.getElementById("cfgOutput");
const cfgFontSize = document.getElementById("cfgFontSize");
const MODEL_OPTIONS = ["deepseek-chat", "deepseek-reasoner"];

let sessionId = "default";
let sessions = [];
let draftSession = null;
let nearBottom = true;
let lastLoadedSettingsSnapshot = null;
let settingsModalCloseTimer = null;
let animatedSessionId = null;

function attachLiquidPointerTracking(element) {
  if (!element || element.dataset.liquidPointerBound === "1") return;
  element.dataset.liquidPointerBound = "1";
  const resetOnLeaveOnly = element.classList.contains("input-bar");
  let resetTimer = null;
  element.style.setProperty("--pointer-x", "50%");
  element.style.setProperty("--pointer-y", "50%");
  const resetPointerGlow = () => {
    element.style.setProperty("--pointer-x", "50%");
    element.style.setProperty("--pointer-y", "50%");
  };
  const clearPendingReset = () => {
    if (resetTimer) {
      window.clearTimeout(resetTimer);
      resetTimer = null;
    }
  };
  const scheduleReset = () => {
    clearPendingReset();
    const resetDelay =
      parseInt(getComputedStyle(element).getPropertyValue("--liquid-reset-delay-ms"), 10) || 170;
    // Match the CSS glow fade-out so the highlight disappears before recentering.
    resetTimer = window.setTimeout(() => {
      resetPointerGlow();
      resetTimer = null;
    }, resetDelay);
  };
  element.addEventListener("pointermove", (event) => {
    clearPendingReset();
    element.classList.add("liquid-glow-active");
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = ((event.clientX - rect.left) / rect.width) * 100;
    const y = ((event.clientY - rect.top) / rect.height) * 100;
    element.style.setProperty("--pointer-x", `${x}%`);
    element.style.setProperty("--pointer-y", `${y}%`);
  });
  element.addEventListener("pointerleave", () => {
    element.classList.remove("liquid-glow-active");
    if (resetOnLeaveOnly || !element.matches(":focus-within")) {
      scheduleReset();
    }
  });
  if (!resetOnLeaveOnly) {
    element.addEventListener("focusout", () => {
      window.setTimeout(() => {
        if (!element.matches(":hover") && !element.matches(":focus-within")) {
          scheduleReset();
        }
      }, 0);
    });
  }
}

function showSettingsModal() {
  if (!settingsModal) return;
  if (settingsModalCloseTimer) {
    clearTimeout(settingsModalCloseTimer);
    settingsModalCloseTimer = null;
  }
  settingsModal.classList.remove("hidden", "closing");
}

function finishClosingSettingsModal() {
  settingsModal.classList.remove("closing");
  settingsModal.classList.add("hidden");
  settingsModalCloseTimer = null;
}

function closeSettingsModal() {
  if (!settingsModal || settingsModal.classList.contains("hidden")) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    finishClosingSettingsModal();
    return;
  }
  if (settingsModalCloseTimer) {
    clearTimeout(settingsModalCloseTimer);
  }
  settingsModal.classList.add("closing");
  settingsModalCloseTimer = window.setTimeout(finishClosingSettingsModal, 180);
}

function getSettingsSnapshot() {
  return {
    apiKey: cfgApiKey.value.trim(),
    apiBase: cfgApiBase.value.trim(),
    model: cfgModel.value.trim(),
    cacheHitRate: Number(cfgCacheHit.value),
    inputHitPerMillion: Number(cfgInputHit.value),
    inputMissPerMillion: Number(cfgInputMiss.value),
    outputPerMillion: Number(cfgOutput.value),
    fontSize: Number(cfgFontSize.value),
    debugMode: !!cfgDebugMode.checked
  };
}

function hasSettingsChanged(nextSnapshot) {
  return JSON.stringify(lastLoadedSettingsSnapshot) !== JSON.stringify(nextSnapshot);
}

function setRestartNoticeVisible(visible) {
  restartNotice?.classList.toggle("hidden", !visible);
}

function normalizeModel(model) {
  return MODEL_OPTIONS.includes(model) ? model : MODEL_OPTIONS[0];
}

marked.use({
  renderer: {
    // Prevent raw HTML rendering to reduce XSS risk.
    html: () => ""
  }
});
marked.setOptions({
  mangle: false,
  headerIds: false
});

const SAFE_URI = /^(https?:|mailto:)/i;
const MAX_SESSION_TITLE = 20;
const DEFAULT_APP_TITLE = "Minecraft Wiki 助手";

function shortText(text, maxLen = MAX_SESSION_TITLE) {
  const t = (text || "").replace(/\s+/g, " ").trim();
  if (!t) return "新会话";
  return t.length > maxLen ? `${t.slice(0, maxLen)}...` : t;
}

function createSession(initialTitle = "新会话") {
  const now = Date.now();
  return {
    id: `s_${now}_${Math.random().toString(36).slice(2, 8)}`,
    title: initialTitle,
    createdAt: now,
    updatedAt: now,
    messages: []
  };
}

function saveSessions() {
  try {
    localStorage.setItem(LS_CHAT_HISTORY, JSON.stringify(sessions));
    const hasActiveSavedSession = sessions.some((s) => s.id === sessionId);
    if (hasActiveSavedSession) {
      localStorage.setItem(LS_ACTIVE_SESSION, sessionId);
    } else {
      localStorage.removeItem(LS_ACTIVE_SESSION);
    }
  } catch (_) {}
}

function startDraftSession() {
  draftSession = createSession("新会话");
  sessionId = draftSession.id;
}

function loadSessions() {
  try {
    const raw = localStorage.getItem(LS_CHAT_HISTORY);
    const parsed = raw ? JSON.parse(raw) : [];
    sessions = Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    sessions = [];
  }
  startDraftSession();
}

function getCurrentSession() {
  return draftSession?.id === sessionId
    ? draftSession
    : sessions.find((s) => s.id === sessionId) || null;
}

function getCurrentSessionTitle() {
  const current = getCurrentSession();
  if (!current) {
    return DEFAULT_APP_TITLE;
  }

  const hasMessages = Array.isArray(current.messages) && current.messages.length > 0;
  const title = (current.title || "").trim();
  if (!hasMessages || !title || title === "新会话") {
    return DEFAULT_APP_TITLE;
  }

  return title;
}

function syncAppTitle() {
  const nextTitle = getCurrentSessionTitle();
  document.title = nextTitle;
  if (appTitle) {
    appTitle.textContent = nextTitle;
  }
}

function deleteSession(sessionToDeleteId) {
  const idx = sessions.findIndex((s) => s.id === sessionToDeleteId);
  if (idx === -1) {
    return;
  }

  const deletingActive = sessionId === sessionToDeleteId;
  sessions.splice(idx, 1);

  if (sessions.length === 0) {
    startDraftSession();
  } else if (deletingActive) {
    const fallback = sessions[idx] || sessions[idx - 1] || sessions[0];
    sessionId = fallback.id;
    draftSession = null;
  }

  saveSessions();
  renderSessionList();
  const current = getCurrentSession();
  renderChatMessages(current?.messages || []);
  syncAppTitle();
}

function renderAssistantFinalBubble(bubble, msg) {
  const html = marked.parse(msg.text || "(无回答)");
  const clean = DOMPurify.sanitize(html, {
    ALLOWED_URI_REGEXP: SAFE_URI,
    FORBID_ATTR: [/^on/i, "style"]
  });
  bubble.innerHTML = clean;
  bubble.classList.add("markdown-body");

  const refs = renderReferences(msg.evidences_for_llm || []);
  if (refs) {
    bubble.appendChild(refs);
  }

  if (msg.token_usage || msg.timing_ms) {
    const footer = document.createElement("div");
    footer.className = "footer";
    const timing = msg.timing_ms || {};
    const token = msg.token_usage || {};
    footer.textContent =
      `耗时 ${timing.total || 0}ms | ` +
      `prompt ${Number(token.prompt_tokens || 0).toFixed(2)} tok | ` +
      `completion ${Number(token.completion_tokens || 0).toFixed(2)} tok | ` +
      `期望成本 ¥${Number(token.total_expected || 0).toFixed(6)}`;
    bubble.appendChild(footer);
  }
}

function renderChatMessages(messages = []) {
  chatList.innerHTML = "";
  for (const msg of messages) {
    const row = createMessageRow(msg.role, msg.text || "", msg.status || "normal", false);
    if (msg.role === "assistant" && msg.status === "normal") {
      renderAssistantFinalBubble(row.bubble, msg);
    }
  }
  nearBottom = true;
  scrollToBottomIfNeeded();
}

function renderSessionList() {
  sessionList.innerHTML = "";
  for (const s of sessions) {
    const item = document.createElement("div");
    item.className = "session-item";
    attachLiquidPointerTracking(item);
    if (s.id === sessionId) {
      item.classList.add("active");
    }
    if (s.id === animatedSessionId) {
      item.classList.add("activating");
    }

    const titleBtn = document.createElement("button");
    titleBtn.type = "button";
    titleBtn.className = "session-title";
    titleBtn.textContent = s.title || "新会话";
    titleBtn.title = s.title || "新会话";
    titleBtn.addEventListener("click", () => {
      if (sessionId === s.id) return;
      animatedSessionId = s.id;
      sessionId = s.id;
      renderSessionList();
      animatedSessionId = null;
      renderChatMessages(s.messages || []);
      saveSessions();
      syncAppTitle();
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "session-delete";
    deleteBtn.textContent = "×";
    deleteBtn.title = "删除会话";
    deleteBtn.setAttribute("aria-label", `删除会话：${s.title || "新会话"}`);
    attachLiquidPointerTracking(deleteBtn);
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });

    item.appendChild(titleBtn);
    item.appendChild(deleteBtn);
    sessionList.appendChild(item);
  }
}

function setStatus(text) {
  statusLabel.textContent = text;
  if (text === "Running") {
    statusLabel.classList.add("running");
  } else {
    statusLabel.classList.remove("running");
  }
}
function updateApiKeyNotice() {
  const hasKey = !!getApiKey();
  if (hasKey) {
    apiKeyNotice.classList.add("hidden");
  } else {
    apiKeyNotice.classList.remove("hidden");
  }
}
apiKeyNoticeLink.addEventListener("click", () => {
  openSettings();
  setTimeout(() => cfgApiKey?.focus?.(), 50);
});
function updateNearBottom() {
  const gap = chatList.scrollHeight - (chatList.scrollTop + chatList.clientHeight);
  nearBottom = gap < 48;
}

chatList.addEventListener("scroll", updateNearBottom);
// ====== 修复外部链接点击跳转 ======
chatList.addEventListener("click", async (e) => {
  // 寻找到被点击元素最近的 <a> 标签
  const a = e.target.closest("a");
  
  // 如果点的是链接，并且有 href 属性
  if (a && a.href) {
    e.preventDefault(); // 阻止 WebView 默认的“在应用内跳转”行为
    try {
      // 使用 Tauri 的 shell plugin 调用系统默认浏览器打开外部链接
      await open(a.href);
    } catch (err) {
      console.error("打开链接失败:", err);
      setStatus("Error: 无法打开链接");
    }
  }
});
function scrollToBottomIfNeeded() {
  if (nearBottom) {
    chatList.scrollTop = chatList.scrollHeight;
  }
}

async function fetchJsonWithRetry(url, options = {}, retries = 6, delayMs = 500) {
  let lastErr = null;
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await fetch(url, options);
      const raw = await res.text();

      // ✅ HTTP 错误：不重试（这类重试没意义）
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${raw}`);
      }

      try {
        return JSON.parse(raw);
      } catch (e) {
        const snippet = raw.slice(0, 300);
        throw new Error(`Invalid JSON: ${snippet}`);
      }
    } catch (err) {
      lastErr = err;

      // ✅ 只有 fetch 网络错误才重试
      // HTTP error 也会走这里，但上面已经 throw 了；想更严格可判断 err.message 里是否有 "HTTP "
      if (String(err?.message || "").startsWith("HTTP ")) {
        throw err;
      }

      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw lastErr;
}

function applyEnterAnimation(bubble, role) {
  // Use transform/opacity-only animations to avoid layout reflow.
  const sideClass = role === "user" ? "from-right" : "from-left";
  bubble.classList.add("enter", sideClass);
  bubble.addEventListener(
    "animationend",
    () => {
      bubble.classList.remove("enter", sideClass);
    },
    { once: true }
  );
}

function animateComposerToBubble(text, bubble) {
  if (!inputBar || !bubble || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    bubble.classList.remove("morph-target");
    applyEnterAnimation(bubble, "user");
    return Promise.resolve();
  }

  const startRect = inputBar.getBoundingClientRect();
  const endRect = bubble.getBoundingClientRect();
  if (!startRect.width || !startRect.height || !endRect.width || !endRect.height) {
    bubble.classList.remove("morph-target");
    applyEnterAnimation(bubble, "user");
    return Promise.resolve();
  }

  const ghost = document.createElement("div");
  ghost.className = "composer-morph";
  ghost.textContent = text;
  Object.assign(ghost.style, {
    top: `${startRect.top}px`,
    left: `${startRect.left}px`,
    width: `${startRect.width}px`,
    height: `${startRect.height}px`
  });
  document.body.appendChild(ghost);

  const midTop = startRect.top + (endRect.top - startRect.top) * 0.42;
  const midLeft = startRect.left + (endRect.left - startRect.left) * 0.35;
  const midWidth = startRect.width + (endRect.width - startRect.width) * 0.58;
  const midHeight = Math.max(endRect.height + 8, startRect.height - 2);

  const animation = ghost.animate(
    [
      {
        top: `${startRect.top}px`,
        left: `${startRect.left}px`,
        width: `${startRect.width}px`,
        height: `${startRect.height}px`,
        borderRadius: getComputedStyle(inputBar).borderRadius,
        opacity: 0.98,
        filter: "blur(0px)"
      },
      {
        top: `${midTop}px`,
        left: `${midLeft}px`,
        width: `${midWidth}px`,
        height: `${midHeight}px`,
        borderRadius: "36px",
        opacity: 0.94,
        filter: "blur(0.2px)",
        offset: 0.58
      },
      {
        top: `${endRect.top}px`,
        left: `${endRect.left}px`,
        width: `${endRect.width}px`,
        height: `${endRect.height}px`,
        borderRadius: getComputedStyle(bubble).borderRadius,
        opacity: 1,
        filter: "blur(0px)"
      }
    ],
    {
      duration: 320,
      easing: "cubic-bezier(0.22, 0.82, 0.2, 1)",
      fill: "forwards"
    }
  );

  return animation.finished
    .catch(() => {})
    .finally(() => {
      ghost.remove();
      bubble.classList.remove("morph-target");
      bubble.classList.add("morph-reveal");
      bubble.addEventListener(
        "animationend",
        () => {
          bubble.classList.remove("morph-reveal");
        },
        { once: true }
      );
    });
}

function animateThinkingToFinalBubble(bubble, renderFinal) {
  if (!bubble || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    renderFinal();
    return;
  }

  const first = bubble.getBoundingClientRect();
  renderFinal();
  const last = bubble.getBoundingClientRect();

  if (!first.width || !first.height || !last.width || !last.height) {
    return;
  }

  const deltaX = first.left - last.left;
  const deltaY = first.top - last.top;
  const scaleX = first.width / last.width;
  const scaleY = first.height / last.height;

  bubble.classList.add("assistant-expanding");
  const animation = bubble.animate(
    [
      {
        transformOrigin: "top left",
        transform: `translate3d(${deltaX}px, ${deltaY}px, 0) scale(${scaleX}, ${scaleY})`,
        opacity: 0.82,
        filter: "blur(0.4px)"
      },
      {
        transformOrigin: "top left",
        transform: "translate3d(0, 0, 0) scale(1, 1)",
        opacity: 1,
        filter: "blur(0px)"
      }
    ],
    {
      duration: 280,
      easing: "cubic-bezier(0.2, 0.8, 0.2, 1)",
      fill: "both"
    }
  );

  animation.finished
    .catch(() => {})
    .finally(() => {
      bubble.classList.remove("assistant-expanding");
    });
}

function animateBubbleHeightChange(bubble, fromHeight) {
  if (!bubble || !fromHeight || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return;
  }

  const toHeight = bubble.getBoundingClientRect().height;
  if (!toHeight || Math.abs(toHeight - fromHeight) < 1) {
    return;
  }

  bubble.classList.add("bubble-resizing");
  bubble.style.height = `${fromHeight}px`;
  bubble.style.overflow = "hidden";

  requestAnimationFrame(() => {
    bubble.style.transition = "height 260ms cubic-bezier(0.22, 0.8, 0.22, 1)";
    bubble.style.height = `${toHeight}px`;
  });

  window.setTimeout(() => {
    bubble.classList.remove("bubble-resizing");
    bubble.style.transition = "";
    bubble.style.height = "";
    bubble.style.overflow = "";
  }, 280);
}

async function progressivelyRevealAssistantBubble(bubble, finalText) {
  const targetText = finalText || "(无回答)";
  const currentText = bubble.textContent || "";
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || currentText === targetText) {
    bubble.textContent = targetText;
    scrollToBottomIfNeeded();
    return;
  }

  let index = currentText.length;
  bubble.textContent = currentText;

  while (index < targetText.length) {
    const remaining = targetText.length - index;
    const chunkSize = remaining > 160 ? 8 : remaining > 80 ? 6 : remaining > 24 ? 4 : 2;
    index = Math.min(targetText.length, index + chunkSize);
    bubble.textContent = targetText.slice(0, index);
    scrollToBottomIfNeeded();
    await new Promise((resolve) => window.setTimeout(resolve, 18));
  }
}

function applyShakeOnce(bubble) {
  bubble.classList.add("shake");
  bubble.addEventListener(
    "animationend",
    () => {
      bubble.classList.remove("shake");
    },
    { once: true }
  );
}

const COPY_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <rect x="9" y="9" width="10" height="10" rx="2" ry="2"></rect>
  <rect x="5" y="5" width="10" height="10" rx="2" ry="2"></rect>
</svg>
`;

const CHECK_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <path d="M20 6L9 17l-5-5"></path>
</svg>
`;

const ERROR_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <path d="M6 6l12 12M18 6L6 18"></path>
</svg>
`;

function getCopyTextFromBubble(bubble) {
  const clone = bubble.cloneNode(true);
  clone.querySelectorAll(".refs, .footer, .dots").forEach((node) => node.remove());
  return clone.textContent.trim();
}

async function writeTextToClipboard(text) {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const temp = document.createElement("textarea");
    temp.value = text;
    temp.setAttribute("readonly", "true");
    temp.style.position = "fixed";
    temp.style.left = "-9999px";
    document.body.appendChild(temp);
    temp.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {}
    temp.remove();
    return ok;
  }
}

function createMessageRow(role, text, status, animate = true) {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const content = document.createElement("div");
  content.className = "message-content";

  const bubbleWrap = document.createElement("div");
  bubbleWrap.className = "bubble-wrap";

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role === "user" ? "user" : "assistant"}`;
  attachLiquidPointerTracking(bubble);
  if (status === "error") {
    bubble.classList.add("error");
  }
  if (status === "thinking") {
    bubble.classList.add("thinking");
  }
  bubble.textContent = text;

  if (status === "thinking") {
    bubble.dataset.thinkingText = text;
    const dots = document.createElement("span");
    dots.className = "dots";
    dots.innerHTML = "<span>.</span><span>.</span><span>.</span>";
    bubble.appendChild(dots);
  }

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "copy-btn";
  copyBtn.innerHTML = COPY_ICON;
  copyBtn.setAttribute("aria-label", "复制本条消息");
  copyBtn.title = "复制本条消息";
  copyBtn.addEventListener("click", async () => {
    const copied = await writeTextToClipboard(getCopyTextFromBubble(bubble));
    copyBtn.classList.toggle("copied", copied);
    copyBtn.classList.toggle("failed", !copied);
    copyBtn.innerHTML = copied ? CHECK_ICON : ERROR_ICON;
    copyBtn.title = copied ? "已复制" : "复制失败";
    setTimeout(() => {
      copyBtn.classList.remove("copied", "failed");
      copyBtn.innerHTML = COPY_ICON;
      copyBtn.title = "复制本条消息";
    }, 1200);
  });

  bubbleWrap.appendChild(bubble);
  bubbleWrap.appendChild(copyBtn);
  content.appendChild(bubbleWrap);
  row.appendChild(content);
  chatList.appendChild(row);

  if (animate) {
    // Trigger enter animation after insertion (no DOM rebuild).
    applyEnterAnimation(bubble, role);
  }

  scrollToBottomIfNeeded();

  return { row, bubble };
}

function removeThinking(bubble) {
  if (bubble.classList.contains("thinking")) {
    bubble.classList.remove("thinking");
  }
  const dots = bubble.querySelector(".dots");
  if (dots) {
    dots.remove();
  }
}

function renderReferences(evidences) {
  if (!Array.isArray(evidences) || evidences.length === 0) {
    return null;
  }
  const details = document.createElement("details");
  details.className = "refs";
  details.open = false;

  const summary = document.createElement("summary");
  summary.className = "refs-summary";
  summary.textContent = `参考 / References (${evidences.length})`;
  summary.addEventListener("click", () => {
    const bubble = details.closest(".bubble");
    if (!bubble) return;
    details.dataset.prevBubbleHeight = String(bubble.getBoundingClientRect().height);
  });
  details.appendChild(summary);

  const list = document.createElement("ol");
  list.className = "refs-list";

  evidences.forEach((ev) => {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "refs-heading";

    const titleText = ev?.title || ev?.section_path || ev?.source || "Reference";
    const url = ev?.url || "";

    if (url) {
      const link = document.createElement("a");
      link.textContent = titleText;
      link.setAttribute("href", url);
      link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noreferrer noopener");
      heading.appendChild(link);
    } else {
      const span = document.createElement("span");
      span.textContent = titleText;
      heading.appendChild(span);
    }

    const meta = ev?.section_path || ev?.source || "";
    if (meta && meta !== titleText) {
      const metaSpan = document.createElement("span");
      metaSpan.className = "refs-meta";
      metaSpan.textContent = ` · ${meta}`;
      heading.appendChild(metaSpan);
    }

    item.appendChild(heading);

    const preview = (ev?.text_preview || "").trim();
    if (preview) {
      const previewSpan = document.createElement("div");
      previewSpan.className = "refs-preview";
      const clipped = preview.length > 180 ? `${preview.slice(0, 180)}…` : preview;
      previewSpan.textContent = clipped;
      item.appendChild(previewSpan);
    }

    list.appendChild(item);
  });

  details.appendChild(list);
  details.addEventListener("toggle", () => {
    const bubble = details.closest(".bubble");
    const fromHeight = Number(details.dataset.prevBubbleHeight || 0);
    requestAnimationFrame(() => {
      animateBubbleHeightChange(bubble, fromHeight);
      if (details.open) {
        list.classList.remove("reveal");
        requestAnimationFrame(() => {
          list.classList.add("reveal");
        });
      } else {
        list.classList.remove("reveal");
      }
    });
  });
  return details;
}


function getApiKey() {
  return (localStorage.getItem(LS_API_KEY) || "").trim();
}

function setUiLocked(locked, tip = "") {
  sendBtn.disabled = locked;
  inputArea.disabled = locked;
  if (tip) setStatus(tip);
}

function ensureApiKeyOrPrompt() {
  const apiKey = getApiKey();
  if (!apiKey) {
    setUiLocked(true, "请先在设置中填写 API Key");
    openSettings(); // ✅ 关键：先拉配置并填充默认值
    setTimeout(() => cfgApiKey?.focus?.(), 50);
    return false;
  }
  setUiLocked(false, "Ready");
  return true;
}
async function sendMessage() {
  if (!getApiKey()) {
    setStatus("请先在设置中填写 API Key");
    showSettingsModal();
    setTimeout(() => cfgApiKey?.focus?.(), 50);
    return;
  }
  const text = inputArea.value.trim();
  if (!text) return;

  const outgoingText = text;
  inputArea.value = "";
  autoResizeInput();
  updateNearBottom();

  const activeSession = getCurrentSession();
  if (!activeSession) {
    setStatus("Error");
    return;
  }

  const isDraftSession = draftSession?.id === activeSession.id;
  if (isDraftSession) {
    draftSession = null;
    sessions.unshift(activeSession);
  }

  if (!activeSession.messages || activeSession.messages.length === 0 || activeSession.title === "新会话") {
    activeSession.title = shortText(text);
  }

  const now = Date.now();
  const userMsgId = `m_u_${now}`;
  const assistantMsgId = `m_a_${now}`;

  const userMessage = {
    id: userMsgId,
    role: "user",
    text,
    status: "normal",
    createdAt: now
  };
  const assistantMessage = {
    id: assistantMsgId,
    role: "assistant",
    text: "正在思考",
    status: "thinking",
    createdAt: now + 1
  };

  activeSession.messages.push(userMessage, assistantMessage);
  activeSession.updatedAt = Date.now();
  saveSessions();
  renderSessionList();
  syncAppTitle();

  const userMsg = createMessageRow("user", text, "normal", false);
  userMsg.bubble.classList.add("morph-target");
  const assistantMsg = createMessageRow("assistant", "正在思考", "thinking", false);
  assistantMsg.bubble.classList.add("pending-reveal");
  animateComposerToBubble(outgoingText, userMsg.bubble).finally(() => {
    assistantMsg.bubble.classList.remove("pending-reveal");
    applyEnterAnimation(assistantMsg.bubble, "assistant");
  });

  setStatus("Running");
  sendBtn.disabled = true;
  inputArea.disabled = true;

  let res;
  try {
    const api_key = getApiKey();
    res = await fetchJsonWithRetry(`${API_BASE}/api/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text, api_key })
    });
  } catch (err) {
    assistantMsg.bubble.textContent = `发生错误：${err.message}`;
    assistantMsg.bubble.classList.add("error");
    applyShakeOnce(assistantMsg.bubble);
    assistantMessage.status = "error";
    assistantMessage.text = `发生错误：${err.message}`;
    activeSession.updatedAt = Date.now();
    saveSessions();
    setStatus("Error");
    sendBtn.disabled = false;
    inputArea.disabled = false;
    return;
  }
  const { message_id } = res;
  const requestSessionId = sessionId;
  const streamUrl = `${API_BASE}/api/stream?session_id=${encodeURIComponent(sessionId)}&message_id=${encodeURIComponent(message_id)}`;

  let finished = false;
  let retried = false;
  let es = null;

  const startStream = () => {
    es = new EventSource(streamUrl);

    es.addEventListener("progress", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const nextText = data.text ?? "";
        assistantMessage.text = nextText || assistantMessage.text;
        activeSession.updatedAt = Date.now();
        saveSessions();

        if (sessionId !== requestSessionId) {
          return;
        }
        if (assistantMsg.bubble.classList.contains("thinking")) {
          if (nextText.trim() !== "") {
            assistantMsg.bubble.dataset.thinkingText = nextText;
            assistantMsg.bubble.textContent = nextText;
          }
        } else {
          assistantMsg.bubble.textContent = nextText;
        }
        scrollToBottomIfNeeded();
      } catch (_) {}
    });

    es.addEventListener("final", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        assistantMessage.status = "normal";
        assistantMessage.text = data.answer || "(无回答)";
        assistantMessage.evidences_for_llm = data.evidences_for_llm || [];
        assistantMessage.token_usage = data.token_usage || {};
        assistantMessage.timing_ms = data.timing_ms || {};
        activeSession.updatedAt = Date.now();
        saveSessions();

        if (sessionId === requestSessionId) {
          const streamedText = assistantMsg.bubble.dataset.thinkingText || "";
          animateThinkingToFinalBubble(assistantMsg.bubble, () => {
            removeThinking(assistantMsg.bubble);
            assistantMsg.bubble.textContent = streamedText;
          });
          progressivelyRevealAssistantBubble(assistantMsg.bubble, assistantMessage.text)
            .then(() => {
              renderAssistantFinalBubble(assistantMsg.bubble, assistantMessage);
            });
        }

        finished = true;
        if (es) {
          es.close();
        }
        setStatus("Ready");
        sendBtn.disabled = false;
        inputArea.disabled = false;
        scrollToBottomIfNeeded();
      } catch (_) {}
    });
    es.addEventListener("backend_error", (ev) => {
      try {
        const data = JSON.parse(ev.data || "{}");
        assistantMessage.status = "error";
        assistantMessage.text = `发生错误：${data.error || "未知错误"}`;
        activeSession.updatedAt = Date.now();
        saveSessions();

        if (sessionId === requestSessionId) {
          removeThinking(assistantMsg.bubble);
          assistantMsg.bubble.textContent = `发生错误：${data.error || "未知错误"}`;
          assistantMsg.bubble.classList.add("error");
          applyShakeOnce(assistantMsg.bubble);
        }

        finished = true;
        if (es) es.close();
        setStatus("Error");
        sendBtn.disabled = false;
        inputArea.disabled = false;
      } catch (_) {}
    });
    es.addEventListener("error", () => {
      if (finished) {
        return;
      }
      if (es) {
        es.close();
      }
      if (!retried) {
        retried = true;
        setTimeout(startStream, 500);
        return;
      }
      removeThinking(assistantMsg.bubble);
      assistantMsg.bubble.textContent = "发生错误：后端流中断";
      assistantMsg.bubble.classList.add("error");
      applyShakeOnce(assistantMsg.bubble);
      assistantMessage.status = "error";
      assistantMessage.text = "发生错误：后端流中断";
      activeSession.updatedAt = Date.now();
      saveSessions();
      setStatus("Error");
      sendBtn.disabled = false;
      inputArea.disabled = false;
    });
  };

  startStream();
}

openLogDirBtn?.addEventListener("click", async () => {
  try {
    const base = await appDataDir();              // 例如 C:/Users/.../Roaming/com.minecraft.wiki.assistant/
    const logDir = `${base}/logs`;                 // 直接拼 logs（不要改斜杠）
    console.log("appDataDir =", base);
    console.log("logDir =", logDir);

    await openPath(logDir);
  } catch (e) {
    console.error(e);
    setStatus("Error");
  }
});
sendBtn.addEventListener("click", sendMessage);

inputArea.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendMessage();
  }
});

function autoResizeInput() {
  inputArea.style.height = "auto";
  const styles = getComputedStyle(inputArea);
  const lineHeight = parseFloat(styles.lineHeight);
  const maxHeight = lineHeight * 6 + 16;
  inputArea.style.height = Math.min(inputArea.scrollHeight, maxHeight) + "px";
  updateInputBarLayout(lineHeight);
}

inputArea.addEventListener("input", autoResizeInput);
inputArea.addEventListener("focus", () => updateInputBarLayout());
inputArea.addEventListener("blur", () => updateInputBarLayout());

function updateInputBarLayout(lineHeight = null) {
  if (!inputBar || !inputArea) return;
  const resolvedLineHeight =
    lineHeight ?? parseFloat(getComputedStyle(inputArea).lineHeight);
  const lines = Math.max(1, Math.round(inputArea.scrollHeight / resolvedLineHeight));
  const contentLength = inputArea.value.trim().length;
  const hasContent = contentLength > 0;
  const isFocused = document.activeElement === inputArea;
  const viewportWidth = window.innerWidth || 1280;
  const maxWidth = Math.min(860, Math.max(520, viewportWidth - 72));
  const minWidth = viewportWidth < 720 ? viewportWidth - 32 : 440;
  const targetWidth = Math.min(
    maxWidth,
    Math.max(
      minWidth,
      430 +
        Math.min(contentLength, 160) * 1.35 +
        Math.min(lines - 1, 5) * 54 +
        (isFocused ? 44 : 0)
    )
  );

  inputBar.style.setProperty("--composer-width", `${targetWidth}px`);
  inputBar.classList.toggle("expanded", hasContent || isFocused || lines > 1);
}

async function openSettings() {
  try {
    const cfg = await fetchJsonWithRetry(`${API_BASE}/api/config`, {}, 8, 500);

    // ✅ api_key：优先本地缓存，其次用后端返回（如果后端未来支持 env key）
    const savedKey = localStorage.getItem(LS_API_KEY) || "";
    cfgApiKey.value = savedKey;
    cfgDebugMode.checked = !!cfg.debug_mode;
    cfgApiBase.value = cfg.api_base || "";
    cfgModel.value = normalizeModel(cfg.model || "");
    cfgCacheHit.value = cfg.cache_hit_rate ?? 0.07;
    cfgInputHit.value = cfg.input_hit_per_million ?? 0.2;
    cfgInputMiss.value = cfg.input_miss_per_million ?? 2.0;
    cfgOutput.value = cfg.output_per_million ?? 3.0;
    cfgFontSize.value = cfg.font_size ?? 14;
    lastLoadedSettingsSnapshot = getSettingsSnapshot();

    showSettingsModal();
  } catch (err) {
    setStatus("Error");
  }
}
settingsBtn.addEventListener("click", openSettings);

settingsCancel.addEventListener("click", () => {
  closeSettingsModal();
});

settingsModal?.addEventListener("click", (event) => {
  if (event.target === settingsModal) {
    closeSettingsModal();
  }
});

settingsModalContent?.addEventListener("click", (event) => {
  event.stopPropagation();
});

settingsSave.addEventListener("click", async () => {
  // ✅ 1) 保存 api_key 到 localStorage（不发给后端，也不落盘）
  const apiKey = cfgApiKey.value.trim();
  if (apiKey) localStorage.setItem(LS_API_KEY, apiKey);
  else localStorage.removeItem(LS_API_KEY);
  updateApiKeyNotice();

  // ✅ 2) 其余配置照旧发给后端
  const updated = {
    api_base: cfgApiBase.value.trim(),
    model: normalizeModel(cfgModel.value.trim()),
    cache_hit_rate: Number(cfgCacheHit.value),
    input_hit_per_million: Number(cfgInputHit.value),
    input_miss_per_million: Number(cfgInputMiss.value),
    output_per_million: Number(cfgOutput.value),
    font_size: Number(cfgFontSize.value),
    debug_mode: !!cfgDebugMode.checked,
  };
  const nextSnapshot = getSettingsSnapshot();
  const settingsChanged = hasSettingsChanged(nextSnapshot);

  try {
    await fetchJsonWithRetry(
      `${API_BASE}/api/config`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updated)
      },
      6,
      500
    );

    document.documentElement.style.setProperty("--font-size", `${updated.font_size}px`);
    lastLoadedSettingsSnapshot = nextSnapshot;
    closeSettingsModal();
    setRestartNoticeVisible(settingsChanged);
    setStatus(settingsChanged ? "设置已保存，重启后生效" : "Ready");
  } catch (err) {
    setStatus("Error");
  }
});

restartAppBtn?.addEventListener("click", async () => {
  restartAppBtn.disabled = true;
  try {
    await invoke("restart_app");
  } catch (_) {
    restartAppBtn.disabled = false;
    setStatus("Error: 重启失败");
  }
});

newSessionBtn?.addEventListener("click", () => {
  startDraftSession();
  renderSessionList();
  renderChatMessages([]);
  syncAppTitle();
  autoResizeInput();
});

window.addEventListener("resize", () => updateInputBarLayout());

document.documentElement.style.setProperty("--font-size", "14px");
async function waitBackendReady() {
  for (let i = 0; i < 20; i++) {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (res.ok) {
        setStatus("Ready");
        return;
      }
    } catch (_) {}

    setStatus("Starting backend...");
    await new Promise(r => setTimeout(r, 500));
  }

  setStatus("Backend not responding");
}

window.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".sidebar, .main").forEach(attachLiquidPointerTracking);
  document.querySelectorAll(".btn, .input-bar").forEach(attachLiquidPointerTracking);
  document
    .querySelectorAll(".modal-content, .form input, .form select")
    .forEach(attachLiquidPointerTracking);
  loadSessions();
  renderSessionList();
  const current = getCurrentSession();
  renderChatMessages(current?.messages || []);
  syncAppTitle();
  autoResizeInput();

  waitBackendReady();
  updateApiKeyNotice();   // ✅ 只显示提示，不弹窗
});
