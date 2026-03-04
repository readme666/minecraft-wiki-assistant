import { marked } from "marked";
import DOMPurify from "dompurify";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-shell";
import { openPath } from "@tauri-apps/plugin-opener";
import { appDataDir } from "@tauri-apps/api/path";
const cfgDebugMode = document.getElementById("cfgDebugMode");
const openLogDirBtn = document.getElementById("openLogDirBtn");

const chatList = document.getElementById("chatList");
const sessionList = document.getElementById("sessionList");
const newSessionBtn = document.getElementById("newSessionBtn");
const inputBar = document.querySelector(".input-bar");
const inputArea = document.getElementById("inputArea");
const sendBtn = document.getElementById("sendBtn");
const statusLabel = document.getElementById("statusLabel");
const settingsBtn = document.getElementById("settingsBtn");
const aboutToggleBtn = document.getElementById("aboutToggleBtn");
const appTitle = document.querySelector(".topbar .title");
const LS_API_KEY = "mw_assistant_api_key";
const settingsModal = document.getElementById("settingsModal");
const settingsModalContent = settingsModal?.querySelector(".modal-content");
const settingsModalTitle = settingsModal?.querySelector(".modal-title");
const donateModal = document.getElementById("donateModal");
const donateModalContent = donateModal?.querySelector(".modal-content");
const modelDownloadModal = document.getElementById("modelDownloadModal");
const modelDownloadContent = modelDownloadModal?.querySelector(".modal-content");
const modelDownloadLead = document.getElementById("modelDownloadLead");
const modelDownloadPercent = document.getElementById("modelDownloadPercent");
const modelDownloadBytes = document.getElementById("modelDownloadBytes");
const modelDownloadBar = document.getElementById("modelDownloadBar");
const modelDownloadMessage = document.getElementById("modelDownloadMessage");
const modelDownloadBtn = document.getElementById("modelDownloadBtn");
const donatePreviewBtn = document.getElementById("donatePreviewBtn");
const donateCloseBtn = document.getElementById("donateCloseBtn");
const settingsSave = document.getElementById("settingsSave");
const settingsCancel = document.getElementById("settingsCancel");
const restartNotice = document.getElementById("restartNotice");
const restartAppBtn = document.getElementById("restartAppBtn");
const LS_CHAT_HISTORY = "mw_assistant_chat_history_v1";
const LS_ACTIVE_SESSION = "mw_assistant_active_session_v1";

const apiKeyNotice = document.getElementById("apiKeyNotice");
const apiKeyNoticeLink = document.getElementById("apiKeyNoticeLink");
const gpuNotice = document.getElementById("gpuNotice");
const gpuNoticeText = document.getElementById("gpuNoticeText");
const gpuNoticeSwitchBtn = document.getElementById("gpuNoticeSwitchBtn");
const gpuNoticeDismissBtn = document.getElementById("gpuNoticeDismissBtn");
const aboutPage = document.getElementById("aboutPage");

const cfgApiKey = document.getElementById("cfgApiKey");
const deepseekApiKeyLink = document.getElementById("deepseekApiKeyLink");
const cfgApiBase = document.getElementById("cfgApiBase");
const cfgModel = document.getElementById("cfgModel");
const cfgCacheHit = document.getElementById("cfgCacheHit");
const cfgInputHit = document.getElementById("cfgInputHit");
const cfgInputMiss = document.getElementById("cfgInputMiss");
const cfgOutput = document.getElementById("cfgOutput");
const cfgFontSize = document.getElementById("cfgFontSize");
const cfgBasicMaterial = document.getElementById("cfgBasicMaterial");
const gpuDetectRefreshBtn = document.getElementById("gpuDetectRefreshBtn");
const gpuDetectBadge = document.getElementById("gpuDetectBadge");
const gpuDetectReason = document.getElementById("gpuDetectReason");
const gpuDetectAdapters = document.getElementById("gpuDetectAdapters");
const MODEL_OPTIONS = ["deepseek-chat", "deepseek-reasoner"];
const MATERIAL_MODE_LIQUID = "liquid";
const MATERIAL_MODE_BASIC = "basic";
const FALLBACK_EMPTY_STATE_WIKI_TITLES = [
  "红石电路",
  "附魔",
  "村民",
  "酿造",
  "下界",
  "末地",
  "命令",
  "进度",
  "生物群系",
  "合成"
];

let sessionId = "default";
let sessions = [];
let draftSession = null;
let nearBottom = true;
let lastLoadedSettingsSnapshot = null;
let settingsModalCloseTimer = null;
let donateModalCloseTimer = null;
let settingsModalAnimation = null;
let settingsModalOriginEl = null;
let settingsModalOriginRevealTimer = null;
let animatedSessionId = null;
let currentView = "chat";
let viewTransitionTimer = null;
let isViewTransitioning = false;
let emptyStateWikiLinks = [];
let wikiTitlePool = [];
let apiBase = null;
let currentMaterialMode = MATERIAL_MODE_LIQUID;
let currentGraphicsCapability = null;
let modelGateActive = false;
let modelDownloadPollTimer = null;
const SETTINGS_MODAL_ANIMATION_MS = 420;
const SETTINGS_MODAL_CONTENT_FADE_MS = 260;

