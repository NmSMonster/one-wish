(function () {
  const utils = window.OneWishRenderUtils;

  function renderExecution(state) {
    const rows = [];
    state.orders.slice(-12).forEach((order) => {
      rows.push({
        ts: order.ts || state.lastMessageAt,
        kind: "ORDER",
        asset: order.asset,
        leg: order.leg,
        detail: `${order.side} ${order.qty} ${order.orderType} @ ${utils.fmtMoney(order.price)}`,
        status: order.status,
      });
    });
    state.fills.slice(-12).forEach((fill) => {
      rows.push({
        ts: fill.ts,
        kind: "FILL",
        asset: fill.asset,
        leg: fill.leg,
        detail: `${fill.qty} @ ${utils.fmtMoney(fill.price)} / fee ${utils.fmtMoney(fill.fee)}`,
        status: fill.orderId,
      });
    });
    rows.sort((a, b) => new Date(b.ts || 0).getTime() - new Date(a.ts || 0).getTime());

    if (!rows.length) {
      utils.byId("executionLog").innerHTML = `<div class="execution-row"><b class="no-data">NO ORDERS OR FILLS</b></div>`;
      return;
    }

    utils.byId("executionLog").innerHTML = rows.slice(0, 16).map((row) => `
      <div class="execution-row">
        <div><span>Type</span><b>${row.kind}</b></div>
        <div><span>Asset</span><b class="asset-cell">${row.asset}</b></div>
        <div><span>Leg</span><b>${row.leg}</b></div>
        <div><span>Status</span><b class="${utils.stateClass(row.status)}">${row.status}</b></div>
        <div><span>Details</span><b>${row.detail}</b></div>
        <div><span>UTC</span><b>${utils.fmtUtc(row.ts)}</b></div>
      </div>
    `).join("");
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderExecution = renderExecution;
})();
