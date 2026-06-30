(function () {
  const utils = window.OneWishRenderUtils;

  function severityClass(severity) {
    if (["CRITICAL", "ERROR"].includes(severity)) return "red";
    if (["WARN", "WARNING"].includes(severity)) return "amber";
    if (["INFO"].includes(severity)) return "green";
    return "muted";
  }

  function renderEventLog(state) {
    if (!state.events.length) {
      utils.byId("eventLog").innerHTML = `<div class="event-row"><b class="no-data">WAITING FOR FEED</b></div>`;
      return;
    }

    utils.byId("eventLog").innerHTML = state.events.slice(0, 28).map((event) => `
      <div class="event-row">
        <div><span>UTC</span><b>${utils.fmtUtc(event.ts)}</b></div>
        <div><span>Event</span><b>${event.eventType}</b></div>
        <div><span>Severity</span><b class="${severityClass(event.severity)}">${event.severity}</b></div>
        <div><span>Message</span><b>${event.message}</b></div>
      </div>
    `).join("");
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderEventLog = renderEventLog;
})();