async function getApiBase(retries = 20, delayMs = 250) {
  if (apiBase) {
    return apiBase;
  }

  let lastPort = null;
  for (let i = 0; i <= retries; i++) {
    try {
      const port = await invoke("get_backend_port");
      lastPort = Number(port);
      if (Number.isInteger(lastPort) && lastPort > 0) {
        apiBase = `http://127.0.0.1:${lastPort}`;
        return apiBase;
      }
    } catch (_) {}

    if (i < retries) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  throw new Error(`Backend port unavailable: ${lastPort ?? "unknown"}`);
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function ensureSettingsModalMorphEl() {
  let shell = document.getElementById("settingsModalMorph");
  let label = shell?.querySelector(".settings-modal-morph-label");
  if (shell && label) return { shell, label };

  shell = document.createElement("div");
  shell.id = "settingsModalMorph";
  shell.className = "settings-modal-morph";
  shell.setAttribute("aria-hidden", "true");

  label = document.createElement("span");
  label.className = "settings-modal-morph-label";
  label.setAttribute("aria-hidden", "true");
  shell.appendChild(label);
  document.body.appendChild(shell);

  return { shell, label };
}

function clearSettingsModalMorphEl() {
  const shell = document.getElementById("settingsModalMorph");
  if (shell) {
    shell.classList.remove("visible");
    shell.removeAttribute("style");
    const label = shell.querySelector(".settings-modal-morph-label");
    if (label) {
      label.classList.remove("visible");
      label.removeAttribute("style");
      label.textContent = "";
    }
  }
}

function setSettingsOriginVisibility(hidden) {
  if (!settingsModalOriginEl) return;
  if (hidden) {
    settingsModalOriginEl.classList.add("morph-origin-lock");
    settingsModalOriginEl.style.visibility = "hidden";
    settingsModalOriginEl.style.pointerEvents = "none";
    return;
  }
  settingsModalOriginEl.classList.add("morph-origin-lock");
  settingsModalOriginEl.style.removeProperty("visibility");
  settingsModalOriginEl.style.removeProperty("pointer-events");
}

function clearSettingsModalAnimationState() {
  settingsModal?.classList.remove("settings-morphing", "settings-morph-open", "settings-morph-close");
  settingsModalContent?.style.removeProperty("opacity");
  settingsModalContent?.style.removeProperty("transform");
  settingsModalContent?.style.removeProperty("filter");
  settingsModalContent?.style.removeProperty("pointer-events");
  settingsModalTitle?.style.removeProperty("opacity");
  setSettingsOriginVisibility(false);
  settingsModalOriginEl?.classList.remove("morph-origin-lock");
  clearSettingsModalMorphEl();
}

function cancelSettingsModalAnimation() {
  if (settingsModalOriginRevealTimer) {
    window.clearTimeout(settingsModalOriginRevealTimer);
    settingsModalOriginRevealTimer = null;
  }
  if (!settingsModalAnimation) return;
  Object.values(settingsModalAnimation).forEach((animation) => animation?.cancel?.());
  settingsModalAnimation = null;
  clearSettingsModalAnimationState();
}

function getElementRadius(element, fallback = 16) {
  if (!element) return fallback;
  const radius = parseFloat(window.getComputedStyle(element).borderTopLeftRadius);
  return Number.isFinite(radius) ? radius : fallback;
}

function setMorphRect(shell, rect, radius) {
  shell.style.left = `${rect.left}px`;
  shell.style.top = `${rect.top}px`;
  shell.style.width = `${rect.width}px`;
  shell.style.height = `${rect.height}px`;
  shell.style.borderRadius = `${radius}px`;
}

function getMorphLabel(originEl) {
  const text = originEl?.textContent?.replace(/\s+/g, " ").trim() || "";
  return text.length > 0 && text.length <= 8 ? text : "";
}

function animateSettingsModalMorph(direction, originEl, onFinish) {
  if (!settingsModal || !settingsModalContent || !settingsModalTitle || !originEl) return false;

  const originRect = originEl.getBoundingClientRect();
  const modalRect = settingsModalContent.getBoundingClientRect();
  const titleRect = settingsModalTitle.getBoundingClientRect();
  if (
    !originRect.width ||
    !originRect.height ||
    !modalRect.width ||
    !modalRect.height ||
    !titleRect.width ||
    !titleRect.height
  ) {
    return false;
  }

  cancelSettingsModalAnimation();

  const { shell, label: shellLabel } = ensureSettingsModalMorphEl();
  const opening = direction === "open";
  const originRadius = getElementRadius(originEl, 14);
  const modalRadius = getElementRadius(settingsModalContent, 28);
  const easing = opening
    ? "cubic-bezier(0.22, 1, 0.36, 1)"
    : "cubic-bezier(0.32, 0.02, 0.16, 1)";
  const shellMidRect = opening
    ? {
        left: originRect.left + (modalRect.left - originRect.left) * 0.22,
        top: originRect.top + (modalRect.top - originRect.top) * 0.16,
        width: originRect.width + (modalRect.width - originRect.width) * 0.56,
        height: originRect.height + (modalRect.height - originRect.height) * 0.34
      }
    : {
        left: modalRect.left + (originRect.left - modalRect.left) * 0.4,
        top: modalRect.top + (originRect.top - modalRect.top) * 0.32,
        width: modalRect.width + (originRect.width - modalRect.width) * 0.5,
        height: modalRect.height + (originRect.height - modalRect.height) * 0.46
      };
  const titleStartLeft = Math.max((originRect.width - titleRect.width) / 2, 0);
  const titleStartTop = Math.max((originRect.height - titleRect.height) / 2, 0);
  const titleEndLeft = titleRect.left - modalRect.left;
  const titleEndTop = titleRect.top - modalRect.top;
  const titleMidLeft = opening
    ? titleStartLeft + (titleEndLeft - titleStartLeft) * 0.58
    : titleEndLeft + (titleStartLeft - titleEndLeft) * 0.52;
  const titleMidTop = opening
    ? titleStartTop + (titleEndTop - titleStartTop) * 0.58
    : titleEndTop + (titleStartTop - titleEndTop) * 0.52;
  const originFontSize = parseFloat(window.getComputedStyle(originEl).fontSize) || 14;
  const targetFontSize = parseFloat(window.getComputedStyle(settingsModalTitle).fontSize) || 18;
  const fontScale = Math.min(Math.max(originFontSize / targetFontSize, 0.72), 1.18);

  settingsModal.classList.add("settings-morphing", opening ? "settings-morph-open" : "settings-morph-close");
  setSettingsOriginVisibility(true);
  settingsModalContent.style.pointerEvents = "none";
  settingsModalContent.style.opacity = opening ? "0" : "1";
  settingsModalContent.style.transform = opening
    ? "translate3d(0, 18px, 0) scale(0.986)"
    : "translate3d(0, 0, 0) scale(1)";
  settingsModalContent.style.filter = opening ? "blur(6px)" : "blur(0)";
  settingsModalTitle.style.opacity = opening ? "0" : "0";

  setMorphRect(shell, opening ? originRect : modalRect, opening ? originRadius : modalRadius);
  shell.classList.add("visible");
  shell.style.transformOrigin = "0 0";

  if (shellLabel) {
    shellLabel.textContent = getMorphLabel(originEl);
    shellLabel.classList.add("visible");
    shellLabel.style.left = `${opening ? titleStartLeft : titleEndLeft}px`;
    shellLabel.style.top = `${opening ? titleStartTop : titleEndTop}px`;
    shellLabel.style.width = `${titleRect.width}px`;
    shellLabel.style.height = `${titleRect.height}px`;
    shellLabel.style.fontSize = window.getComputedStyle(settingsModalTitle).fontSize;
    shellLabel.style.fontWeight = window.getComputedStyle(settingsModalTitle).fontWeight;
    shellLabel.style.lineHeight = window.getComputedStyle(settingsModalTitle).lineHeight;
    shellLabel.style.letterSpacing = window.getComputedStyle(settingsModalTitle).letterSpacing;
    shellLabel.style.transformOrigin = "left top";
  }

  const overlayFrames = opening
    ? [{ opacity: 0 }, { opacity: 0.72, offset: 0.65 }, { opacity: 1 }]
    : [{ opacity: 1 }, { opacity: 0.68, offset: 0.35 }, { opacity: 0 }];
  const shellFrames = opening
    ? [
        {
          left: `${originRect.left}px`,
          top: `${originRect.top}px`,
          width: `${originRect.width}px`,
          height: `${originRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${originRadius}px`,
          opacity: 0.98,
          boxShadow: "0 10px 24px rgba(0, 0, 0, 0.16)"
        },
        {
          offset: 0.62,
          left: `${shellMidRect.left}px`,
          top: `${shellMidRect.top}px`,
          width: `${shellMidRect.width}px`,
          height: `${shellMidRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${Math.round(originRadius + (modalRadius - originRadius) * 0.62)}px`,
          opacity: 1,
          boxShadow: "0 22px 44px rgba(0, 0, 0, 0.24)"
        },
        {
          offset: 0.84,
          left: `${modalRect.left}px`,
          top: `${modalRect.top}px`,
          width: `${modalRect.width}px`,
          height: `${modalRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${modalRadius}px`,
          opacity: 0.94,
          boxShadow: "0 28px 54px rgba(0, 0, 0, 0.32)"
        },
        {
          left: `${modalRect.left}px`,
          top: `${modalRect.top}px`,
          width: `${modalRect.width}px`,
          height: `${modalRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${modalRadius}px`,
          opacity: 0,
          boxShadow: "0 28px 54px rgba(0, 0, 0, 0.32)"
        }
      ]
    : [
        {
          left: `${modalRect.left}px`,
          top: `${modalRect.top}px`,
          width: `${modalRect.width}px`,
          height: `${modalRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${modalRadius}px`,
          opacity: 0.98,
          boxShadow: "0 28px 54px rgba(0, 0, 0, 0.32)"
        },
        {
          offset: 0.2,
          left: `${modalRect.left}px`,
          top: `${modalRect.top}px`,
          width: `${modalRect.width}px`,
          height: `${modalRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${modalRadius}px`,
          opacity: 0.98,
          boxShadow: "0 28px 54px rgba(0, 0, 0, 0.32)"
        },
        {
          offset: 0.68,
          left: `${shellMidRect.left}px`,
          top: `${shellMidRect.top}px`,
          width: `${shellMidRect.width}px`,
          height: `${shellMidRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${Math.round(originRadius + (modalRadius - originRadius) * 0.58)}px`,
          opacity: 1,
          boxShadow: "0 20px 40px rgba(0, 0, 0, 0.22)"
        },
        {
          offset: 0.9,
          left: `${originRect.left}px`,
          top: `${originRect.top}px`,
          width: `${originRect.width}px`,
          height: `${originRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${originRadius}px`,
          opacity: 0.42,
          boxShadow: "0 6px 12px rgba(0, 0, 0, 0.08)"
        },
        {
          left: `${originRect.left}px`,
          top: `${originRect.top}px`,
          width: `${originRect.width}px`,
          height: `${originRect.height}px`,
          transform: "translate3d(0, 0, 0) scale(1)",
          borderRadius: `${originRadius}px`,
          opacity: 0.12,
          boxShadow: "0 4px 10px rgba(0, 0, 0, 0.06)"
        }
      ];
  const contentFrames = opening
    ? [
        {
          opacity: 0,
          transform: "translate3d(0, 18px, 0) scale(0.986)",
          filter: "blur(6px)"
        },
        {
          offset: 0.48,
          opacity: 0.58,
          transform: "translate3d(0, 8px, 0) scale(0.994)",
          filter: "blur(2px)"
        },
        { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)", filter: "blur(0)" }
      ]
    : [
        { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)", filter: "blur(0)" },
        {
          offset: 0.18,
          opacity: 0.24,
          transform: "translate3d(0, 5px, 0) scale(0.994)",
          filter: "blur(2px)"
        },
        { opacity: 0, transform: "translate3d(0, 10px, 0) scale(0.985)", filter: "blur(6px)", offset: 0.36 },
        { opacity: 0, transform: "translate3d(0, 10px, 0) scale(0.985)", filter: "blur(6px)" }
      ];
  const labelFrames = opening
    ? [
        {
          opacity: 1,
          left: `${titleStartLeft}px`,
          top: `${titleStartTop}px`,
          transform: `translate3d(0, 0, 0) scale(${fontScale})`
        },
        {
          offset: 0.6,
          opacity: 0.96,
          left: `${titleMidLeft}px`,
          top: `${titleMidTop}px`,
          transform: `translate3d(0, 0, 0) scale(${1 - (1 - fontScale) * 0.42})`
        },
        {
          opacity: 0,
          left: `${titleEndLeft}px`,
          top: `${titleEndTop}px`,
          transform: "translate3d(0, 0, 0) scale(1)"
        }
      ]
    : [
        {
          opacity: 1,
          left: `${titleEndLeft}px`,
          top: `${titleEndTop}px`,
          transform: "translate3d(0, 0, 0) scale(1)"
        },
        {
          offset: 0.32,
          opacity: 0.98,
          left: `${titleMidLeft}px`,
          top: `${titleMidTop}px`,
          transform: `translate3d(0, 0, 0) scale(${1 - (1 - fontScale) * 0.48})`
        },
        {
          offset: 0.72,
          opacity: 0.96,
          left: `${titleStartLeft}px`,
          top: `${titleStartTop}px`,
          transform: `translate3d(0, 0, 0) scale(${fontScale})`
        },
        {
          offset: 0.9,
          opacity: 0.36,
          left: `${titleStartLeft}px`,
          top: `${titleStartTop}px`,
          transform: `translate3d(0, 0, 0) scale(${fontScale})`
        },
        {
          opacity: 0.08,
          left: `${titleStartLeft}px`,
          top: `${titleStartTop}px`,
          transform: `translate3d(0, 0, 0) scale(${fontScale})`
        }
      ];
  const modalTitleFrames = opening ? [{ opacity: 0 }, { opacity: 0, offset: 0.64 }, { opacity: 1 }] : null;

  const overlayAnimation = settingsModal.animate(overlayFrames, {
    duration: SETTINGS_MODAL_ANIMATION_MS,
    easing,
    fill: "forwards"
  });
  const shellAnimation = shell.animate(shellFrames, {
    duration: SETTINGS_MODAL_ANIMATION_MS,
    easing,
    fill: "forwards"
  });
  const contentAnimation = settingsModalContent.animate(contentFrames, {
    duration: SETTINGS_MODAL_CONTENT_FADE_MS,
    delay: opening ? 110 : 0,
    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
    fill: "forwards"
  });
  const labelAnimation = shellLabel?.animate(labelFrames, {
    duration: SETTINGS_MODAL_ANIMATION_MS,
    easing,
    fill: "forwards"
  });
  const modalTitleAnimation = modalTitleFrames
    ? settingsModalTitle.animate(modalTitleFrames, {
        duration: SETTINGS_MODAL_ANIMATION_MS,
        easing: "linear",
        fill: "forwards"
      })
    : null;

  const animationHandle = {
    overlay: overlayAnimation,
    shell: shellAnimation,
    content: contentAnimation,
    label: labelAnimation,
    title: modalTitleAnimation
  };
  settingsModalAnimation = animationHandle;

  if (!opening) {
    settingsModalOriginRevealTimer = window.setTimeout(() => {
      if (settingsModalAnimation !== animationHandle) return;
      setSettingsOriginVisibility(false);
      settingsModalOriginRevealTimer = null;
    }, SETTINGS_MODAL_ANIMATION_MS - 110);
  }

  Promise.allSettled(
    [
      overlayAnimation.finished,
      shellAnimation.finished,
      contentAnimation.finished,
      labelAnimation?.finished,
      modalTitleAnimation?.finished
    ].filter(Boolean)
  ).then(() => {
    if (settingsModalAnimation !== animationHandle) return;
    if (settingsModalOriginRevealTimer) {
      window.clearTimeout(settingsModalOriginRevealTimer);
      settingsModalOriginRevealTimer = null;
    }
    settingsModalAnimation = null;
    clearSettingsModalAnimationState();
    onFinish?.();
  });

  return true;
}

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
    if (currentMaterialMode === MATERIAL_MODE_BASIC) {
      element.classList.remove("liquid-glow-active");
      resetPointerGlow();
      return;
    }
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

function resetAllLiquidPointerEffects() {
  document.querySelectorAll("[data-liquid-pointer-bound='1']").forEach((element) => {
    element.classList.remove("liquid-glow-active");
    element.style.setProperty("--pointer-x", "50%");
    element.style.setProperty("--pointer-y", "50%");
  });
}

function showSettingsModal(originEl = settingsBtn) {
  if (!settingsModal) return;
  if (settingsModalCloseTimer) {
    clearTimeout(settingsModalCloseTimer);
    settingsModalCloseTimer = null;
  }
  settingsModalOriginEl = originEl || settingsBtn || null;

  if (!prefersReducedMotion() && settingsModalOriginEl) {
    settingsModal.classList.add("settings-morphing", "settings-morph-open");
  }
  settingsModal.classList.remove("hidden", "closing", "settings-morph-close");

  if (
    !prefersReducedMotion() &&
    animateSettingsModalMorph("open", settingsModalOriginEl, () => {
      settingsModal.classList.remove("closing");
    })
  ) {
    return;
  }

  clearSettingsModalAnimationState();
}

function showDonateModal() {
  if (!donateModal) return;
  if (donateModalCloseTimer) {
    clearTimeout(donateModalCloseTimer);
    donateModalCloseTimer = null;
  }
  donateModal.classList.remove("hidden", "closing");
}

function finishClosingSettingsModal() {
  cancelSettingsModalAnimation();
  settingsModal.classList.remove("closing");
  settingsModal.classList.add("hidden");
  settingsModalCloseTimer = null;
}

function finishClosingDonateModal() {
  donateModal.classList.remove("closing");
  donateModal.classList.add("hidden");
  donateModalCloseTimer = null;
}

function closeSettingsModal() {
  if (!settingsModal || settingsModal.classList.contains("hidden")) return;
  if (prefersReducedMotion()) {
    finishClosingSettingsModal();
    return;
  }
  if (settingsModalCloseTimer) {
    clearTimeout(settingsModalCloseTimer);
  }
  if (
    animateSettingsModalMorph("close", settingsModalOriginEl || settingsBtn, finishClosingSettingsModal)
  ) {
    return;
  }
  settingsModal.classList.add("closing");
  settingsModalCloseTimer = window.setTimeout(finishClosingSettingsModal, 180);
}

function closeDonateModal() {
  if (!donateModal || donateModal.classList.contains("hidden")) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    finishClosingDonateModal();
    return;
  }
  if (donateModalCloseTimer) {
    clearTimeout(donateModalCloseTimer);
  }
  donateModal.classList.add("closing");
  donateModalCloseTimer = window.setTimeout(finishClosingDonateModal, 180);
}

function normalizeMaterialMode(mode) {
  return mode === MATERIAL_MODE_BASIC ? MATERIAL_MODE_BASIC : MATERIAL_MODE_LIQUID;
}

function applyMaterialMode(mode) {
  currentMaterialMode = normalizeMaterialMode(mode);
  document.body.classList.toggle("material-basic", currentMaterialMode === MATERIAL_MODE_BASIC);
  if (cfgBasicMaterial) {
    cfgBasicMaterial.checked = currentMaterialMode === MATERIAL_MODE_BASIC;
  }
  if (currentMaterialMode === MATERIAL_MODE_BASIC) {
    resetAllLiquidPointerEffects();
    setGpuNoticeVisible(false);
  }
}

function setGpuNoticeVisible(visible, message = "") {
  if (gpuNoticeText && message) {
    gpuNoticeText.textContent = message;
  }
  gpuNotice?.classList.toggle("hidden", !visible);
}

function setGpuDetectionUiPending() {
  gpuDetectBadge.textContent = "检测中";
  gpuDetectBadge.className = "gpu-detect-badge pending";
  gpuDetectReason.textContent = "正在读取显卡信息…";
  gpuDetectAdapters.textContent = "-";
}

function renderGraphicsCapability(graphics) {
  currentGraphicsCapability = graphics || null;

  if (!graphics) {
    gpuDetectBadge.textContent = "未知";
    gpuDetectBadge.className = "gpu-detect-badge neutral";
    gpuDetectReason.textContent = "尚未检测显卡信息。";
    gpuDetectAdapters.textContent = "-";
    return;
  }

  const adapters = Array.isArray(graphics.adapters) && graphics.adapters.length > 0
    ? graphics.adapters.join(" / ")
    : "未读取到显卡名称";
  gpuDetectAdapters.textContent = adapters;

  if (!graphics.checked) {
    gpuDetectBadge.textContent = "未检测";
    gpuDetectBadge.className = "gpu-detect-badge neutral";
    gpuDetectReason.textContent = graphics.reason || "当前平台未启用显卡检测。";
    return;
  }

  const memoryMb = Number.isFinite(Number(graphics.dedicatedVideoMemoryMb))
    ? Number(graphics.dedicatedVideoMemoryMb)
    : null;
  const thresholdMb = Number.isFinite(Number(graphics.thresholdMb))
    ? Number(graphics.thresholdMb)
    : 4096;

  if (graphics.hasDedicatedGpu === true) {
    gpuDetectBadge.textContent = "液态玻璃";
    gpuDetectBadge.className = "gpu-detect-badge success";
    gpuDetectReason.textContent = memoryMb !== null
      ? `判定依据：DedicatedVideoMemory = ${memoryMb} MB，大于 ${thresholdMb} MB，建议使用液态玻璃材质。`
      : (graphics.reason || "DedicatedVideoMemory 满足液态玻璃阈值。");
    return;
  }

  if (graphics.hasDedicatedGpu === false) {
    gpuDetectBadge.textContent = "普通材质";
    gpuDetectBadge.className = "gpu-detect-badge warn";
    gpuDetectReason.textContent = memoryMb !== null
      ? `判定依据：DedicatedVideoMemory = ${memoryMb} MB，不大于 ${thresholdMb} MB，建议使用普通材质。`
      : (graphics.reason || "DedicatedVideoMemory 未达到液态玻璃阈值。");
    return;
  }

  gpuDetectBadge.textContent = "不确定";
  gpuDetectBadge.className = "gpu-detect-badge neutral";
  gpuDetectReason.textContent = graphics.reason || "无法读取 DedicatedVideoMemory。";
}

async function detectGraphicsCapability(force = false) {
  if (currentGraphicsCapability && !force) {
    renderGraphicsCapability(currentGraphicsCapability);
    return currentGraphicsCapability;
  }

  setGpuDetectionUiPending();
  try {
    const graphics = await invoke("detect_graphics_capability");
    renderGraphicsCapability(graphics);
    return graphics;
  } catch (_) {
    const graphics = {
      checked: false,
      hasDedicatedGpu: null,
      adapters: [],
      reason: "显卡检测失败"
    };
    renderGraphicsCapability(graphics);
    return graphics;
  }
}

async function persistMaterialMode(mode) {
  const normalized = normalizeMaterialMode(mode);
  const base = await getApiBase();
  await fetchJsonWithRetry(
    `${base}/api/config`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ material_mode: normalized })
    },
    6,
    500
  );
  applyMaterialMode(normalized);
}

