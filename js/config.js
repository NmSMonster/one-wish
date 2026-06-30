window.OneWishConfig = {
  // Jedna linia przełączenia:
  // "mock" = lokalna symulacja tego samego kontraktu, wyraźnie oznaczona banerem.
  // "ws" = realny backend WebSocket.
  adapter: "mock",
  wsUrl: "ws://127.0.0.1:8765/gui",
  assets: ["BTC", "ETH", "SOL", "XRP"],
};
