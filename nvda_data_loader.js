(function attachNvdaDataLoader(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.NvdaDataLoader = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function createNvdaDataLoader() {
  function parseNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(String(value).replace(/[$,%\s,]/g, ''));
    return Number.isFinite(parsed) ? parsed : null;
  }

  function parseNasdaqDate(value) {
    const match = String(value || '').match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (!match) return null;
    const [, month, day, year] = match;
    return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
  }

  function inRange(row, start, end) {
    return (!start || row.date >= start) && (!end || row.date <= end);
  }

  function cleanRows(rows, start, end) {
    return rows
      .filter(row => row.date && row.open != null && row.high != null && row.low != null && row.close != null && row.volume != null)
      .filter(row => inRange(row, start, end))
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  function parseLocalCachePayload(payload, options) {
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    return {
      source: payload.source || 'Local Cache',
      meta: payload.meta || {symbol: payload.symbol || 'NVDA'},
      rows: cleanRows(rows.map(row => ({
        date: row.date,
        open: parseNumber(row.open),
        high: parseNumber(row.high),
        low: parseNumber(row.low),
        close: parseNumber(row.close),
        volume: parseNumber(row.volume),
      })), options.start, options.end),
    };
  }

  function parseNasdaqHistoricalPayload(payload, options) {
    const table = payload && payload.data && payload.data.tradesTable;
    const rows = table && Array.isArray(table.rows) ? table.rows : [];
    return {
      source: 'Nasdaq Historical',
      meta: {symbol: (payload.data && payload.data.symbol) || 'NVDA'},
      rows: cleanRows(rows.map(row => ({
        date: parseNasdaqDate(row.date),
        open: parseNumber(row.open),
        high: parseNumber(row.high),
        low: parseNumber(row.low),
        close: parseNumber(row.close),
        volume: parseNumber(row.volume),
      })), options.start, options.end),
    };
  }

  function parseYahooChartPayload(payload, options) {
    const result = payload && payload.chart && payload.chart.result && payload.chart.result[0];
    if (!result) {
      throw new Error((payload && payload.chart && payload.chart.error && payload.chart.error.description) || 'Yahoo返回为空');
    }
    const timestamps = result.timestamp || [];
    const quote = result.indicators && result.indicators.quote && result.indicators.quote[0];
    if (!quote) throw new Error('Yahoo返回缺少OHLCV字段');
    const rows = timestamps.map((timestamp, index) => {
      const date = new Date(timestamp * 1000).toISOString().slice(0, 10);
      return {
        date,
        open: parseNumber(quote.open[index]),
        high: parseNumber(quote.high[index]),
        low: parseNumber(quote.low[index]),
        close: parseNumber(quote.close[index]),
        volume: parseNumber(quote.volume[index]),
      };
    });
    return {
      source: 'Yahoo Finance',
      meta: result.meta || {symbol: 'NVDA'},
      rows: cleanRows(rows, options.start, options.end),
    };
  }

  function parsePayload(payload, source, options) {
    if (source.type === 'localCache') return parseLocalCachePayload(payload, options);
    if (source.type === 'nasdaqHistorical') return parseNasdaqHistoricalPayload(payload, options);
    if (source.type === 'yahooChart') return parseYahooChartPayload(payload, options);
    throw new Error(`未知行情源类型：${source.type}`);
  }

  async function loadMarketData({fetchImpl, sources, start, end}) {
    const fetcher = fetchImpl || fetch;
    const errors = [];
    for (const source of sources) {
      try {
        const response = await fetcher(source.url, {cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        const parsed = parsePayload(payload, source, {start, end});
        if (!parsed.rows.length) throw new Error('未取得有效OHLCV数据');
        return {
          ...parsed,
          source: source.label || parsed.source,
          url: source.url,
        };
      } catch (error) {
        errors.push(`${source.label || source.url}: ${error.message}`);
      }
    }
    throw new Error(errors.length ? errors.join('；') : '全部行情接口均失败');
  }

  return {
    loadMarketData,
    parseLocalCachePayload,
    parseNasdaqHistoricalPayload,
    parseYahooChartPayload,
  };
});