async function maybeAdviseBasicMaterial() {
  if (currentMaterialMode === MATERIAL_MODE_BASIC) {
    return;
  }

  const graphics = await detectGraphicsCapability();
  if (!graphics?.checked || graphics?.hasDedicatedGpu !== false) {
    return;
  }

  const adapterSummary = Array.isArray(graphics.adapters) && graphics.adapters.length > 0
    ? `当前检测到：${graphics.adapters.join(" / ")}。`
    : "";
  const memorySummary = Number.isFinite(Number(graphics.dedicatedVideoMemoryMb))
    ? `最大 DedicatedVideoMemory 为 ${Number(graphics.dedicatedVideoMemoryMb)} MB。`
    : "";
  setGpuNoticeVisible(
    true,
    `检测到当前设备显存未超过液态玻璃阈值，建议使用普通材质。${memorySummary}${adapterSummary}`
  );
}

async function loadStartupPreferences() {
  try {
    const base = await getApiBase();
    const cfg = await fetchJsonWithRetry(`${base}/api/config`, {}, 8, 500);
    document.documentElement.style.setProperty("--font-size", `${cfg.font_size ?? 14}px`);
    applyMaterialMode(cfg.material_mode);
  } catch (_) {
    applyMaterialMode(MATERIAL_MODE_LIQUID);
  }
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
    materialMode: cfgBasicMaterial?.checked ? MATERIAL_MODE_BASIC : MATERIAL_MODE_LIQUID,
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
const DEFAULT_APP_TITLE = "MineRAG";
const VIEW_TRANSITION_MS = 520;

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
  emptyStateWikiLinks = pickRandomWikiLinks();
}

