# One Wish GUI Data Contract

GUI jest tylko warstwą renderującą. Nie liczy sygnałów, nie podejmuje decyzji tradingowych i nie modyfikuje pozycji lokalnie. Źródłem prawdy jest backend Python wysyłający wiadomości WebSocket JSON z polem `type`.

## Przełączanie adaptera

W pliku `js/config.js`:

```js
adapter: "mock" // tryb domyślny, widoczny baner SIMULATION
```

Zmiana na live:

```js
adapter: "ws"
wsUrl: "ws://127.0.0.1:8765/gui"
```

`mockAdapter.js` emituje ten sam schemat co backend i służy wyłącznie do uruchomienia GUI bez serwera. Tryb mock zawsze pokazuje baner `SIMULATION - MOCK DATA ONLY - NOT LIVE TRADING`.

## Wiadomości backend -> GUI

```js
status: {
  type, botState, connection, latencyMs, serverTimeUtc
}

market: {
  type, asset, spot, perp, index, basisBps, fundingRate,
  predictedFunding, nextFundingTs, dataLagMs
}

signal: {
  type, asset, fairBasisBps, observedBasisBps, dislocationBps,
  expectedNetEdge, state, reason,
  series: [{ ts, fairBasisBps, observedBasisBps }] // opcjonalne, używane przez wykres
}

position: {
  type, id, asset, spotQty, spotEntry, perpQty, perpEntry,
  netDelta, unrealizedPnl, fundingAccrued, marginRatio, openedTs
}

order: {
  type, id, asset, leg, side, orderType, price, qty, status
}

fill: {
  type, orderId, asset, leg, price, qty, fee, ts
}

pnl: {
  type, realized, unrealized, fundingCollected, feesPaid, net,
  series: [{ ts, value }]
}

risk: {
  type,
  dailyLoss: { used, limit },
  perAssetExposure: [{ asset, used, limit }],
  openPositions: { used, limit },
  marginBuffer,
  lastDecision,
  reason
}

event: {
  type, ts, eventType, severity, message, data
}
```

## Komendy GUI -> backend

GUI wysyła tylko jawne komendy operatora. Przykład Kill Switch:

```js
{ type: "command", action: "kill", ts, source: "gui" }
```

Backend decyduje, co zrobić z komendą. Frontend nie wykonuje flatten, pause, resume ani żadnej decyzji tradingowej samodzielnie.
