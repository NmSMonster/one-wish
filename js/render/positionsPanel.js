(function () {
  const utils = window.OneWishRenderUtils;

  function renderPositions(state) {
    const positions = Array.from(state.positions.values());
    if (!positions.length) {
      utils.byId("positionRows").innerHTML = `<tr><td colspan="9" class="no-data">NO OPEN POSITIONS</td></tr>`;
      return;
    }

    utils.byId("positionRows").innerHTML = positions.map((position) => `
      <tr>
        <td>${position.id}</td>
        <td class="asset-cell">${position.asset}</td>
        <td>${utils.fmtNumber(position.spotQty, 4)} @ ${utils.fmtMoney(position.spotEntry)}</td>
        <td>${utils.fmtNumber(position.perpQty, 4)} @ ${utils.fmtMoney(position.perpEntry)}</td>
        <td class="${Math.abs(position.netDelta) > 0.01 ? "red" : "green"}">${utils.fmtNumber(position.netDelta, 4)}</td>
        <td class="${utils.classBySign(position.unrealizedPnl)}">${utils.fmtMoney(position.unrealizedPnl)}</td>
        <td class="${utils.classBySign(position.fundingAccrued)}">${utils.fmtMoney(position.fundingAccrued)}</td>
        <td>${position.marginRatio === null || position.marginRatio === undefined ? "—" : utils.fmtPct(position.marginRatio)}</td>
        <td>${utils.fmtHold(position.openedTs)}</td>
      </tr>
    `).join("");
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderPositions = renderPositions;
})();
