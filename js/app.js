(function () {
  const config = window.OneWishConfig;
  const state = window.OneWishState.createInitialState();
  const renderers = window.OneWishRenderers;
  const utils = window.OneWishRenderUtils;

  const knownTypes = new Set(["status", "market", "signal", "position", "order", "fill", "pnl", "risk", "event"]);

  function applyMessage(message) {
    if (!message || !knownTypes.has(message.type)) {
      state.events.unshift({
        type: "event",
        ts: new Date().toISOString(),
        eventType: "GUI_UNKNOWN_MESSAGE",
        severity: "ERROR",
        message: "Unknown or missing WebSocket message type",
        data: message,
      });
      return;
    }

    state.lastMessageAt = new Date().toISOString();

    switch (message.type) {
      case "status":
        state.status = {
          botState: message.botState || "WAITING_FOR_FEED",
          connection: message.connection || "DISCONNECTED",
          latencyMs: message.latencyMs ?? null,
          serverTimeUtc: message.serverTimeUtc || null,
        };
        break;
      case "market":
        state.market[message.asset] = message;
        break;
      case "signal":
        state.signal[message.asset] = message;
        break;
      case "position":
        // backend flaguje zamknięcie (closed: true) — usuwamy z tabeli zamiast
        // nadpisywać, inaczej zamknięte pozycje wisiałyby jako otwarte
        if (message.closed) {
          state.positions.delete(message.id);
        } else {
          state.positions.set(message.id, message);
        }
        break;
      case "order":
        state.orders.push({ ...message, ts: state.lastMessageAt });
        state.orders = state.orders.slice(-80);
        break;
      case "fill":
        state.fills.push(message);
        state.fills = state.fills.slice(-80);
        break;
      case "pnl":
        state.pnl = message;
        break;
      case "risk":
        state.risk = message;
        break;
      case "event":
        state.events.unshift(message);
        state.events = state.events.slice(0, 160);
        break;
      default:
        break;
    }
  }

  function renderAll() {
    renderers.renderStatus(state);
    renderers.renderMarket(state);
    renderers.renderScanner(state);
    renderers.renderFairValue(state);
    renderers.renderPositions(state);
    renderers.renderExecution(state);
    renderers.renderPnl(state);
    renderers.renderRisk(state);
    renderers.renderEventLog(state);
  }

  function createAdapter() {
    if (config.adapter === "ws") {
      return new window.OneWishAdapters.WsAdapter(config.wsUrl);
    }
    return new window.OneWishAdapters.MockAdapter();
  }

  const adapter = createAdapter();

  adapter.onMessage((message) => {
    applyMessage(message);
    renderAll();
  });

  utils.byId("assetSelect").addEventListener("change", (event) => {
    state.selectedAsset = event.target.value;
    renderers.renderFairValue(state);
  });

  utils.byId("killSwitch").addEventListener("click", () => {
    // GUI niczego samo nie zatrzymuje. Wysyła komendę operatora do backendu/adaptora.
    adapter.sendCommand({
      action: "kill",
      ts: new Date().toISOString(),
      source: "gui",
    });
  });

  renderAll();

  adapter.connect().catch((error) => {
    applyMessage({
      type: "status",
      botState: "WAITING_FOR_FEED",
      connection: "DISCONNECTED",
      latencyMs: null,
      serverTimeUtc: new Date().toISOString(),
    });
    applyMessage({
      type: "event",
      ts: new Date().toISOString(),
      eventType: "GUI_ADAPTER_CONNECT_FAILED",
      severity: "ERROR",
      message: String(error.message || error),
      data: { adapter: config.adapter, wsUrl: config.wsUrl },
    });
    renderAll();
  });

  setInterval(() => {
    renderers.renderStatus(state);
    renderers.renderMarket(state);
    renderers.renderPositions(state);
  }, 1000);
})();
