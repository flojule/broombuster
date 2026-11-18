"""The street tiles' loader (frontend/js/map.js cachedTileLoader, run under node): one fetch
per tile however many ask at once, aborts, and the byte-bounded cache."""

import shutil
import subprocess
from pathlib import Path

import pytest

_MAP_JS = Path(__file__).parent.parent / "frontend" / "js" / "map.js"

_SCRIPT = """
const vm = require('vm'), assert = require('assert');
const ctx = { AbortController, DOMException };
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(process.argv[1], 'utf8'), ctx);
const fetches = [];
const load = (params, ac) => new Promise((resolve, reject) => {
  fetches.push({ url: params.url, ac, resolve });
  ac.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
});
const get = vm.runInContext('cachedTileLoader', ctx)(load);
const MB = 1024 * 1024, req = url => ({ url, type: 'arrayBuffer' });
(async () => {
  // Two callers, one fetch; each gets its own copy.
  const [a, b] = [new AbortController(), new AbortController()];
  const pa = get(req('t/1'), a), pb = get(req('t/1'), b);
  assert.strictEqual(fetches.length, 1);
  fetches[0].resolve({ data: new Uint8Array(3 * MB).fill(7) });
  const [ra, rb] = await Promise.all([pa, pb]);
  assert.notStrictEqual(ra.data.buffer, rb.data.buffer);
  assert.strictEqual(rb.data[5], 7);
  // Cached: no new fetch.
  await get(req('t/1'), new AbortController());
  assert.strictEqual(fetches.length, 1);
  // One caller of two aborts: the other still gets the tile; both abort: the fetch stops.
  const [c, d] = [new AbortController(), new AbortController()];
  const pc = get(req('t/2'), c), pd = get(req('t/2'), d);
  c.abort();
  await assert.rejects(pc, { name: 'AbortError' });
  assert.strictEqual(fetches[1].ac.signal.aborted, false);
  fetches[1].resolve({ data: new Uint8Array(3 * MB) });
  await pd;
  const [e, f] = [new AbortController(), new AbortController()];
  const pe = get(req('t/3'), e), pf = get(req('t/3'), f);
  e.abort(); f.abort();
  await assert.rejects(pe, { name: 'AbortError' });
  await assert.rejects(pf, { name: 'AbortError' });
  assert.strictEqual(fetches[2].ac.signal.aborted, true);
  // Over the byte budget (8 MB): the least recently used tile goes.
  const pg = get(req('t/4'), new AbortController());
  fetches[3].resolve({ data: new Uint8Array(3 * MB) });
  await pg;
  await get(req('t/2'), new AbortController());
  assert.strictEqual(fetches.length, 4);  // kept; t/1 was the least recently used
  get(req('t/1'), new AbortController());
  assert.strictEqual(fetches.length, 5);
  console.log('ok');
})().catch(err => { console.error(err); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_tile_loader_shares_fetches_aborts_and_bounds_its_cache():
    out = subprocess.run(
        ["node", "-e", _SCRIPT, str(_MAP_JS)], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
