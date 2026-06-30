(function () {
  const money = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
  const compactMoney = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 2,
  });
  const number = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 4,
  });

  function byId(id) {
    return document.getElementById(id);
  }

  function text(id, value) {
    const node = byId(id);
    if (node) node.textContent = value;
  }

  function safe(value, formatter = String) {
    if (value === null || value === undefined || Number.isNaN(value)) return "NO DATA";
    return formatter(value);
  }

  function fmtMoney(value) {
    return safe(value, (v) => money.format(v));
  }

  function fmtCompactMoney(value) {
    return safe(value, (v) => compactMoney.format(v));
  }

  function fmtNumber(value, digits = 2) {
    return safe(value, (v) => Number(v).toFixed(digits));
  }

  function fmtBps(value) {
    return safe(value, (v) => `${Number(v).toFixed(2)} bps`);
  }

  function fmtPct(value) {
    return safe(value, (v) => `${(Number(v) * 100).toFixed(4)}%`);
  }

  function fmtMs(value) {
    return safe(value, (v) => `${Math.round(Number(v))} MS`);
  }

  function toDate(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") {
      const millis = value < 1000000000000 ? value * 1000 : value;
      const date = new Date(millis);
      return Number.isNaN(date.getTime()) ? null : date;
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function fmtUtc(value) {
    if (!value) return "--";
    if (typeof value === "string" && /^\d{2}:\d{2}:\d{2}$/.test(value)) return value;
    const date = toDate(value);
    if (!date) return "--";
    return date.toISOString().slice(11, 19);
  }

  function fmtCountdown(ts) {
    if (!ts) return "NO DATA";
    const date = toDate(ts);
    if (!date) return "NO DATA";
    const diff = date.getTime() - Date.now();
    if (!Number.isFinite(diff)) return "NO DATA";
    if (diff <= 0) return "DUE";
    const total = Math.floor(diff / 1000);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function fmtHold(openedTs) {
    if (!openedTs) return "NO DATA";
    const date = toDate(openedTs);
    if (!date) return "NO DATA";
    const diff = Date.now() - date.getTime();
    if (!Number.isFinite(diff) || diff < 0) return "NO DATA";
    const total = Math.floor(diff / 1000);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    return `${h}H ${String(m).padStart(2, "0")}M`;
  }

  function classBySign(value) {
    if (value === null || value === undefined) return "no-data";
    if (Number(value) > 0) return "green";
    if (Number(value) < 0) return "red";
    return "muted";
  }

  function stateClass(value) {
    if (["RUNNING", "CONNECTED", "LIVE", "SIMULATION", "RISK_APPROVED", "EDGE_DETECTED", "FILLED"].includes(value)) return "good";
    if (["PAUSED", "RECONNECTING", "WAITING_FOR_FEED", "PARTIALLY_FILLED", "NO_TRADE"].includes(value)) return "warn";
    if (["KILLED", "DISCONNECTED", "RISK_REJECTED", "EDGE_LOST", "REJECTED", "CANCELED"].includes(value)) return "bad";
    return "";
  }

  function clearCanvas(canvas) {
    const ctx = canvas.getContext("2d");
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function drawNoData(canvas) {
    const { ctx, width, height } = clearCanvas(canvas);
    ctx.fillStyle = "rgba(123, 133, 131, 0.45)";
    ctx.font = "900 13px Cascadia Mono, Consolas, monospace";
    ctx.textAlign = "center";
    ctx.fillText("NO DATA", width / 2, height / 2);
  }

  function drawSeriesChart(canvas, seriesList) {
    const { ctx, width, height } = clearCanvas(canvas);
    const padding = { left: 48, right: 20, top: 24, bottom: 34 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;
    const allPoints = seriesList.flatMap((entry) => entry.points);
    if (!allPoints.length) {
      drawNoData(canvas);
      return;
    }
    const validPoints = allPoints.filter((point) => toDate(point.ts) && Number.isFinite(Number(point.value)));
    if (!validPoints.length) {
      drawNoData(canvas);
      return;
    }
    const minTs = Math.min(...validPoints.map((point) => toDate(point.ts).getTime()));
    const maxTs = Math.max(...validPoints.map((point) => toDate(point.ts).getTime()));
    const minValue = Math.min(...allPoints.map((point) => Number(point.value)));
    const maxValue = Math.max(...allPoints.map((point) => Number(point.value)));
    const valueSpan = Math.max(0.0001, maxValue - minValue);
    const timeSpan = Math.max(1, maxTs - minTs);

    ctx.save();
    ctx.strokeStyle = "rgba(99, 116, 116, 0.18)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 6]);
    for (let i = 0; i <= 4; i += 1) {
      const y = padding.top + (plotH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotW, y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.strokeStyle = "rgba(244, 175, 46, 0.42)";
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, padding.top + plotH);
    ctx.lineTo(padding.left + plotW, padding.top + plotH);
    ctx.stroke();

    ctx.fillStyle = "#7b8583";
    ctx.font = "800 10px Cascadia Mono, Consolas, monospace";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i += 1) {
      const value = maxValue - (valueSpan / 4) * i;
      const y = padding.top + (plotH / 4) * i + 3;
      ctx.fillText(value.toFixed(2), padding.left - 8, y);
    }
    ctx.restore();

    seriesList.forEach((entry) => {
      ctx.save();
      ctx.beginPath();
      entry.points.forEach((point, index) => {
        const pointDate = toDate(point.ts);
        if (!pointDate || !Number.isFinite(Number(point.value))) return;
        const ts = pointDate.getTime();
        const x = padding.left + ((ts - minTs) / timeSpan) * plotW;
        const y = padding.top + plotH - ((Number(point.value) - minValue) / valueSpan) * plotH;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = entry.color;
      ctx.lineWidth = entry.width || 2;
      if (entry.dash) ctx.setLineDash(entry.dash);
      ctx.shadowBlur = 8;
      ctx.shadowColor = entry.color;
      ctx.stroke();
      ctx.restore();
    });
  }

  window.OneWishRenderUtils = {
    byId,
    text,
    number,
    fmtMoney,
    fmtCompactMoney,
    fmtNumber,
    fmtBps,
    fmtPct,
    fmtMs,
    toDate,
    fmtUtc,
    fmtCountdown,
    fmtHold,
    classBySign,
    stateClass,
    drawSeriesChart,
    drawNoData,
  };
})();
