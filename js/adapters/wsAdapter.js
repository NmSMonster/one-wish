(function () {
  class WsAdapter {
    constructor(url) {
      this.url = url;
      this.listeners = new Set();
      this.socket = null;
    }

    connect() {
      return new Promise((resolve, reject) => {
        this.socket = new WebSocket(this.url);

        this.socket.addEventListener("open", () => {
          this.emit({
            type: "status",
            botState: "WAITING_FOR_FEED",
            connection: "LIVE",
            latencyMs: null,
            serverTimeUtc: new Date().toISOString(),
          });
          resolve();
        });

        this.socket.addEventListener("message", (event) => {
          try {
            const message = JSON.parse(event.data);
            this.emit(message);
          } catch (error) {
            this.emit({
              type: "event",
              ts: new Date().toISOString(),
              eventType: "GUI_PARSE_ERROR",
              severity: "ERROR",
              message: "Frontend could not parse WebSocket JSON message",
              data: { error: String(error) },
            });
          }
        });

        this.socket.addEventListener("close", () => {
          this.emit({
            type: "status",
            botState: "WAITING_FOR_FEED",
            connection: "DISCONNECTED",
            latencyMs: null,
            serverTimeUtc: new Date().toISOString(),
          });
        });

        this.socket.addEventListener("error", () => {
          this.emit({
            type: "event",
            ts: new Date().toISOString(),
            eventType: "GUI_WS_ERROR",
            severity: "ERROR",
            message: `WebSocket error for ${this.url}`,
            data: { url: this.url },
          });
          reject(new Error(`WebSocket error for ${this.url}`));
        });
      });
    }

    disconnect() {
      if (this.socket) this.socket.close();
    }

    onMessage(listener) {
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    // GUI nie podejmuje decyzji tradingowych. Wysyła wyłącznie komendę operatora.
    sendCommand(command) {
      const payload = JSON.stringify({
        type: "command",
        action: command.action,
        ts: command.ts || new Date().toISOString(),
        source: command.source || "gui",
      });
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        this.emit({
          type: "event",
          ts: new Date().toISOString(),
          eventType: "GUI_COMMAND_NOT_SENT",
          severity: "ERROR",
          message: "Command not sent because WebSocket is not connected",
          data: command,
        });
        return;
      }
      this.socket.send(payload);
    }

    emit(message) {
      this.listeners.forEach((listener) => listener(message));
    }
  }

  window.OneWishAdapters = window.OneWishAdapters || {};
  window.OneWishAdapters.WsAdapter = WsAdapter;
})();
