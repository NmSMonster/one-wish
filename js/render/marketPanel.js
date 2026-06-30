(function () {
  const utils = window.OneWishRenderUtils;

  function renderMarket(state) {
    const rows = window.OneWishConfig.assets.map((asset) => {
      const row = state.market[asset];
      if (!row) {
        return `<tr><td class="asset-cell">${asset}</td><td colspan="8" class="no-data">WAITING FOR FEED</td></tr>`;
      }
      return `
        <tr>
          <td class="asset-cell">${asset}</td>
          <td>${utils.fmtMoney(row.spot)}</td>
          <td>${utils.fmtMoney(row.perp)}</td>
          <td>${utils.fmtMoney(row.index)}</td>
          <td class="${utils.classBySign(row.basisBps)}">${utils.fmtBps(row.basisBps)}</td>
          <td class="${utils.classBySign(row.fundingRate)}">${utils.fmtPct(row.fundingRate)}</td>
          <td class="${utils.classBySign(row.predictedFunding)}">${utils.fmtPct(row.predictedFunding)}</td>
          <td>${utils.fmtCountdown(row.nextFundingTs)}</td>
          <td class="${row.dataLagMs > 250 ? "red" : "green"}">${utils.fmtMs(row.dataLagMs)}</td>
        </tr>
      `;
    }).join("");

    utils.byId("marketRows").innerHTML = rows;
  }

  window.OneWishRenderers = window.OneWishRenderers || {};
  window.OneWishRenderers.renderMarket = renderMarket;
})();