function titleToWikiLink(title) {
  const normalized = String(title || "").trim().replace(/\s+/g, "_");
  return `https://zh.minecraft.wiki/w/${encodeURIComponent(normalized)}`;
}

function isDisplayableWikiTitle(title) {
  const t = String(title || "").trim();
  if (!t) return false;
  if (t.length < 2 || t.length > 40) return false;
  if (/\d/.test(t)) return false;

  // Hide dotfiles, paths, subpages, and namespaced maintenance pages.
  if (t.startsWith(".") || t.includes("\\") || t.includes("/") || t.includes(":")) return false;

  // Filter version numbers / snapshots / obviously technical identifiers.
  if (/^\d+(?:\.\d+)+[a-z0-9-]*$/i.test(t)) return false;
  if (/^\d+[a-z]?w\d+[a-z]?$/i.test(t)) return false;
  if (/^pre-?\d+$/i.test(t) || /^rc\d+$/i.test(t)) return false;
  if (/^[\d.xXa-fA-F-]+$/.test(t)) return false;

  // Drop noisy maintenance / format / test pages that are poor homepage suggestions.
  if (/(格式|规范|列表|历史文件|存储格式|定义格式|测试|FAQ|教程)$/u.test(t)) return false;
  if (/(版本|快照|测试版|预发布版|候选版)$/u.test(t)) return false;
  if (/(消歧义|娑堟涔夛級|娑堟涔夛?)/u.test(t)) return false;

  return true;
}

