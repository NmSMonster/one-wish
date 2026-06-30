(function () {
  const utils = window.OneWishRenderUtils;

  function renderFairValue(state) {
    const asset = state.selectedAsset;
    const signal = state.signal[asset];
    const canvas = utils.byId("fairValueChart");
    const empty = utils.byId("fairValueEmpty");
    if (!signal || !signal.series || !signal.series.length) {
      empty.classList.remove("hidden");
      utils.drawNoData(canvas);
      return;
    }
    empty.classList.add("hidden");
    utils.drawSeriesChart(canvas, [
      {
        color: "#70c7ff",
        points: signal.series.map((point) => ({ ts: point.ts, value: point.fairBasisBps })),
        width: 2,
      },
      {
        color: "#f4af2e",
        points: signal.series.map((point) => ({ ts: point.ts, value: point.observedBasisBps })),
        width: 2.4,
      },
    ]);
  }

  function renderPnl(state) {
    const pnl = state.pnl;
    utils.text("pnlRealized", pnl ? utils.fmtMoney(pnl.realized) : "NO DATA");
    utils.text("pnlUnrealized", pnl ? utils.fmtMoney(pnl.unrealized) : "NO DATA");
    utils.text("pnlFunding", pnl ? utils.fmtMoney(pnl.fundingCollected) : "NO DATA");
    utils.text("pnlFees", pnl ? utils.fmtMoney(pnl.feesPaid) : "NO DATA");
    utils.text("pnlNet", pnl ? utils.fmtMoney(pnl.net) : "NO DATA");
    ["pnlRealized", "pnlUnrealized", "pnlFunding", "pnlNet"].forEach((id) => {
      const node = utils.byId(id);
      if (node && pnl) node.className = utils.classBySign(Number(node.textContent.replace(/[^0-9.-]/g, "")));
    });
    const canvas = utils.byId("pnlChart");
    const empty = utils.byId("pnlEmpty");
    if (!pnl || !pnl.series || !pnl.series.length) {
      empty.classList.remove("hidden");
      utils.drawNoData(canvas);
      return;
    }
    empty.classList.add("hidden");
    utils.drawSeriesChart(canvas, [
      {
        color: "#49f08a",
        points: pnl.series,
        width: 2.4,
      },
    ]);
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderFairValue = renderFairValue;
  window.OneWishRenderers.renderPnl = renderPnl;
})();
