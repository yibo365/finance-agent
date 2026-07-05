const assert = require('node:assert/strict');
const test = require('node:test');

const {
  loadMarketData,
  parseNasdaqHistoricalPayload,
} = require('../nvda_data_loader.js');

const nasdaqPayload = {
  data: {
    symbol: 'NVDA',
    tradesTable: {
      rows: [
        {
          date: '07/02/2026',
          close: '$194.83',
          volume: '142,385,500',
          open: '$197.14',
          high: '$200.055',
          low: '$192.35',
        },
        {
          date: '07/01/2026',
          close: '$197.58',
          volume: '146,147,600',
          open: '$196.20',
          high: '$199.85',
          low: '$193.45',
        },
      ],
    },
  },
};

test('parses Nasdaq historical rows into ascending OHLCV rows', () => {
  const result = parseNasdaqHistoricalPayload(nasdaqPayload, {
    start: '2026-07-01',
    end: '2026-07-02',
  });

  assert.equal(result.meta.symbol, 'NVDA');
  assert.equal(result.source, 'Nasdaq Historical');
  assert.deepEqual(result.rows.map(row => row.date), ['2026-07-01', '2026-07-02']);
  assert.deepEqual(result.rows[0], {
    date: '2026-07-01',
    open: 196.2,
    high: 199.85,
    low: 193.45,
    close: 197.58,
    volume: 146147600,
  });
});

test('falls back to the next source when a market data source fails', async () => {
  const calls = [];
  const fetchImpl = async url => {
    calls.push(url);
    if (url === 'blocked-yahoo') {
      throw new Error('HTTP 429');
    }
    return {
      ok: true,
      json: async () => nasdaqPayload,
    };
  };

  const result = await loadMarketData({
    fetchImpl,
    sources: [
      {label: 'Yahoo', url: 'blocked-yahoo', type: 'yahooChart'},
      {label: 'Nasdaq Historical', url: 'nasdaq-cache', type: 'nasdaqHistorical'},
    ],
    start: '2026-07-01',
    end: '2026-07-02',
  });

  assert.deepEqual(calls, ['blocked-yahoo', 'nasdaq-cache']);
  assert.equal(result.source, 'Nasdaq Historical');
  assert.equal(result.rows.length, 2);
});