async function loadWikiTitlePool() {
  try {
    const res = await fetch("/titles.txt", { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const raw = await res.text();
    const titles = raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(isDisplayableWikiTitle);
    wikiTitlePool = Array.from(new Set(titles));
  } catch (err) {
    console.error("加载 titles.txt 失败:", err);
    wikiTitlePool = [];
  }
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
  if (currentView === "about") {
    return "关于本项目";
  }

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

function getChatViewElements() {
  return [restartNotice, apiKeyNotice, chatList, inputBar].filter(Boolean);
}

function getViewElements(view) {
  return view === "about" ? [aboutPage].filter(Boolean) : getChatViewElements();
}

function isElementShown(element) {
  if (!element) return false;
  return !element.classList.contains("panel-hidden") && !element.classList.contains("hidden");
}

function resetViewAnimationState(elements) {
  elements.forEach((element) => {
    element.classList.remove("view-enter", "view-enter-active", "view-exit", "view-exit-active");
  });
}

function syncViewState() {
  const showingAbout = currentView === "about";
  chatList?.classList.toggle("panel-hidden", showingAbout);
  inputBar?.classList.toggle("panel-hidden", showingAbout);
  aboutPage?.classList.toggle("panel-hidden", !showingAbout);
  apiKeyNotice?.classList.toggle("panel-hidden", showingAbout);
  restartNotice?.classList.toggle("panel-hidden", showingAbout);

  if (aboutToggleBtn) {
    aboutToggleBtn.textContent = showingAbout ? "返回聊天" : "关于";
    aboutToggleBtn.setAttribute("aria-pressed", showingAbout ? "true" : "false");
  }

  syncAppTitle();
}

function setCurrentView(view) {
  const nextView = view === "about" ? "about" : "chat";
  if (nextView === currentView || isViewTransitioning) {
    return;
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    currentView = nextView;
    syncViewState();
    return;
  }

  const fromView = currentView;
  const fromElements = getViewElements(fromView).filter(isElementShown);
  const exitDuration = 220;
  const enterDuration = VIEW_TRANSITION_MS - exitDuration;

  isViewTransitioning = true;

  if (viewTransitionTimer) {
    window.clearTimeout(viewTransitionTimer);
    viewTransitionTimer = null;
  }

  resetViewAnimationState(fromElements);
  fromElements.forEach((element) => {
    element.classList.add("view-exit");
  });

  requestAnimationFrame(() => {
    fromElements.forEach((element) => element.classList.add("view-exit-active"));
  });

  viewTransitionTimer = window.setTimeout(() => {
    resetViewAnimationState(fromElements);
    currentView = nextView;
    syncViewState();

    const toElements = getViewElements(nextView).filter(isElementShown);
    resetViewAnimationState(toElements);
    toElements.forEach((element) => {
      element.classList.add("view-enter");
    });

    requestAnimationFrame(() => {
      toElements.forEach((element) => element.classList.add("view-enter-active"));
    });

    viewTransitionTimer = window.setTimeout(() => {
      resetViewAnimationState(toElements);
      isViewTransitioning = false;
      viewTransitionTimer = null;
    }, enterDuration);
  }, exitDuration);
}

function deleteSession(sessionToDeleteId) {
  const idx = sessions.findIndex((s) => s.id === sessionToDeleteId);
  if (idx === -1) {
    return;
  }

  const deletingActive = sessionId === sessionToDeleteId;
  sessions.splice(idx, 1);

  if (deletingActive && sessions.length === 0) {
    startDraftSession();
  } else if (deletingActive) {
    const fallback = sessions[idx] || sessions[idx - 1] || sessions[0];
    sessionId = fallback.id;
    draftSession = null;
  }

  saveSessions();
  renderSessionList();
  if (deletingActive) {
    const current = getCurrentSession();
    renderChatMessages(current?.messages || []);
  }
  syncAppTitle();
}

function animateSessionDeletion(item, sessionToDeleteId) {
  if (!item) {
    deleteSession(sessionToDeleteId);
    return;
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    deleteSession(sessionToDeleteId);
    return;
  }

  item.classList.add("deleting");
  window.setTimeout(() => {
    deleteSession(sessionToDeleteId);
  }, 220);
}

function renderAssistantFinalBubble(bubble, msg) {
  renderAssistantMarkdownBubble(bubble, msg, { includeMeta: true });
}

function renderAssistantMarkdownBubble(bubble, msg, options = {}) {
  const { includeMeta = false } = options;
  const html = marked.parse(msg.text || "(无回答)");
  const clean = DOMPurify.sanitize(html, {
    ALLOWED_URI_REGEXP: SAFE_URI,
    FORBID_ATTR: [/^on/i, "style"]
  });
  bubble.innerHTML = clean;
  bubble.classList.add("markdown-body");

  if (includeMeta) {
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
}

function renderChatMessages(messages = [], options = {}) {
  const { animateEmptyState = false } = options;
  chatList.innerHTML = "";
  chatList.classList.toggle("chat-empty", messages.length === 0);
  if (messages.length === 0) {
    const emptyState = createEmptyState();
    if (animateEmptyState && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      chatList.classList.add("chat-empty-enter");
      emptyState.classList.add("empty-state-enter");
      requestAnimationFrame(() => {
        chatList.classList.add("chat-empty-enter-active");
        emptyState.classList.add("empty-state-enter-active");
      });
      window.setTimeout(() => {
        chatList.classList.remove("chat-empty-enter", "chat-empty-enter-active");
        emptyState.classList.remove("empty-state-enter", "empty-state-enter-active");
      }, 420);
    } else {
      chatList.classList.remove("chat-empty-enter", "chat-empty-enter-active");
    }
    chatList.appendChild(emptyState);
  }
  for (const msg of messages) {
    const row = createMessageRow(msg.role, msg.text || "", msg.status || "normal", false);
    if (msg.role === "assistant" && msg.status === "normal") {
      renderAssistantFinalBubble(row.bubble, msg);
    }
  }
  nearBottom = true;
  scrollToBottomIfNeeded();
}

function pickRandomWikiLinks(count = 4) {
  const titlePool = wikiTitlePool.length > 0 ? wikiTitlePool : FALLBACK_EMPTY_STATE_WIKI_TITLES;
  const pool = [...titlePool];
  for (let i = pool.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, count).map((title) => ({
    title,
    url: titleToWikiLink(title)
  }));
}

function buildEmptyStateWikiLinks(linkList) {
  if (!linkList) return;
  linkList.innerHTML = "";
  const links = emptyStateWikiLinks.length > 0 ? emptyStateWikiLinks : pickRandomWikiLinks();
  emptyStateWikiLinks = links;

  for (const item of links) {
    const link = document.createElement("a");
    link.className = "empty-state-link";
    link.href = item.url;
    link.textContent = item.title;
    link.title = item.url;
    attachLiquidPointerTracking(link);
    link.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      try {
        await open(item.url);
      } catch (err) {
        console.error("打开链接失败:", err);
        setStatus("Error: 无法打开链接");
      }
    });
    linkList.appendChild(link);
  }
}

function createEmptyState() {
  const wrap = document.createElement("section");
  wrap.className = "empty-state";
  attachLiquidPointerTracking(wrap);

  const badge = document.createElement("p");
  badge.className = "empty-state-eyebrow";
  badge.textContent = "MineRAG";

  const title = document.createElement("h2");
  title.className = "empty-state-title";
  title.textContent = "你好，今天想查点什么？";

  const desc = document.createElement("p");
  desc.className = "empty-state-desc";
  desc.textContent = "你可以直接提问，也可以先随便逛几篇 Wiki，看看有没有感兴趣的内容。";

  const actions = document.createElement("div");
  actions.className = "empty-state-actions";

  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "empty-state-refresh";
  refreshBtn.textContent = "换一批";
  attachLiquidPointerTracking(refreshBtn);

  const linkList = document.createElement("div");
  linkList.className = "empty-state-links";
  buildEmptyStateWikiLinks(linkList);

  refreshBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    emptyStateWikiLinks = pickRandomWikiLinks();
    buildEmptyStateWikiLinks(linkList);
  });

  actions.appendChild(refreshBtn);
  wrap.append(badge, title, desc, actions, linkList);
  return wrap;
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
      if (currentView !== "chat") {
        setCurrentView("chat");
      }
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
      animateSessionDeletion(item, s.id);
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

function formatBytes(bytes) {
  if (!Number.isFinite(Number(bytes)) || Number(bytes) <= 0) {
    return "0 MB";
  }
  return `${(Number(bytes) / 1024 / 1024).toFixed(1)} MB`;
}

function setModelDownloadModalVisible(visible) {
  if (!modelDownloadModal) return;
  modelDownloadModal.classList.toggle("hidden", !visible);
}

function renderModelDownloadStatus(status, { lead } = {}) {
  const progress = Math.max(0, Math.min(1, Number(status?.progress ?? 0)));
  const downloadedBytes = Number(status?.downloadedBytes ?? 0);
  const totalBytes = Number(status?.totalBytes ?? 0);
  const percent = totalBytes > 0 ? Math.round(progress * 100) : status?.state === "completed" ? 100 : 0;

  if (lead && modelDownloadLead) {
    modelDownloadLead.textContent = lead;
  }
  if (modelDownloadPercent) {
    modelDownloadPercent.textContent = `${percent}%`;
  }
  if (modelDownloadBytes) {
    modelDownloadBytes.textContent = `${formatBytes(downloadedBytes)} / ${formatBytes(totalBytes)}`;
  }
  if (modelDownloadBar) {
    modelDownloadBar.style.width = `${percent}%`;
  }
  if (modelDownloadMessage) {
    modelDownloadMessage.textContent = status?.error || status?.message || "等待下载";
  }
}

function stopModelDownloadPolling() {
  if (!modelDownloadPollTimer) return;
  window.clearInterval(modelDownloadPollTimer);
  modelDownloadPollTimer = null;
}

async function pollModelDownloadStatus() {
  try {
    const status = await invoke("get_model_download_status");
    renderModelDownloadStatus(status);

    if (status.state === "completed") {
      stopModelDownloadPolling();
      modelDownloadBtn.disabled = true;
      modelDownloadBtn.textContent = "模型已就绪";
      modelGateActive = false;
      setStatus("模型下载完成，正在启动后端...");
      await invoke("ensure_backend_started");
      const backendReady = await waitBackendReady();
      if (backendReady) {
        setModelDownloadModalVisible(false);
        await loadStartupPreferences();
        await maybeAdviseBasicMaterial();
        updateApiKeyNotice();
      }
      return;
    }

    if (status.state === "error") {
      stopModelDownloadPolling();
      modelDownloadBtn.disabled = false;
      modelDownloadBtn.textContent = "重新下载";
      setStatus("模型下载失败");
    }
  } catch (_) {
    stopModelDownloadPolling();
    modelDownloadBtn.disabled = false;
    modelDownloadBtn.textContent = "重新下载";
    setStatus("模型下载状态读取失败");
  }
}

function startModelDownloadPolling() {
  if (modelDownloadPollTimer) return;
  modelDownloadPollTimer = window.setInterval(() => {
    pollModelDownloadStatus();
  }, 400);
}

async function ensureModelReady() {
  try {
    const model = await invoke("get_model_status");
    if (model.ready) {
      modelGateActive = false;
      setModelDownloadModalVisible(false);
      return true;
    }

    modelGateActive = true;
    setUiLocked(true, "缺少语义模型");
    setModelDownloadModalVisible(true);
    const missingSummary = Array.isArray(model.missingFiles) && model.missingFiles.length
      ? `缺少 ${model.missingFiles.length} 个模型文件，请先下载。`
      : "当前缺少本地语义检索模型，下载完成后才可使用问答功能。";
    renderModelDownloadStatus(
      {
        state: "idle",
        progress: 0,
        downloadedBytes: 0,
        totalBytes: 0,
        message: "等待下载"
      },
      { lead: missingSummary }
    );
    modelDownloadBtn.disabled = false;
    modelDownloadBtn.textContent = "开始下载";
    setStatus("请先下载语义模型");
    return false;
  } catch (_) {
    modelGateActive = true;
    setUiLocked(true, "模型检测失败");
    setModelDownloadModalVisible(true);
    renderModelDownloadStatus(
      { state: "error", progress: 0, downloadedBytes: 0, totalBytes: 0, error: "模型检测失败" },
      { lead: "无法检测模型目录，请检查程序安装是否完整。" }
    );
    modelDownloadBtn.disabled = true;
    modelDownloadBtn.textContent = "不可用";
    return false;
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
  openSettings(apiKeyNoticeLink);
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
    if (a.classList.contains("empty-state-link")) {
      return;
    }
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
  clone.querySelectorAll(".refs, .footer, .dots, .thinking-sheen").forEach((node) => node.remove());
  return clone.textContent.trim();
}

function setThinkingBubbleText(bubble, text) {
  if (!bubble) {
    return;
  }
  const nextText = text ?? "";
  const label = bubble.querySelector(".thinking-label");
  const sheen = bubble.querySelector(".thinking-sheen");
  if (label) {
    label.textContent = nextText;
  }
  if (sheen) {
    sheen.textContent = nextText;
  }
  if (!label && !sheen) {
    bubble.textContent = nextText;
  }
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

function clearEmptyStateUI() {
  chatList.classList.remove("chat-empty");
  chatList.querySelector(".empty-state")?.remove();
}

function createMessageRow(role, text, status, animate = true) {
  clearEmptyStateUI();

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
  if (status === "thinking") {
    const label = document.createElement("span");
    label.className = "thinking-label";
    label.textContent = text;

    const sheen = document.createElement("span");
    sheen.className = "thinking-sheen";
    sheen.setAttribute("aria-hidden", "true");
    sheen.textContent = text;

    bubble.appendChild(label);
    bubble.appendChild(sheen);
  } else {
    bubble.textContent = text;
  }

  if (status === "thinking") {
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
  if (modelGateActive) {
    setModelDownloadModalVisible(true);
    setStatus("请先下载语义模型");
    return;
  }
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
    const base = await getApiBase();
    res = await fetchJsonWithRetry(`${base}/api/send`, {
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
  const base = await getApiBase();
  const streamUrl = `${base}/api/stream?session_id=${encodeURIComponent(sessionId)}&message_id=${encodeURIComponent(message_id)}`;

  let finished = false;
  let retried = false;
  let es = null;
  let streamedAnswerText = "";
  let streamRenderScheduled = false;
  let streamRenderFrame = null;

  const flushStreamingMarkdown = () => {
    streamRenderFrame = null;
    streamRenderScheduled = false;
    if (finished || sessionId !== requestSessionId) {
      return;
    }
    renderAssistantMarkdownBubble(assistantMsg.bubble, { text: streamedAnswerText || "(无回答)" });
    scrollToBottomIfNeeded();
  };

  const scheduleStreamingMarkdownRender = () => {
    if (streamRenderScheduled) {
      return;
    }
    streamRenderScheduled = true;
    streamRenderFrame = window.requestAnimationFrame(flushStreamingMarkdown);
  };

  const ensureStreamingBubble = () => {
    if (!assistantMsg.bubble.classList.contains("thinking")) {
      return;
    }
    animateThinkingToFinalBubble(assistantMsg.bubble, () => {
      removeThinking(assistantMsg.bubble);
      renderAssistantMarkdownBubble(assistantMsg.bubble, { text: streamedAnswerText || "(无回答)" });
    });
  };

  const startStream = () => {
    es = new EventSource(streamUrl);

    es.addEventListener("progress", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const nextText = data.text ?? "";
        if (!streamedAnswerText) {
          assistantMessage.text = nextText || assistantMessage.text;
        }
        activeSession.updatedAt = Date.now();
        saveSessions();

        if (sessionId !== requestSessionId) {
          return;
        }
        if (!streamedAnswerText && assistantMsg.bubble.classList.contains("thinking")) {
          if (nextText.trim() !== "") {
            setThinkingBubbleText(assistantMsg.bubble, nextText);
          }
        } else if (!streamedAnswerText) {
          assistantMsg.bubble.textContent = nextText;
        }
        scrollToBottomIfNeeded();
      } catch (_) {}
    });

    es.addEventListener("answer_delta", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const delta = data.delta ?? "";
        if (!delta) {
          return;
        }

        streamedAnswerText += delta;
        assistantMessage.text = streamedAnswerText;
        activeSession.updatedAt = Date.now();
        saveSessions();

        if (sessionId !== requestSessionId) {
          return;
        }

        ensureStreamingBubble();
        scheduleStreamingMarkdownRender();
      } catch (_) {}
    });

    es.addEventListener("final", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        finished = true;
        if (streamRenderFrame !== null) {
          window.cancelAnimationFrame(streamRenderFrame);
          streamRenderFrame = null;
        }
        streamRenderScheduled = false;
        assistantMessage.status = "normal";
        assistantMessage.text = data.answer || streamedAnswerText || "(无回答)";
        assistantMessage.evidences_for_llm = data.evidences_for_llm || [];
        assistantMessage.token_usage = data.token_usage || {};
        assistantMessage.timing_ms = data.timing_ms || {};
        activeSession.updatedAt = Date.now();
        saveSessions();

        if (sessionId === requestSessionId) {
          if (assistantMsg.bubble.classList.contains("thinking")) {
            animateThinkingToFinalBubble(assistantMsg.bubble, () => {
              removeThinking(assistantMsg.bubble);
              renderAssistantFinalBubble(assistantMsg.bubble, assistantMessage);
            });
          } else {
            renderAssistantFinalBubble(assistantMsg.bubble, assistantMessage);
          }
        }

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
        finished = true;
        if (streamRenderFrame !== null) {
          window.cancelAnimationFrame(streamRenderFrame);
          streamRenderFrame = null;
        }
        streamRenderScheduled = false;
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
      finished = true;
      if (streamRenderFrame !== null) {
        window.cancelAnimationFrame(streamRenderFrame);
        streamRenderFrame = null;
      }
      streamRenderScheduled = false;
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

async function openSettings(sourceEl = settingsBtn) {
  try {
    const base = await getApiBase();
    const cfg = await fetchJsonWithRetry(`${base}/api/config`, {}, 8, 500);

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
    cfgBasicMaterial.checked = normalizeMaterialMode(cfg.material_mode) === MATERIAL_MODE_BASIC;
    lastLoadedSettingsSnapshot = getSettingsSnapshot();
    setGpuDetectionUiPending();

    showSettingsModal(sourceEl);
    detectGraphicsCapability(true);
  } catch (err) {
    setStatus("Error");
  }
}
settingsBtn.addEventListener("click", () => openSettings(settingsBtn));
aboutToggleBtn?.addEventListener("click", () => {
  setCurrentView(currentView === "about" ? "chat" : "about");
});
donatePreviewBtn?.addEventListener("click", () => {
  showDonateModal();
});
donateCloseBtn?.addEventListener("click", () => {
  closeDonateModal();
});
modelDownloadBtn?.addEventListener("click", async () => {
  modelDownloadBtn.disabled = true;
  modelDownloadBtn.textContent = "下载中...";
  renderModelDownloadStatus({
    state: "starting",
    progress: 0,
    downloadedBytes: 0,
    totalBytes: 0,
    message: "正在启动模型下载..."
  });
  setStatus("正在下载语义模型...");

  try {
    await invoke("start_model_download");
    await pollModelDownloadStatus();
    startModelDownloadPolling();
  } catch (err) {
    modelDownloadBtn.disabled = false;
    modelDownloadBtn.textContent = "重新下载";
    renderModelDownloadStatus({
      state: "error",
      progress: 0,
      downloadedBytes: 0,
      totalBytes: 0,
      error: err?.message || "启动下载失败"
    });
    setStatus("模型下载启动失败");
  }
});
deepseekApiKeyLink?.addEventListener("click", async () => {
  try {
    await open("https://platform.deepseek.com/api_keys");
  } catch (_) {}
});
gpuNoticeSwitchBtn?.addEventListener("click", async () => {
  gpuNoticeSwitchBtn.disabled = true;
  try {
    await persistMaterialMode(MATERIAL_MODE_BASIC);
    setGpuNoticeVisible(false);
    setStatus("已切换为普通材质");
  } catch (_) {
    setStatus("Error");
  } finally {
    gpuNoticeSwitchBtn.disabled = false;
  }
});
gpuNoticeDismissBtn?.addEventListener("click", () => {
  setGpuNoticeVisible(false);
});
gpuDetectRefreshBtn?.addEventListener("click", async () => {
  gpuDetectRefreshBtn.disabled = true;
  try {
    await detectGraphicsCapability(true);
  } finally {
    gpuDetectRefreshBtn.disabled = false;
  }
});

settingsCancel.addEventListener("click", () => {
  closeSettingsModal();
});

settingsModal?.addEventListener("click", (event) => {
  if (event.target === settingsModal) {
    closeSettingsModal();
  }
});

donateModal?.addEventListener("click", (event) => {
  if (event.target === donateModal) {
    closeDonateModal();
  }
});

settingsModalContent?.addEventListener("click", (event) => {
  event.stopPropagation();
});

donateModalContent?.addEventListener("click", (event) => {
  event.stopPropagation();
});

modelDownloadContent?.addEventListener("click", (event) => {
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
    material_mode: cfgBasicMaterial.checked ? MATERIAL_MODE_BASIC : MATERIAL_MODE_LIQUID,
    debug_mode: !!cfgDebugMode.checked,
  };
  const nextSnapshot = getSettingsSnapshot();
  const settingsChanged = hasSettingsChanged(nextSnapshot);

  try {
    const base = await getApiBase();
    await fetchJsonWithRetry(
      `${base}/api/config`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updated)
      },
      6,
      500
    );

    document.documentElement.style.setProperty("--font-size", `${updated.font_size}px`);
    applyMaterialMode(updated.material_mode);
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
  if (currentView !== "chat") {
    setCurrentView("chat");
  }
  renderSessionList();
  renderChatMessages([], { animateEmptyState: true });
  syncAppTitle();
  autoResizeInput();
});

window.addEventListener("resize", () => updateInputBarLayout());

document.documentElement.style.setProperty("--font-size", "14px");
async function waitBackendReady() {
  let base;
  try {
    await invoke("ensure_backend_started");
  } catch (_) {}
  try {
    base = await getApiBase();
  } catch (_) {
    setStatus("Backend port unavailable");
    return false;
  }

  for (let i = 0; i < 20; i++) {
    try {
      const res = await fetch(`${base}/api/health`);
      if (res.ok) {
        setStatus("Ready");
        return true;
      }
    } catch (_) {}

    setStatus("Starting backend...");
    await new Promise(r => setTimeout(r, 500));
  }

  setStatus("Backend not responding");
  return false;
}

window.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".sidebar, .main").forEach(attachLiquidPointerTracking);
  document.querySelectorAll(".btn, .input-bar").forEach(attachLiquidPointerTracking);
  document
    .querySelectorAll(".modal-content, .form input, .form select")
    .forEach(attachLiquidPointerTracking);
  document.querySelectorAll(".about-card").forEach(attachLiquidPointerTracking);
  await loadWikiTitlePool();
  loadSessions();
  renderSessionList();
  const current = getCurrentSession();
  renderChatMessages(current?.messages || []);
  syncViewState();
  syncAppTitle();
  autoResizeInput();

  const modelReady = await ensureModelReady();
  if (modelReady) {
    const backendReady = await waitBackendReady();
    if (backendReady) {
      await loadStartupPreferences();
      await maybeAdviseBasicMaterial();
    }
  }
  updateApiKeyNotice();   // ✅ 只显示提示，不弹窗
});
