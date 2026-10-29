(function () {
  "use strict";
  if (document.getElementById("zhixing-support-widget")) return;
  var source = document.currentScript;
  if (!source) return;
  var origin = new URL(source.src).origin;
  var previewMode = source.getAttribute("data-preview-mode") === "1";
  var storageKey = "zhixing_support_" + origin + (previewMode ? "_preview" : "");
  var root = document.createElement("div");
  root.id = "zhixing-support-widget";
  var shadow = root.attachShadow({ mode: "open" });
  var style = document.createElement("style");
  style.textContent =
    ':host{all:initial}button{font:600 14px system-ui,-apple-system,"PingFang SC",sans-serif;cursor:pointer}.launcher{position:fixed;right:24px;bottom:24px;z-index:2147483600;background:#174a81;color:white;border:0;border-radius:40px;padding:16px 22px;display:flex;align-items:center;gap:9px;box-shadow:0 6px 24px #153b6429}.launcher:hover{background:#123d6c}.launcher:focus-visible{outline:3px solid #72a6ef;outline-offset:4px}.panel{position:fixed;right:24px;bottom:96px;z-index:2147483599;width:400px;height:min(680px,calc(100dvh - 120px));border:1px solid #dce3ea;border-radius:16px;box-shadow:0 16px 64px #153b6430;overflow:hidden;background:white}.panel[hidden]{display:none}.panel iframe{width:100%;height:100%;border:0;display:block}@media(max-width:500px){.panel{right:8px;left:8px;bottom:84px;width:auto;height:calc(100dvh - 100px)}.launcher{right:16px;bottom:16px}}';
  shadow.appendChild(style);
  var button = document.createElement("button");
  button.className = "launcher";
  button.setAttribute("aria-label", "打开在线咨询");
  button.setAttribute("aria-expanded", "false");
  button.innerHTML =
    '<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"/></svg><span>在线咨询</span>';
  var panel = document.createElement("div");
  panel.className = "panel";
  panel.hidden = true;
  var frame = null;
  function open() {
    if (!frame) {
      frame = document.createElement("iframe");
      frame.title = "知行智能客服";
      frame.src =
        origin +
        "/chat?parentOrigin=" +
        encodeURIComponent(location.origin) +
        (previewMode ? "&previewMode=1" : "");
      frame.setAttribute(
        "sandbox",
        "allow-scripts allow-same-origin allow-forms",
      );
      panel.appendChild(frame);
    }
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-label", "关闭在线咨询");
    setTimeout(function () {
      if (frame) frame.focus();
    }, 100);
  }
  function close() {
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "打开在线咨询");
    button.focus();
  }
  button.addEventListener("click", function () {
    if (panel.hidden) open();
    else close();
  });
  window.addEventListener("zhixing:open", open);
  window.addEventListener("message", function (event) {
    if (
      !frame ||
      event.source !== frame.contentWindow ||
      event.origin !== origin ||
      !event.data ||
      typeof event.data.type !== "string"
    )
      return;
    if (event.data.type === "zhixing:ready") {
      var saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(storageKey) || "null");
      } catch (_) {}
      frame.contentWindow.postMessage(
        { type: "zhixing:init", session: saved },
        origin,
      );
    }
    if (event.data.type === "zhixing:save") {
      var value = event.data.session;
      if (
        value &&
        typeof value.id === "string" &&
        value.id.length < 100 &&
        typeof value.token === "string" &&
        value.token.length < 200
      ) {
        try {
          localStorage.setItem(
            storageKey,
            JSON.stringify({ id: value.id, token: value.token }),
          );
        } catch (_) {}
      }
    }
    if (event.data.type === "zhixing:close") close();
  });
  shadow.appendChild(panel);
  shadow.appendChild(button);
  document.body.appendChild(root);
})();
