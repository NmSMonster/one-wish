(function () {
  const utils = window.OneWishRenderUtils;

  function renderStatus(state) {
    const status = state.status;
    const simulationBanner = utils.byId("simulationBanner");
    simulationBanner.classList.toggle("hidden", !(state.simulation || state.adapterMode === "mock"));

    utils.text("feedModeLabel", `ADAPTER: ${state.adapterMode.toUpperCase()}`);
    utils.text("botState", status.botState || "WAITING FOR FEED");
    utils.text("connectionState", status.connection || "DISCONNECTED");
    utils.text("latencyMs", utils.fmtMs(status.latencyMs));
    utils.text("serverTimeUtc", utils.fmtUtc(status.serverTimeUtc));
    utils.text("brandClock", utils.fmtUtc(status.serverTimeUtc));
    utils.text("brandLatency", status.latencyMs === null ? "--" : Math.round(status.latencyMs));

    const connection = utils.byId("brandConnection");
    const label = status.connection || "DISCONNECTED";
    connection.classList.remove("connected", "disconnected");
    if (["CONNECTED", "LIVE", "SIMULATION"].includes(label)) connection.classList.add("connected");
    if (label === "DISCONNECTED") connection.classList.add("disconnected");
    connection.innerHTML = `<span></span> ${label}`;

    const botState = utils.byId("botState");
    botState.className = utils.stateClass(status.botState);
    const connectionState = utils.byId("connectionState");
    connectionState.className = utils.stateClass(status.connection);

    const pnl = state.pnl;
    utils.text("brandNetPnl", pnl ? utils.fmtCompactMoney(pnl.net) : "NO DATA");
    const brandPnl = utils.byId("brandNetPnl");
    brandPnl.className = pnl ? utils.classBySign(pnl.net) : "no-data";

    const risk = state.risk;
    utils.text("brandRisk", risk ? risk.lastDecision : "NO DATA");
    utils.byId("brandRisk").className = risk ? utils.stateClass(risk.lastDecision) : "no-data";

    window.OneWishConfig.assets.forEach((asset) => {
      const node = document.querySelector(`[data-brand-basis="${asset}"]`);
      const row = state.market[asset];
      node.textContent = row ? utils.fmtBps(row.basisBps) : "NO DATA";
      node.className = row ? utils.classBySign(row.basisBps) : "no-data";
    });
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderStatus = renderStatus;
})();
