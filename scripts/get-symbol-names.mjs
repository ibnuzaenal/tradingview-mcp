#!/usr/bin/env node
import { chart, health } from '../src/core/index.js';

try { await health.healthCheck(); } catch { await health.launch({ kill_existing: true }); await new Promise(r => setTimeout(r, 8000)); }

const symbols = process.argv[2].split(',');
console.error(`Fetching names for ${symbols.length} symbols.`);

const out = [];
for (const sym of symbols) {
  try {
    await chart.setSymbol({ symbol: sym });
    await new Promise((r) => setTimeout(r, 350));
    const info = await chart.symbolInfo();
    out.push({ symbol: sym, name: info.description || info.full_name });
    console.error(`${sym} -> ${info.description}`);
  } catch (err) {
    out.push({ symbol: sym, name: null, error: err.message });
    console.error(`${sym} -> FAILED: ${err.message}`);
  }
}
process.stdout.write(JSON.stringify(out, null, 2));
