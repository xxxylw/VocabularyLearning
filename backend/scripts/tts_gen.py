#!/usr/bin/env python3
"""Generate TTS audio (edge-tts) for open_dictionary_v3 words lacking other audio.

Reads word list (one normalized word per line); generates mp3 to <out>/<slug>.mp3
using voice en-US-AriaNeural. Skips words that already have a non-empty mp3.
Resumable: writes progress.jsonl, one record per word.
"""
from __future__ import annotations
import argparse, asyncio, json, os, re, sys, time

# Ensure edge-tts is importable when installed under /home/gem/.aily/.cli/python/edgetts
for _p in ("/home/gem/.aily/.cli/python/edgetts", "/home/gem/.aily/.cli/python/edgetts-cmd"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

SLUG_RE = re.compile(r"[^a-z0-9._-]")
VOICE = "en-US-AriaNeural"

def slug(word: str) -> str:
    return SLUG_RE.sub("_", word.strip().lower()).strip("_")[:120] or "_"


async def synth_one(word: str, out_path: str, sem: asyncio.Semaphore) -> str:
    import edge_tts
    async with sem:
        try:
            comm = edge_tts.Communicate(word, voice=VOICE)
            await comm.save(out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 200:
                return "ok"
            return "empty"
        except Exception as e:
            print(f"err {word}: {e!r}", flush=True)
            return "error"


async def main(words_path: str, out_dir: str, conc: int) -> None:
    os.makedirs(out_dir, exist_ok=True)
    prog = os.path.join(out_dir, "_tts_progress.jsonl")
    done: set[str] = set()
    if os.path.exists(prog):
        with open(prog, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["word"])
                except Exception:
                    pass
    with open(words_path, encoding="utf-8") as f:
        todo = [l.strip() for l in f if l.strip() and l.strip() not in done]
    print(f"tts_total={len(todo)} done={len(done)}", flush=True)
    sem = asyncio.Semaphore(conc)
    pf = open(prog, "a", encoding="utf-8")
    t0 = time.time()
    stats = {"ok": 0, "error": 0, "empty": 0}
    for i in range(0, len(todo), conc * 4):
        chunk = todo[i:i + conc * 4]
        results = await asyncio.gather(*(synth_one(w, os.path.join(out_dir, slug(w) + ".mp3"), sem) for w in chunk))
        for w, r in zip(chunk, results):
            stats[r] = stats.get(r, 0) + 1
            pf.write(json.dumps({"word": w, "status": r, "ts": time.time()}) + "\n")
        pf.flush()
        elapsed = int(time.time() - t0)
        print(f"tts_progress {i + len(chunk)}/{len(todo)} {stats} elapsed={elapsed}s", flush=True)
    pf.close()
    print(f"RUN_END {stats}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conc", type=int, default=5)
    a = ap.parse_args()
    asyncio.run(main(a.words, a.out, a.conc))
