(function () {
  const assets = ["BTC", "ETH", "SOL", "XRP"];
  const baseTime = Date.UTC(2026, 5, 29, 8, 0, 0);

  const marketFrames = [
    {
      BTC: [60478.2, 60511.4, 60476.9, 5.48, 0.000084, 0.000079, 52],
      ETH: [1598.5, 1599.72, 1598.44, 8.01, 0.000102, 0.000097, 61],
      SOL: [71.08, 71.15, 71.07, 11.26, 0.000118, 0.000111, 73],
      XRP: [1.066, 1.0667, 1.0659, 7.51, 0.000061, 0.000058, 67],
    },
    {
      BTC: [60482.8, 60509.6, 60480.7, 4.78, 0.000082, 0.000076, 49],
      ETH: [1599.1, 1600.04, 1599.0, 6.50, 0.000101, 0.000096, 57],
      SOL: [71.12, 71.20, 71.1, 14.06, 0.000121, 0.000112, 69],
      XRP: [1.0658, 1.0661, 1.0657, 3.75, 0.000059, 0.000057, 65],
    },
    {
      BTC: [60491.0, 60519.2, 60490.3, 4.78, 0.00008, 0.000074, 54],
      ETH: [1600.2, 1601.88, 1600.08, 11.25, 0.000104, 0.000099, 63],
      SOL: [71.2, 71.28, 71.19, 12.64, 0.00012, 0.000115, 71],
      XRP: [1.0662, 1.0669, 1.0661, 7.50, 0.00006, 0.000058, 66],
    },
    {
      BTC: [60484.5, 60530.8, 60483.2, 7.87, 0.000086, 0.000083, 58],
      ETH: [1599.8, 1600.92, 1599.7, 7.63, 0.0001, 0.000095, 64],
      SOL: [71.16, 71.22, 71.15, 9.84, 0.000117, 0.000109, 72],
      XRP: [1.0659, 1.0665, 1.0658, 6.57, 0.000061, 0.000059, 68],
    },
  ];

  const signalFrames = [
    {
      BTC: [4.1, 5.48, 1.38, 18.4, "NO_TRADE", "Edge below min_edge after fees"],
      ETH: [6.0, 8.01, 2.01, 31.2, "EDGE_DETECTED", "Observed basis wider than fair basis; funding positive"],
      SOL: [8.8, 11.26, 2.46, 42.7, "EDGE_DETECTED", "Basis dislocation survives estimated round-trip costs"],
      XRP: [7.2, 7.51, 0.31, 3.6, "NO_TRADE", "Dislocation too small"],
    },
    {
      BTC: [4.4, 4.78, 0.38, 2.4, "NO_TRADE", "No actionable dislocation"],
      ETH: [6.2, 6.5, 0.3, 5.8, "EDGE_LOST", "Basis converged below entry threshold"],
      SOL: [8.6, 14.06, 5.46, 79.5, "EDGE_DETECTED", "Perp lag remains wide versus fair basis"],
      XRP: [6.0, 3.75, -2.25, -11.2, "NO_TRADE", "Funding positive but basis does not compensate costs"],
    },
    {
      BTC: [4.2, 4.78, 0.58, 7.1, "NO_TRADE", "Waiting for stronger net edge"],
      ETH: [6.1, 11.25, 5.15, 74.3, "EDGE_DETECTED", "Repricing detector confirms post-spot move lag"],
      SOL: [8.9, 12.64, 3.74, 51.8, "EDGE_DETECTED", "Liquidity and lag checks pass"],
      XRP: [6.5, 7.5, 1.0, 10.2, "NO_TRADE", "Risk manager would reject duplicate exposure"],
    },
    {
      BTC: [4.7, 7.87, 3.17, 45.0, "EDGE_DETECTED", "Basis dislocation detected with positive funding"],
      ETH: [6.1, 7.63, 1.53, 21.4, "NO_TRADE", "Net edge below configured reserve"],
      SOL: [8.7, 9.84, 1.14, 13.9, "NO_TRADE", "Dislocation fading"],
      XRP: [6.4, 6.57, 0.17, 1.2, "NO_TRADE", "No repricing edge"],
    },
  ];

  const pnlFrames = [
    [128.5, 42.1, 19.6, 11.4, 178.8],
    [128.5, 57.9, 20.3, 11.8, 194.9],
    [128.5, 31.2, 21.1, 12.2, 168.6],
    [128.5, 76.4, 21.9, 12.6, 214.2],
  ];

  function ts(offsetMinutes, frame = 0) {
    return new Date(baseTime + offsetMinutes * 60000 + frame * 30000).toISOString();
  }

  function nextFunding(frame) {
    return new Date(baseTime + 8 * 3600000 + frame * 30000).toISOString();
  }

  function buildPnlSeries(frame) {
    const values = [102.4, 109.2, 117.0, 114.8, 126.3, 131.9, 128.7, 146.5, 151.2, 168.6, 178.8, 194.9, 168.6, 214.2];
    return values.slice(frame, frame + 10).map((value, index) => ({
      ts: ts(index * 5, frame),
      value,
    }));
  }

  function buildFairSeries(asset, frame) {
    const base = {
      BTC: [[3.8, 4.3], [4.0, 4.8], [4.1, 5.5], [4.3, 4.9], [4.4, 7.9]],
      ETH: [[5.8, 7.4], [6.0, 8.0], [6.2, 6.5], [6.1, 11.3], [6.2, 7.6]],
      SOL: [[8.2, 10.1], [8.8, 11.3], [8.6, 14.1], [8.9, 12.6], [8.7, 9.8]],
      XRP: [[6.0, 6.8], [7.2, 7.5], [6.0, 3.8], [6.5, 7.5], [6.4, 6.6]],
    }[asset];
    return base.map((pair, index) => ({
      ts: ts(index * 3, frame),
      fairBasisBps: pair[0],
      observedBasisBps: pair[1],
    }));
  }

  class MockAdapter {
    constructor() {
      this.listeners = new Set();
      this.frame = 0;
      this.timers = [];
      this.connected = false;
    }

    connect() {
      this.connected = true;
      this.emitFrame();
      this.timers.push(setInterval(() => this.emitFrame(), 1800));
      return Promise.resolve();
    }

    disconnect() {
      this.connected = false;
      this.timers.forEach((timer) => clearInterval(timer));
      this.timers = [];
    }

    onMessage(listener) {
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    sendCommand(command) {
      this.emit({
        type: "event",
        ts: new Date().toISOString(),
        eventType: "OPERATOR_COMMAND",
        severity: command.action === "kill" ? "CRITICAL" : "INFO",
        message: `SIMULATION command sent: ${command.action}`,
        data: command,
      });
      if (command.action === "kill") {
        this.emit({
          type: "status",
          botState: "KILLED",
          connection: "SIMULATION",
          latencyMs: 7,
          serverTimeUtc: new Date().toISOString(),
        });
      }
    }

    emit(message) {
      this.listeners.forEach((listener) => listener(message));
    }

    emitFrame() {
      const frame = this.frame % marketFrames.length;
      const market = marketFrames[frame];
      const signal = signalFrames[frame];
      const pnl = pnlFrames[frame];

      this.emit({
        type: "status",
        botState: "RUNNING",
        connection: "SIMULATION",
        latencyMs: [8, 7, 9, 8][frame],
        serverTimeUtc: new Date().toISOString(),
      });

      assets.forEach((asset) => {
        const row = market[asset];
        this.emit({
          type: "market",
          asset,
          spot: row[0],
          perp: row[1],
          index: row[2],
          basisBps: row[3],
          fundingRate: row[4],
          predictedFunding: row[5],
          nextFundingTs: nextFunding(frame),
          dataLagMs: row[6],
        });

        const sig = signal[asset];
        this.emit({
          type: "signal",
          asset,
          fairBasisBps: sig[0],
          observedBasisBps: sig[1],
          dislocationBps: sig[2],
          expectedNetEdge: sig[3],
          state: sig[4],
          reason: sig[5],
          series: buildFairSeries(asset, frame),
        });
      });

      this.emit({
        type: "position",
        id: "SIM-ETH-001",
        asset: "ETH",
        spotQty: 1.25,
        spotEntry: 1594.3,
        perpQty: -1.25,
        perpEntry: 1601.1,
        netDelta: 0.002,
        unrealizedPnl: frame === 2 ? 18.4 : 34.7 + frame * 4,
        fundingAccrued: 8.2 + frame * 0.7,
        marginRatio: 0.42,
        openedTs: ts(-96, frame),
      });

      this.emit({
        type: "position",
        id: "SIM-SOL-002",
        asset: "SOL",
        spotQty: 42,
        spotEntry: 70.82,
        perpQty: -42,
        perpEntry: 71.18,
        netDelta: -0.004,
        unrealizedPnl: 13.6 + frame * 1.9,
        fundingAccrued: 3.1 + frame * 0.4,
        marginRatio: 0.51,
        openedTs: ts(-48, frame),
      });

      const orderStatus = ["NEW", "PARTIALLY_FILLED", "FILLED", "FILLED"][frame];
      this.emit({
        type: "order",
        id: `SIM-ORD-${100 + frame}`,
        asset: assets[frame],
        leg: frame % 2 === 0 ? "PERP" : "SPOT",
        side: frame % 2 === 0 ? "SELL" : "BUY",
        orderType: "LIMIT",
        price: market[assets[frame]][frame % 2 === 0 ? 1 : 0],
        qty: frame % 2 === 0 ? 0.08 : 1.2,
        status: orderStatus,
      });

      if (frame > 0) {
        this.emit({
          type: "fill",
          orderId: `SIM-ORD-${99 + frame}`,
          asset: assets[frame - 1],
          leg: "PERP",
          price: market[assets[frame - 1]][1],
          qty: 0.08,
          fee: 0.18 + frame * 0.03,
          ts: new Date().toISOString(),
        });
      }

      this.emit({
        type: "pnl",
        realized: pnl[0],
        unrealized: pnl[1],
        fundingCollected: pnl[2],
        feesPaid: pnl[3],
        net: pnl[4],
        series: buildPnlSeries(frame),
      });

      this.emit({
        type: "risk",
        dailyLoss: { used: frame === 2 ? 142 : 86, limit: 500 },
        perAssetExposure: [
          { asset: "BTC", used: 1800, limit: 5000 },
          { asset: "ETH", used: 2400, limit: 5000 },
          { asset: "SOL", used: 1250, limit: 3000 },
          { asset: "XRP", used: 280, limit: 1500 },
        ],
        openPositions: { used: 2, limit: 6 },
        marginBuffer: frame === 2 ? 0.34 : 0.42,
        lastDecision: frame === 2 ? "RISK_REJECTED" : "RISK_APPROVED",
        reason: frame === 2 ? "ETH duplicate exposure blocked by policy" : "All configured limits inside thresholds",
      });

      this.emit({
        type: "event",
        ts: new Date().toISOString(),
        eventType: signal[assets[frame]][4] === "EDGE_DETECTED" ? "EDGE_DETECTED" : "NO_TRADE_CONDITION",
        severity: signal[assets[frame]][4] === "EDGE_DETECTED" ? "INFO" : "DEBUG",
        message: `${assets[frame]} ${signal[assets[frame]][5]}`,
        data: { asset: assets[frame], simulation: true },
      });

      this.frame += 1;
    }
  }

  window.OneWishAdapters = window.OneWishAdapters || {};
  window.OneWishAdapters.MockAdapter = MockAdapter;
})();
