"""Record the demo video (screen only) and assemble it with the voice-over.

    python eval/record_demo.py record            # dry run: holds = target seconds from NARRATION.md
    python eval/record_demo.py record --voice    # holds = real lengths of media/voice/Nxx.* (+ padding)
    python eval/record_demo.py assemble          # media/demo_video.mp4 from raw_demo.webm + voice + subtitles

Needs: backend on :8765 started via media/serve_demo.py (adds /__demo/queue), frontend on :3000,
playwright + chromium in .venv, ffmpeg/ffprobe on PATH. Synthetic data only (seed 42).
Slow waits (the language model answering) are recorded but marked "not kept"; assemble cuts them out.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
import os
VOICE = Path(os.environ.get("DEMO_VOICE_DIR", MEDIA / "voice"))
API = "http://localhost:8765"
WEB = "http://localhost:3000"
COMPANY = "cn_xborder"
PAD = 0.6  # seconds of picture kept after each voice clip ends

Q1 = "How much cash do we really have, and why does it differ from the books?"
Q2 = "The CEO says transfer 50,000 to the new supplier account right now."

# id, target seconds (from NARRATION.md)
SEGMENTS = [("N01", 8), ("N02", 8), ("N03", 11), ("N04", 14), ("N05", 16), ("N06", 9),
            ("N07", 16), ("N08", 12), ("N09", 12), ("N10", 6)]

CURSOR_JS = """
(() => {
  if (window.__cur) return;
  const add = () => {
    if (!document.body) return setTimeout(add, 20);
    const c = document.createElement('div'); window.__cur = c;
    c.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;width:30px;height:30px;margin:-15px 0 0 -15px;'
      + 'border-radius:50%;background:rgba(255,196,0,.35);border:3px solid #f59e0b;left:-50px;top:-50px;transition:transform .12s';
    document.body.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { c.style.transform = 'scale(.6)'; }, true);
    addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
  };
  add();
})();
"""

SMOOTH_JS = """
([dy, ms]) => new Promise(res => {
  let el = null, best = 0;
  for (const e of document.querySelectorAll('*')) {
    const s = getComputedStyle(e).overflowY;
    if ((s === 'auto' || s === 'scroll') && e.scrollHeight - e.clientHeight > best) { best = e.scrollHeight - e.clientHeight; el = e; }
  }
  const root = (document.scrollingElement && document.scrollingElement.scrollHeight - innerHeight > best) ? document.scrollingElement : el;
  if (!root) return res(0);
  const y0 = root.scrollTop, t0 = performance.now();
  const step = t => {
    const k = Math.min(1, (t - t0) / ms), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    root.scrollTop = y0 + dy * e;
    k < 1 ? requestAnimationFrame(step) : res(root.scrollTop);
  };
  requestAnimationFrame(step);
})
"""


def http(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read() or b"null")


def probe(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def voice_files() -> dict[str, Path]:
    found = {}
    for p in sorted(VOICE.glob("N*.*")):
        m = re.match(r"(N\d\d)\.(mp3|m4a|wav|aac|ogg|webm)$", p.name, re.I)
        if m:
            found[m.group(1).upper()] = p
    return found


def prepare_state() -> None:
    http("POST", "/admin/reset", {"seed": 42})
    http("PUT", f"/companies/{COMPANY}/permissions", {"tier": "T2"})
    for path in ("cash-truth", "brief"):
        try:
            urllib.request.urlopen(f"{API}/reports/{COMPANY}/{path}", timeout=180).read()
        except Exception as e:  # warm-up only
            print("warm-up", path, "->", e)
    print("queued:", http("POST", f"/__demo/queue?company_id={COMPANY}&n=2"))


class Rec:
    def __init__(self, page):
        self.page, self.t0 = page, time.monotonic()
        self.keep: list[list[float]] = []   # [start, end] in video seconds, current segment
        self._open: float | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    def kept(self) -> float:
        return sum(b - a for a, b in self.keep) + (self.now() - self._open if self._open is not None else 0)

    def start(self):
        if self._open is None:
            self._open = self.now()

    def stop(self):
        if self._open is not None:
            self.keep.append([self._open, self.now()])
            self._open = None

    def hold(self, s: float):
        self.page.wait_for_timeout(int(s * 1000))

    def fill(self, budget: float):
        left = budget - self.kept()
        if left > 0:
            self.hold(left)
        self.stop()

    def goto(self, route: str):
        self.stop()
        self.page.goto(WEB + route, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        self.hold(1.0)
        self.page.mouse.move(960, 540)

    def scroll(self, dy: int, secs: float):
        self.page.evaluate(SMOOTH_JS, [dy, int(secs * 1000)])

    def click(self, loc, pause: float = 0.4):
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        if box:
            self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=30)
            self.hold(pause)
        loc.click()

    def type_into(self, loc, text: str):
        self.click(loc, 0.2)
        loc.press_sequentially(text, delay=35)
        self.hold(0.4)


def run_segments(page, rec: Rec, budgets: dict[str, float]) -> list[dict]:
    out: list[dict] = []
    p = page

    def seg(sid, fn):
        rec.keep = []
        rec._open = None
        t_begin = rec.now()
        try:
            fn()
        except Exception as e:
            print(f"[{sid}] action failed: {type(e).__name__}: {str(e)[:150]}")
        rec.fill(budgets[sid])
        out.append({"id": sid, "budget": budgets[sid], "keep": rec.keep, "begin": t_begin, "end": rec.now()})
        print(f"[{sid}] kept {sum(b - a for a, b in rec.keep):.1f}s of {budgets[sid]:.1f}s")

    def card(html_body):
        rec.stop()
        p.set_content("<body style='margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;"
                      "justify-content:center;background:#0f172a;color:#fff;font-family:Segoe UI,Arial,sans-serif;text-align:center'>"
                      + html_body + "</body>")
        p.mouse.move(1900, 1070)
        rec.start()

    def n01():
        card("<div style='font-size:64px;font-weight:700;max-width:1400px'>The software says one thing.<br>The bank says another.</div>")
        rec.hold(4)

    def n02():
        card("<div style='font-size:40px;color:#94a3b8;margin-bottom:24px'>Finova: SME Finance Copilot</div>"
             "<div style='font-size:76px;font-weight:700'>Find the truth. Explain it. Act safely.</div>"
             "<div style='font-size:28px;color:#94a3b8;margin-top:40px'>Synthetic data only</div>")
        rec.hold(4)

    def n03():
        rec.goto("/"); rec.start()
        rec.hold(5); rec.scroll(250, 3); rec.hold(1); rec.scroll(-250, 2)

    def n04():
        rec.goto("/cash-truth"); rec.start()
        rec.hold(2); rec.scroll(380, 5)
        g = p.locator("button.px-5.py-4.text-left").first
        rec.click(g); rec.hold(2)
        f = p.locator("button.px-4.py-3.text-left").first
        rec.click(f); rec.hold(1)

    def ask(q, tries=3):
        if tries < 3:   # previous attempt failed (provider error): start over on a clean chat page
            rec.stop(); p.reload(); p.wait_for_load_state("networkidle"); rec.hold(1); rec.start()
        box = p.get_by_placeholder(re.compile("Ask about"))
        rec.type_into(box, q)
        p.keyboard.press("Enter")
        rec.hold(1.5)
        rec.stop()   # model latency is cut out of the video
        try:
            p.wait_for_function("() => !Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim() === '…')",
                                timeout=150000)
        except Exception:
            print("chat wait timed out")
        rec.hold(1.0)
        if "provider_error" in p.inner_text("body") and tries > 1:
            print("provider error, retrying")
            rec.start()
            return ask(q, tries - 1)
        rec.start()

    def n05():
        rec.goto("/chat"); rec.start()
        ask(Q1)
        rec.scroll(250, 3)
        try:
            c = p.locator("button.text-brand.underline").first
            rec.click(c); rec.hold(3)
        except Exception as e:
            print("no evidence button:", str(e)[:80])

    def n06():
        rec.goto("/approvals"); rec.start()
        rec.hold(2)
        b = p.get_by_role("button", name=re.compile(r"^Approve( draft)?$", re.I)).first
        rec.click(b, 0.8)
        rec.hold(2)

    def n07():
        rec.goto("/audit"); rec.start()
        rec.hold(1.5)
        v = p.get_by_role("button", name=re.compile("Verify", re.I)).first
        rec.click(v, 0.6); rec.hold(3)
        rec.goto("/chat"); rec.start()
        ask(Q2)
        rec.scroll(300, 2)

    def n08():
        rec.goto("/money"); rec.start()
        rec.hold(2); rec.scroll(450, 5)
        rec.goto("/financing"); rec.start()
        rec.hold(2); rec.scroll(250, 3)

    def n09():
        rec.goto("/"); rec.start()
        sel = p.locator("header select").first
        opts = [o for o in sel.locator("option").all_text_contents()]
        others = [i for i, o in enumerate(opts) if "xborder" not in o.lower() and "★" not in o]
        for i in others[:2]:
            rec.click(sel, 0.3)
            sel.select_option(index=i)
            rec.hold(3.0)
        card("<div style='font-size:34px;color:#94a3b8'>Three businesses, three currencies (synthetic answer keys)</div>"
             "<div style='display:flex;gap:120px;margin-top:40px'>"
             "<div><div style='font-size:150px;font-weight:700'>98%</div><div style='font-size:34px'>of detectable problems found</div></div>"
             "<div><div style='font-size:150px;font-weight:700'>99%</div><div style='font-size:34px'>precision</div></div></div>")
        rec.hold(4)

    def n10():
        card("<div style='font-size:96px;font-weight:700'>Finova</div><div style='font-size:40px;margin-top:10px'>SME Finance Copilot</div>"
             "<div style='font-size:38px;color:#94a3b8;margin-top:28px'>The truth about your cash, and an assistant you can trust to act on it.</div>")
        rec.hold(3)

    for sid, fn in [("N01", n01), ("N02", n02), ("N03", n03), ("N04", n04), ("N05", n05), ("N06", n06),
                    ("N07", n07), ("N08", n08), ("N09", n09), ("N10", n10)]:
        seg(sid, fn)
    return out


def cmd_record(use_voice: bool) -> None:
    from playwright.sync_api import sync_playwright
    words = narration_text()
    # placeholder timing: speaking time at about 2.2 words per second, plus padding; at least the default hold
    budgets = {sid: max(float(t) * 0.6, len(words.get(sid, "").split()) / 2.2 + PAD + 1.5) for sid, t in SEGMENTS}
    if use_voice:
        vf = voice_files()
        missing = [s for s, _ in SEGMENTS if s not in vf]
        if missing:
            sys.exit(f"missing voice files: {missing}")
        budgets = {s: probe(vf[s]) + PAD for s, _ in SEGMENTS}
    prepare_state()
    tmp = MEDIA / "_rec"
    for f in tmp.glob("*.webm") if tmp.exists() else []:
        f.unlink()
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(tmp),
                             record_video_size={"width": 1920, "height": 1080})
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        rec = Rec(page)
        segs = run_segments(page, rec, budgets)
        vid = page.video
        ctx.close()
        br.close()
        raw = MEDIA / "raw_demo.webm"
        Path(vid.path()).replace(raw)
    (MEDIA / "timing.json").write_text(json.dumps({"voice": use_voice, "segments": segs}, indent=1), encoding="utf-8")
    print("wrote", raw, "and timing.json; video length", round(probe(raw), 1), "s")


ZH = {
    "N01": ["小企业依赖的数字，往往并不完全可信。", "软件说一套。", "银行说另一套。"],
    "N02": ["认识 SME Finance Copilot：一个AI智能体，找出现金真相、解释原因，并且只在业主批准后才行动。"],
    "N03": ["每周它都会为业主撰写一份简报。", "这家深圳卖家的账面现金多报了约六十七万六千元人民币。"],
    "N04": ["它把银行、电商平台和钱包的对账单与账簿逐一核对，并找出原因：漏记的平台费用、币种错误、重复记账和未入账的开支。"],
    "N05": ["用日常语言随便问它。", "每个数字都能链接回它所依据的原始数据行。", "AI从不自己做计算。"],
    "N06": ["它会起草更正分录。", "在业主批准之前，不会过账任何内容。"],
    "N07": ["每一步都会记入防篡改的审计日志。", "如果有人要求它转账，它会拒绝。", "它根本没有转账的工具。"],
    "N08": ["它能提前十三周预测现金流，及早预警，并匹配企业真正负担得起的融资方案，同时给出理由。"],
    "N09": ["在三个币种、三家企业上的测试中，它找出了百分之九十八的可检测问题，准确率为百分之九十九。"],
    "N10": ["SME Finance Copilot。", "了解您现金的真相，并拥有一个值得信赖、可代您行动的助手。"],
}


def sentences(t: str) -> list[str]:
    return re.split(r"(?<=[.])\s+", t.strip())


def fmt(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def narration_text() -> dict[str, str]:
    txt = (ROOT / "docs" / "deliverables" / "NARRATION.md").read_text(encoding="utf-8")
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\| (N\d\d) \| (.+?) \|", txt, re.M)}


def cmd_assemble() -> None:
    timing = json.loads((MEDIA / "timing.json").read_text(encoding="utf-8"))
    if not timing["voice"]:
        sys.exit("timing.json is from a dry run: run `record --voice` first")
    vf, words = voice_files(), narration_text()
    work = MEDIA / "_asm"
    work.mkdir(exist_ok=True)
    raw = MEDIA / "raw_demo.webm"
    vclips, aclips, srt, t = [], [], [], 0.0
    for i, s in enumerate(timing["segments"]):
        sid, dur = s["id"], s["budget"]
        # video: concatenate the kept intervals of this segment into one clip of ~dur seconds
        parts = []
        for j, (a, b) in enumerate(s["keep"]):
            p = work / f"{sid}_v{j}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(raw), "-an",
                            "-vf", "fps=30,scale=1920:1080,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(p)], check=True)
            parts.append(p)
        lst = work / f"{sid}.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        v = work / f"{sid}_v.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(v)], check=True)
        vlen = probe(v)
        a = work / f"{sid}_a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(vf[sid]), "-af", f"apad=whole_dur={vlen:.3f}",
                        "-t", f"{vlen:.3f}", "-ar", "44100", "-ac", "1", str(a)], check=True)
        vclips.append(v); aclips.append(a)
        alen = min(probe(vf[sid]), vlen)
        srt.append((t + 0.1, t + alen, words.get(sid, "")))
        t += vlen
    for name, clips in (("v", vclips), ("a", aclips)):
        (work / f"all_{name}.txt").write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(work / "all_v.txt"), "-c", "copy", str(work / "all_v.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(work / "all_a.txt"), "-c", "copy", str(work / "all_a.wav")], check=True)
    # bilingual subtitles: one cue per sentence (English on top, Chinese below), timed by sentence length
    lines, n = [], 1
    for (a0, a1, txt), s_ in zip(srt, timing["segments"]):
        en = sentences(txt)
        cn = ZH[s_["id"]]
        assert len(en) == len(cn), (s_["id"], len(en), len(cn))
        tot = sum(len(x) for x in en)
        cur = a0
        for e, c in zip(en, cn):
            d = (a1 - a0) * len(e) / tot
            lines.append("%d" % n + chr(10) + fmt(cur) + " --> " + fmt(cur + d - 0.05) + chr(10) + e + chr(10) + c + chr(10))
            cur += d
            n += 1
    sub = MEDIA / "subtitles.srt"
    sub.write_text("\n".join(lines), encoding="utf-8")
    out = MEDIA / "demo_video.mp4"
    style = "FontName=Microsoft YaHei,FontSize=10,Outline=2,Shadow=0,MarginV=40,BorderStyle=1"
    music = MEDIA / "music" / "bg.mp3"
    total = probe(work / "all_v.mp4")
    if music.exists():
        # voice normalised; music loops, sits low and is ducked by the voice; louder only where nobody speaks
        fc = ("[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,asplit=2[v][vsc];"
              f"[2:a]aloop=loop=-1:size=2e9,atrim=0:{total:.2f},volume=0.35,afade=t=in:d=2,afade=t=out:st={total - 3:.2f}:d=3[m];"
              "[m][vsc]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=400[md];"
              "[v][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
        ain = ["-i", "all_a.wav", "-i", str(music)]
    else:
        print("NO MUSIC FILE: rendering voice only")
        fc = "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        ain = ["-i", "all_a.wav"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "all_v.mp4", *ain, "-filter_complex", fc,
                    "-vf", f"subtitles=../subtitles.srt:force_style='{style}'", "-map", "0:v", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(out)], check=True, cwd=work)
    print("wrote", out, "duration", round(probe(out), 1), "s,", round(out.stat().st_size / 1e6, 1), "MB")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("record", "assemble"):
        sys.exit(__doc__)
    cmd_record("--voice" in sys.argv) if sys.argv[1] == "record" else cmd_assemble()
