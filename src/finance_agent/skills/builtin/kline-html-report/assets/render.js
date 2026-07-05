/**
 * kline-html-report 渲染骨架（收编自早期原型，改造点：
 * 1) 数据不再 fetch——从内嵌的 window.__REPORT_PAYLOAD__ 读取，file:// 断网可开；
 * 2) 新增确定性变化点（changepoints）标记 trace；
 * 3) 纯函数经 UMD 导出，node --test 可直接单测。）
 */
(function attachKlineReport(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.KlineReport = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function createKlineReport() {
  const COLORS = { up: '#16c784', down: '#ff5c72', mixed: '#f8b84e', neutral: '#5aa8ff' };
  const SYMBOLS = { up: 'triangle-up', down: 'triangle-down', mixed: 'diamond', neutral: 'circle' };
  const CP_LABELS = {
    trend_up: '趋势拐头向上', trend_down: '趋势拐头向下',
    accel_up: '加速上涨', accel_down: '加速下跌',
    drawdown: '回撤确认', rally: '反弹确认', volume_spike: '量能异常',
  };
  const CP_COLORS = {
    trend_up: '#16c784', rally: '#16c784', accel_up: '#16c784',
    trend_down: '#ff5c72', drawdown: '#ff5c72', accel_down: '#ff5c72',
    volume_spike: '#5aa8ff',
  };

  const fmtPct = v => (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v))
    ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  const fmtUSD = v => (v === null || v === undefined || Number.isNaN(v))
    ? '—' : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const fmtVol = v => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toLocaleString();
  const parseUTC = s => new Date(s + 'T00:00:00Z');

  function escapeHtml(str) {
    return String(str).replace(/[&<>'"]/g, ch => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]
    ));
  }

  function movingAverage(values, window) {
    const out = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= window) sum -= values[i - window];
      if (i >= window - 1) out[i] = sum / window;
    }
    return out;
  }

  function maxDrawdown(closes, dates) {
    // peakDate 必须是"最大回撤发生时的峰值日"，不能被后续新高覆盖
    // （原型版的 bug：peakDate 总是全序列最后的峰值）。
    let peak = -Infinity, curPeakDate = null;
    let peakDate = null, troughDate = null, maxDD = 0;
    for (let i = 0; i < closes.length; i++) {
      const c = closes[i];
      if (c > peak) { peak = c; curPeakDate = dates[i]; }
      const dd = (c / peak - 1) * 100;
      if (dd < maxDD) { maxDD = dd; troughDate = dates[i]; peakDate = curPeakDate; }
    }
    return { maxDD, peakDate: peakDate || curPeakDate, troughDate };
  }

  /** 事件日 → 之后第一个交易日；并计算事件后 5/20 交易日收益。 */
  function mapEventsToTradingDays(rows, events) {
    return events.map(e => {
      const target = parseUTC(e.date).getTime();
      let idx = rows.length - 1;
      for (let i = 0; i < rows.length; i++) {
        if (parseUTC(rows[i].date).getTime() >= target) { idx = i; break; }
      }
      const row = rows[idx];
      const idx5 = Math.min(idx + 5, rows.length - 1);
      const idx20 = Math.min(idx + 20, rows.length - 1);
      const ret5 = idx5 > idx ? (rows[idx5].close / row.close - 1) * 100 : null;
      const ret20 = idx20 > idx ? (rows[idx20].close / row.close - 1) * 100 : null;
      const lagDays = Math.round((parseUTC(row.date).getTime() - target) / 86400000);
      return { ...e, tradingDate: row.date, idx, close: row.close, ret5, ret20, lagDays };
    });
  }

  function filterEvents(events, criteria) {
    const q = (criteria.q || '').trim().toLowerCase();
    const cat = criteria.category || 'all';
    const minImpact = Number(criteria.minImpact || 1);
    const dir = criteria.direction || 'all';
    return events.filter(e => {
      const hay = [e.date, e.title, e.category, e.move, e.notes,
        ...(e.sources || []).map(s => s.name)].join(' ').toLowerCase();
      return (!q || hay.includes(q))
        && (cat === 'all' || e.category === cat)
        && e.impact >= minImpact
        && (dir === 'all' || e.direction === dir);
    });
  }

  function buildEventTrace(events) {
    return {
      type: 'scatter', mode: 'markers+text', name: '事件标注',
      x: events.map(e => e.tradingDate),
      y: events.map(e => e.close),
      yaxis: 'y',
      text: events.map(e => e.impact >= 4 ? `★${e.impact}` : `${e.impact}`),
      textposition: 'top center',
      marker: {
        size: events.map(e => 9 + e.impact * 3.2),
        color: events.map(e => COLORS[e.direction] || COLORS.neutral),
        symbol: events.map(e => SYMBOLS[e.direction] || SYMBOLS.neutral),
        line: { width: 1.4, color: '#ffffff' },
      },
      customdata: events.map(e => [
        e.title, e.category, e.move, e.impact, e.date, e.tradingDate,
        fmtPct(e.ret5), fmtPct(e.ret20), e.notes,
        (e.sources || []).map(s => s.name).join(' / '),
      ]),
      hovertemplate:
        '<b>%{customdata[0]}</b><br>事件日：%{customdata[4]}<br>映射交易日：%{customdata[5]}<br>'
        + '收盘价：%{y:.2f}<br>类型：%{customdata[1]}<br>行情变化：%{customdata[2]}<br>'
        + '影响评级：%{customdata[3]} / 5<br>后5日：%{customdata[6]}｜后20日：%{customdata[7]}<br>'
        + '说明：%{customdata[8]}<br>来源：%{customdata[9]}<extra></extra>',
    };
  }

  /** 变化点标记：×（低价下方），hover 展示触发规则与数据窗口。 */
  function buildChangepointTrace(rows, changepoints) {
    const lowByDate = Object.fromEntries(rows.map(r => [r.date, r.low]));
    const points = changepoints.filter(cp => lowByDate[cp.date] !== undefined);
    return {
      type: 'scatter', mode: 'markers', name: '变化点（规则触发）',
      x: points.map(cp => cp.date),
      y: points.map(cp => lowByDate[cp.date] * 0.96),
      yaxis: 'y',
      marker: {
        size: points.map(cp => 7 + cp.severity * 3),
        color: points.map(cp => CP_COLORS[cp.kind] || COLORS.neutral),
        symbol: 'x',
        line: { width: 1, color: '#ffffff' },
      },
      customdata: points.map(cp => [
        CP_LABELS[cp.kind] || cp.kind, cp.rule, cp.severity,
        `${cp.window[0]} → ${cp.window[1]}`,
      ]),
      hovertemplate:
        '<b>%{customdata[0]}</b>（严重度 %{customdata[2]}/3）<br>'
        + '触发：%{customdata[1]}<br>数据窗口：%{customdata[3]}<extra></extra>',
    };
  }

  function makeEventShapes(events) {
    return events.map(e => ({
      type: 'line', xref: 'x', yref: 'paper',
      x0: e.tradingDate, x1: e.tradingDate, y0: 0.23, y1: 1,
      line: {
        color: COLORS[e.direction] || COLORS.neutral,
        width: e.impact >= 5 ? 1.35 : 0.85,
        dash: e.impact >= 5 ? 'solid' : 'dot',
      },
      opacity: 0.33,
    }));
  }

  function eventsToCsv(events) {
    const header = ['event_date', 'mapped_trading_date', 'title', 'category', 'direction',
      'move', 'impact', 'close', 'ret_5d_pct', 'ret_20d_pct', 'notes', 'sources'];
    const lines = [header.join(',')];
    for (const e of events) {
      const vals = [e.date, e.tradingDate, e.title, e.category, e.direction, e.move, e.impact,
        e.close != null ? e.close.toFixed(4) : '', e.ret5 != null ? e.ret5.toFixed(4) : '',
        e.ret20 != null ? e.ret20.toFixed(4) : '', e.notes,
        (e.sources || []).map(s => `${s.name} ${s.url}`).join(' | ')];
      lines.push(vals.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','));
    }
    return lines.join(String.fromCharCode(10));
  }

  // ---------- 以下为浏览器侧 DOM 装配（node 测试不触达） ----------

  function renderTable(doc, events) {
    const tbody = doc.querySelector('#eventTable tbody');
    tbody.innerHTML = events.map(e => {
      const dirClass = e.direction === 'up' ? 'up' : e.direction === 'down' ? 'down' : 'mixed';
      const links = (e.sources || []).map(s =>
        `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a>`,
      ).join('<br>');
      const evRefs = (e.evidence_refs || []).map(id =>
        `<a class="badge" href="#${escapeHtml(id)}">${escapeHtml(id)}</a>`,
      ).join(' ');
      return `<tr>
        <td>${escapeHtml(e.date)}</td>
        <td>${escapeHtml(e.tradingDate)}${e.lagDays ? `<br><span class="badge">+${e.lagDays}天</span>` : ''}</td>
        <td><strong>${escapeHtml(e.title)}</strong><br><span class="${dirClass}">${escapeHtml(e.move)}</span><br><span style="color:var(--muted)">${escapeHtml(e.notes)}</span></td>
        <td><span class="badge">${escapeHtml(e.category)}</span></td>
        <td class="impact ${dirClass}">${e.impact}/5</td>
        <td class="${(e.ret5 || 0) >= 0 ? 'up' : 'down'}">${fmtPct(e.ret5)}</td>
        <td class="${(e.ret20 || 0) >= 0 ? 'up' : 'down'}">${fmtPct(e.ret20)}</td>
        <td>${links}${evRefs ? `<br>${evRefs}` : ''}</td>
      </tr>`;
    }).join('');
  }

  function currentCriteria(doc) {
    return {
      q: doc.getElementById('searchBox').value,
      category: doc.getElementById('categoryFilter').value,
      minImpact: doc.getElementById('impactFilter').value,
      direction: doc.getElementById('directionFilter').value,
    };
  }

  function init(payload, win) {
    const w = win || window;
    const doc = w.document;
    const Plotly = w.Plotly;
    const rows = payload.rows;
    const allEvents = mapEventsToTradingDays(rows, payload.events || []);
    const changepoints = payload.changepoints || [];
    const eventTraceIndex = 4;

    const dates = rows.map(r => r.date);
    const closes = rows.map(r => r.close);
    const first = rows[0], last = rows[rows.length - 1];
    const dd = maxDrawdown(closes, dates);
    doc.getElementById('rangeCard').textContent = `${first.date} → ${last.date}`;
    doc.getElementById('rangeSub').textContent = `${rows.length.toLocaleString()} 个交易日，含 OHLCV`;
    doc.getElementById('lastClose').textContent = fmtUSD(last.close);
    doc.getElementById('lastSub').textContent = `${last.date}｜成交量 ${fmtVol(last.volume)}`;
    doc.getElementById('periodRet').textContent = fmtPct((last.close / first.close - 1) * 100);
    doc.getElementById('maxDD').textContent = fmtPct(dd.maxDD);
    doc.getElementById('maxDDSub').textContent = `峰值日 ${dd.peakDate || '—'} → 谷值日 ${dd.troughDate || '—'}`;

    const candle = {
      type: 'candlestick', name: `${payload.meta.ticker} OHLC`,
      x: dates, open: rows.map(r => r.open), high: rows.map(r => r.high),
      low: rows.map(r => r.low), close: closes,
      increasing: { line: { color: COLORS.up }, fillcolor: COLORS.up },
      decreasing: { line: { color: COLORS.down }, fillcolor: COLORS.down },
      hoverlabel: { bgcolor: '#101828' },
      hovertemplate: `<b>%{x}</b><br>开盘：%{open:.2f}<br>最高：%{high:.2f}<br>最低：%{low:.2f}<br>收盘：%{close:.2f}<extra>${escapeHtml(payload.meta.ticker)}</extra>`,
    };
    const vol = {
      type: 'bar', name: '成交量', x: dates, y: rows.map(r => r.volume), yaxis: 'y2', opacity: 0.38,
      marker: { color: closes.map((c, i) => i === 0 || c >= closes[i - 1] ? COLORS.up : COLORS.down) },
      hovertemplate: '<b>%{x}</b><br>成交量：%{y:,}<extra></extra>',
    };
    const ma50 = { type: 'scatter', mode: 'lines', name: 'MA50', x: dates, y: movingAverage(closes, 50), line: { width: 1.4, color: '#ffd166' }, hovertemplate: '%{x}<br>MA50：%{y:.2f}<extra></extra>' };
    const ma200 = { type: 'scatter', mode: 'lines', name: 'MA200', x: dates, y: movingAverage(closes, 200), line: { width: 1.4, color: '#8ecae6' }, hovertemplate: '%{x}<br>MA200：%{y:.2f}<extra></extra>' };

    const layout = {
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      margin: { l: 64, r: 38, t: 32, b: 42 },
      font: { color: '#e9f0ff', family: '-apple-system,BlinkMacSystemFont,Segoe UI,Arial' },
      legend: { orientation: 'h', y: 1.06, x: 0, bgcolor: 'rgba(0,0,0,0)' },
      xaxis: {
        rangeslider: { visible: false }, type: 'date', showgrid: true, gridcolor: 'rgba(255,255,255,0.08)',
        rangeselector: {
          x: 0, y: 1.14, bgcolor: '#17223a', activecolor: '#2a63e8', font: { color: '#e9f0ff' },
          buttons: [
            { count: 6, label: '6M', step: 'month', stepmode: 'backward' },
            { count: 1, label: '1Y', step: 'year', stepmode: 'backward' },
            { count: 2, label: '2Y', step: 'year', stepmode: 'backward' },
            { step: 'all', label: '全部' },
          ],
        },
      },
      yaxis: { domain: [0.27, 1], title: '价格（USD）', showgrid: true, gridcolor: 'rgba(255,255,255,0.08)', zeroline: false },
      yaxis2: { domain: [0, 0.19], title: '成交量', showgrid: true, gridcolor: 'rgba(255,255,255,0.06)', zeroline: false },
      hovermode: 'x unified',
      shapes: makeEventShapes(allEvents),
    };
    const config = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };
    Plotly.newPlot('chart', [candle, vol, ma50, ma200, buildEventTrace(allEvents), buildChangepointTrace(rows, changepoints)], layout, config);
    renderTable(doc, allEvents);

    const refresh = () => {
      const evs = filterEvents(allEvents, currentCriteria(doc));
      Plotly.restyle('chart', {
        x: [evs.map(e => e.tradingDate)],
        y: [evs.map(e => e.close)],
        text: [evs.map(e => e.impact >= 4 ? `★${e.impact}` : `${e.impact}`)],
        'marker.size': [evs.map(e => 9 + e.impact * 3.2)],
        'marker.color': [evs.map(e => COLORS[e.direction] || COLORS.neutral)],
        'marker.symbol': [evs.map(e => SYMBOLS[e.direction] || SYMBOLS.neutral)],
        customdata: [evs.map(e => [e.title, e.category, e.move, e.impact, e.date, e.tradingDate, fmtPct(e.ret5), fmtPct(e.ret20), e.notes, (e.sources || []).map(s => s.name).join(' / ')])],
      }, [eventTraceIndex]);
      Plotly.relayout('chart', { shapes: makeEventShapes(evs) });
      renderTable(doc, evs);
      doc.getElementById('statusLine').innerHTML =
        `<strong>已显示 ${evs.length} / ${allEvents.length} 个事件</strong>，${changepoints.length} 个规则变化点。拖动缩放K线，悬停标记查看明细。`;
    };

    const categories = Array.from(new Set(allEvents.map(e => e.category))).sort();
    const catSel = doc.getElementById('categoryFilter');
    categories.forEach(c => {
      const opt = doc.createElement('option');
      opt.value = c; opt.textContent = c; catSel.appendChild(opt);
    });
    ['searchBox', 'categoryFilter', 'impactFilter', 'directionFilter'].forEach(id => {
      doc.getElementById(id).addEventListener('input', refresh);
      doc.getElementById(id).addEventListener('change', refresh);
    });
    doc.getElementById('resetBtn').addEventListener('click', () => {
      doc.getElementById('searchBox').value = '';
      doc.getElementById('categoryFilter').value = 'all';
      doc.getElementById('impactFilter').value = '1';
      doc.getElementById('directionFilter').value = 'all';
      refresh();
    });
    doc.getElementById('csvBtn').addEventListener('click', () => {
      const csv = eventsToCsv(filterEvents(allEvents, currentCriteria(doc)));
      const blob = new w.Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const a = doc.createElement('a');
      a.href = w.URL.createObjectURL(blob);
      a.download = `${payload.meta.ticker.toLowerCase()}_events.csv`;
      a.click();
      w.URL.revokeObjectURL(a.href);
    });
    refresh();
  }

  return {
    escapeHtml, fmtPct, fmtUSD, movingAverage, maxDrawdown,
    mapEventsToTradingDays, filterEvents, buildEventTrace, buildChangepointTrace,
    makeEventShapes, eventsToCsv, init,
    COLORS, CP_LABELS,
  };
});
