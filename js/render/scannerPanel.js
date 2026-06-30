(function () {
  const utils = window.OneWishRenderUtils;

  function signalClass(value) {
    if (value === "EDGE_DETECTED") return "edge";
    if (value === "EDGE_LOST") return "bad";
    return "warn";
  }

  function renderScanner(state) {
    const cards = window.OneWishConfig.assets.map((asset) => {
      const signal = state.signal[asset];
      if (!signal) {
        return `
          <article class="scanner-card">
            <header><h2>${asset}</h2><div class="state-pill warn">NO DATA</div></header>
            <div class="reason">WAITING FOR FEED</div>
          </article>
        `;
      }
      return `
        <article class="scanner-card">
          <header>
            <h2>${asset}</h2>
            <div class="state-pill ${signalClass(signal.state)}">${signal.state}</div>
          </header>
          <div class="scanner-metrics">
            <div><span>Fair basis</span><b>${utils.fmtBps(signal.fairBasisBps)}</b></div>
            <div><span>Observed</span><b>${utils.fmtBps(signal.observedBasisBps)}</b></div>
            <div><span>Dislocation</span><b class="${utils.classBySign(signal.dislocationBps)}">${utils.fmtBps(signal.dislocationBps)}</b></div>
            <div><span>Expected net edge</span><b class="${utils.classBySign(signal.expectedNetEdge)}">${utils.fmtBps(signal.expectedNetEdge)}</b></div>
          </div>
          <div class="reason">${signal.reason || "NO REASON PROVIDED"}</div>
        </article>
      `;
    }).join("");
    utils.byId("signalCards").innerHTML = cards;
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderScanner = renderScanner;
})();
