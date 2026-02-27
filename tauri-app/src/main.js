import { marked } from "marked";
import DOMPurify from "dompurify";
import { open } from "@tauri-apps/plugin-shell";
import { openPath } from "@tauri-apps/plugin-opener";
import { appDataDir } from "@tauri-apps/api/path";
const cfgDebugMode = document.getElementById("cfgDebugMode");
const openLogDirBtn = document.getElementById("openLogDirBtn");
const API_BASE = "http://127.0.0.1:8000";

const chatList = document.getElementById("chatList");
const inputArea = document.getElementById("inputArea");
const sendBtn = document.getElementById("sendBtn");
const statusLabel = document.getElementById("statusLabel");
const settingsBtn = document.getElementById("settingsBtn");
const LS_API_KEY = "mw_assistant_api_key";
const settingsModal = document.getElementById("settingsModal");
const settingsSave = document.getElementById("settingsSave");
const settingsCancel = document.getElementById("settingsCancel");

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

let sessionId = "default";
let nearBottom = true;

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

function createMessageRow(role, text, status) {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const content = document.createElement("div");
  content.className = "message-content";

  const bubbleWrap = document.createElement("div");
  bubbleWrap.className = "bubble-wrap";

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role === "user" ? "user" : "assistant"}`;
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

  // Trigger enter animation after insertion (no DOM rebuild).
  applyEnterAnimation(bubble, role);

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
  settingsModal.classList.remove("hidden");
  setTimeout(() => cfgApiKey?.focus?.(), 50);
  return;
}
  const text = inputArea.value.trim();
  if (!text) return;
  


  inputArea.value = "";
  autoResizeInput();
  updateNearBottom();

  createMessageRow("user", text, "normal");
  const assistantMsg = createMessageRow("assistant", "正在思考", "thinking");

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
    setStatus("Error");
    sendBtn.disabled = false;
    inputArea.disabled = false;
    return;
  }
  const { message_id } = res;
  const streamUrl = `${API_BASE}/api/stream?session_id=${encodeURIComponent(sessionId)}&message_id=${encodeURIComponent(message_id)}`;

  let finished = false;
  let retried = false;
  let es = null;
  let clearedThinking = false; // Avoid repeated DOM removal to prevent flicker.

  const startStream = () => {
    es = new EventSource(streamUrl);

    es.addEventListener("progress", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const nextText = data.text ?? "";
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
        removeThinking(assistantMsg.bubble);
        clearedThinking = true;
        const html = marked.parse(data.answer || "(无回答)");
        const clean = DOMPurify.sanitize(html, {
          ALLOWED_URI_REGEXP: SAFE_URI,
          FORBID_ATTR: [/^on/i, "style"]
        });
        assistantMsg.bubble.innerHTML = clean;
        assistantMsg.bubble.classList.add("markdown-body");

        const refs = renderReferences(data.evidences_for_llm || []);
        if (refs) {
          assistantMsg.bubble.appendChild(refs);
        }

        if (data.token_usage || data.timing_ms) {
          const footer = document.createElement("div");
          footer.className = "footer";
          const timing = data.timing_ms || {};
          const token = data.token_usage || {};
          footer.textContent =
            `耗时 ${timing.total || 0}ms | ` +
            `prompt ${Number(token.prompt_tokens || 0).toFixed(2)} tok | ` +
            `completion ${Number(token.completion_tokens || 0).toFixed(2)} tok | ` +
            `期望成本 ¥${Number(token.total_expected || 0).toFixed(6)}`;
          assistantMsg.bubble.appendChild(footer);
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
        removeThinking(assistantMsg.bubble);
        assistantMsg.bubble.textContent = `发生错误：${data.error || "未知错误"}`;
        assistantMsg.bubble.classList.add("error");
        applyShakeOnce(assistantMsg.bubble);

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
      clearedThinking = true;
      assistantMsg.bubble.textContent = "发生错误：后端流中断";
      assistantMsg.bubble.classList.add("error");
      applyShakeOnce(assistantMsg.bubble);
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
  const lineHeight = parseFloat(getComputedStyle(inputArea).lineHeight);
  const maxHeight = lineHeight * 6 + 16;
  inputArea.style.height = Math.min(inputArea.scrollHeight, maxHeight) + "px";
}

inputArea.addEventListener("input", autoResizeInput);

async function openSettings() {
  try {
    const cfg = await fetchJsonWithRetry(`${API_BASE}/api/config`, {}, 8, 500);

    // ✅ api_key：优先本地缓存，其次用后端返回（如果后端未来支持 env key）
    const savedKey = localStorage.getItem(LS_API_KEY) || "";
    cfgApiKey.value = savedKey;
    cfgDebugMode.checked = !!cfg.debug_mode;
    cfgApiBase.value = cfg.api_base || "";
    cfgModel.value = cfg.model || "";
    cfgCacheHit.value = cfg.cache_hit_rate ?? 0.07;
    cfgInputHit.value = cfg.input_hit_per_million ?? 0.2;
    cfgInputMiss.value = cfg.input_miss_per_million ?? 2.0;
    cfgOutput.value = cfg.output_per_million ?? 3.0;
    cfgFontSize.value = cfg.font_size ?? 14;

    settingsModal.classList.remove("hidden");
  } catch (err) {
    setStatus("Error");
  }
}
settingsBtn.addEventListener("click", openSettings);

settingsCancel.addEventListener("click", () => {
  settingsModal.classList.add("hidden");
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
    model: cfgModel.value.trim(),
    cache_hit_rate: Number(cfgCacheHit.value),
    input_hit_per_million: Number(cfgInputHit.value),
    input_miss_per_million: Number(cfgInputMiss.value),
    output_per_million: Number(cfgOutput.value),
    font_size: Number(cfgFontSize.value),
    debug_mode: !!cfgDebugMode.checked,
  };

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
    settingsModal.classList.add("hidden");
    setStatus("Ready");
  } catch (err) {
    setStatus("Error");
  }
});

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
  waitBackendReady();
  updateApiKeyNotice();   // ✅ 只显示提示，不弹窗
});
