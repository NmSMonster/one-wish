(function () {
  const assets = window.OneWishConfig.assets;

  function createInitialState() {
    return {
      adapterMode: window.OneWishConfig.adapter,
      simulation: window.OneWishConfig.adapter === "mock",
      status: {
        botState: "WAITING_FOR_FEED",
        connection: "DISCONNECTED",
        latencyMs: null,
        serverTimeUtc: null,
      },
      market: Object.fromEntries(assets.map((asset) => [asset, null])),
      signal: Object.fromEntries(assets.map((asset) => [asset, null])),
      positions: new Map(),
      orders: [],
      fills: [],
      pnl: null,
      risk: null,
      events: [],
      selectedAsset: "BTC",
      lastMessageAt: null,
    };
  }

  window.OneWishState = {
    createInitialState,
  };
})();
