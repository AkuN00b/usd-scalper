"use strict";

function lineChart(canvas, formatX, formatY, baseline = false) {
  let series = [];
  let comparison = [];

  function redraw() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width < 150 || height < 80 || (!series.length && !comparison.length)) return;
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale, scale);
    const style = getComputedStyle(canvas);
    const accent = style.getPropertyValue("--accent").trim();
    const secondary = style.getPropertyValue("--compare").trim();
    const grid = style.getPropertyValue("--edge").trim();
    const muted = style.getPropertyValue("--muted").trim();

    let low = Infinity;
    let high = -Infinity;
    let minX = Infinity;
    let maxX = -Infinity;
    for (const points of [series, comparison]) {
      if (!points.length) continue;
      minX = Math.min(minX, points[0].x);
      maxX = Math.max(maxX, points[points.length - 1].x);
      for (const { y } of points) {
        low = Math.min(low, y);
        high = Math.max(high, y);
      }
    }
    const padding = (high - low) * .09 || Math.abs(high) * .001 || 1;
    low -= padding;
    high += padding;
    const top = 18;
    const bottom = height - 32;
    const left = 14;
    ctx.font = "11px system-ui, sans-serif";
    const right = width - Math.max(ctx.measureText(formatY(high)).width, ctx.measureText(formatY(low)).width) - 24;
    const toX = (x) => minX === maxX ? (left + right) / 2 : left + (x - minX) * (right - left) / (maxX - minX);
    const toY = (y) => bottom - (y - low) * (bottom - top) / (high - low);
    const zero = baseline && low < 0 && high > 0 ? toY(0) : null;
    ctx.strokeStyle = grid;
    ctx.fillStyle = muted;
    ctx.textBaseline = "middle";
    for (let index = 0; index <= 4; index += 1) {
      const value = high - (high - low) * index / 4;
      const y = toY(value);
      if (zero !== null && Math.abs(zero - y) < 14) continue;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
      ctx.fillText(formatY(value), right + 8, y);
    }
    if (zero !== null) {
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = muted;
      ctx.beginPath();
      ctx.moveTo(left, zero);
      ctx.lineTo(right, zero);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillText(formatY(0), right + 8, zero);
    }

    const labelCount = width >= 460 && maxX !== minX ? 3 : 2;
    if (minX === maxX) {
      const label = formatX(maxX);
      ctx.fillText(label, toX(maxX) - ctx.measureText(label).width / 2, bottom + 16);
    } else {
      for (let index = 0; index < labelCount; index += 1) {
        const fraction = index / (labelCount - 1);
        const label = formatX(minX + (maxX - minX) * fraction);
        ctx.fillText(label, left + (right - left - ctx.measureText(label).width) * fraction, bottom + 16);
      }
    }

    ctx.lineWidth = 2;
    ctx.lineJoin = "round";

    function plot(points, color, dash) {
      if (!points.length) return;
      ctx.beginPath();
      points.forEach(({ x, y }, index) => {
        if (index === 0) ctx.moveTo(toX(x), toY(y));
        else ctx.lineTo(toX(x), toY(y));
      });
      ctx.strokeStyle = color;
      ctx.setLineDash(dash);
      ctx.stroke();
      const last = points[points.length - 1];
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(toX(last.x), toY(last.y), 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
    plot(series, accent, []);
    plot(comparison, secondary, [5, 4]);
  }

  new ResizeObserver(redraw).observe(canvas);
  window.addEventListener("appearancechange", redraw);
  return (points, comparisonPoints = []) => {
    series = points;
    comparison = comparisonPoints;
    redraw();
  };
}
