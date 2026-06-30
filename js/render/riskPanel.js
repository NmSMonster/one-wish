(function () {
  const utils = window.OneWishRenderUtils;

  function pct(used, limit) {
    if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return 0;
    return Math.max(0, Math.min(100, (used / limit) * 100));
  }

  function riskItem(label, used, limit, displayUsed, displayLimit) {
    const usedPct = pct(used, limit);
    return `
      <div class="risk-item">
        <span>${label}</span>
        <b>${displayUsed} / ${displayLimit}</b>
        <div class="risk-bar ${usedPct > 80 ? "danger" : ""}" style="--used:${usedPct}%"><i></i></div>
      </div>
    `;
  }

  function renderRisk(state) {
    const risk = state.risk;
    if (!risk) {
      utils.text("riskDecision", "NO DATA");
      utils.byId("riskLimits").innerHTML = `<div class="risk-item"><span>Risk</span><b class="no-data">WAITING FOR FEED</b></div>`;
      utils.text("riskReason", "WAITING FOR FEED");
      return;
    }

    utils.text("riskDecision", risk.lastDecision);
    utils.byId("riskDecision").className = `head-state ${utils.stateClass(risk.lastDecision)}`;
    const exposureItems = (risk.perAssetExposure || []).map((item) => (
      riskItem(`${item.asset} exposure`, item.used, item.limit, utils.fmtMoney(item.used), utils.fmtMoney(item.limit))
    ));
    const marginLimit = 1;
    const marginUsed = 1 - Number(risk.marginBuffer || 0);
    utils.byId("riskLimits").innerHTML = [
      riskItem("Daily loss", risk.dailyLoss.used, risk.dailyLoss.limit, utils.fmtMoney(risk.dailyLoss.used), utils.fmtMoney(risk.dailyLoss.limit)),
      riskItem("Open positions", risk.openPositions.used, risk.openPositions.limit, risk.openPositions.used, risk.openPositions.limit),
      riskItem("Margin buffer used", marginUsed, marginLimit, utils.fmtPct(marginUsed), "100.0000%"),
      ...exposureItems,
    ].join("");
    utils.text("riskReason", risk.reason || "NO REASON PROVIDED");
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderRisk = renderRisk;
})();
