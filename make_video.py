import os, re, json, random, pathlib, subprocess, asyncio, time
import requests
import edge_tts

VOICE = os.environ.get("VOICE", "bn-BD-NabanitaNeural")  # পুরুষ ভয়েস: bn-BD-PradeepNeural
TITLE = os.environ["TITLE"].strip()
SCRIPT = os.environ["SCRIPT"].strip()
KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
PIXABAY_KEY = os.environ.get("PIXABAY_KEY", "")
W, H = 1080, 1920

work = pathlib.Path("work")
work.mkdir(exist_ok=True)


def run(cmd):
    subprocess.run(cmd, check=True)


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return float(out.strip())


def split_sentences(text):
    parts = re.split(r"(?<=[।?!\.])\s+", text)
    return [p.strip() for p in parts if p.strip()]


async def make_audio(sentences):
    files = []
    for i, s in enumerate(sentences):
        f = work / f"s{i}.mp3"
        for attempt in range(3):
            try:
                await edge_tts.Communicate(s, VOICE, rate="+5%").save(str(f))
                break
            except Exception as e:
                print("TTS retry", attempt, e)
                if attempt == 2:
                    raise
                time.sleep(3)
        files.append(f)
    return files


def concat(files, out, extra):
    lst = work / (out.stem + "_list.txt")
    lst.write_text("".join(f"file '{f.resolve()}'\n" for f in files))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst)] + extra + [str(out)])


def ass_time(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(sentences, durs, total):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Noto Sans Bengali,78,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,5,80,80,0,1
Style: Title,Noto Sans Bengali,62,&H0000D7FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,70,70,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Title,,0,0,0,,{TITLE}"]
    t = 0.0
    for s, d in zip(sentences, durs):
        words = s.split()
        chunks = [" ".join(words[i:i + 5]) for i in range(0, len(words), 5)] or [s]
        total_chars = sum(len(c) for c in chunks)
        ct = t
        for c in chunks:
            cd = d * len(c) / total_chars
            lines.append(f"Dialogue: 0,{ass_time(ct)},{ass_time(ct + cd)},Sub,,0,0,0,,{c}")
            ct += cd
        t += d
    (work / "subs.ass").write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def fetch_clips():
    clips = []
    if not PIXABAY_KEY:
        return clips
    for kw in KEYWORDS[:3]:
        try:
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params={"key": PIXABAY_KEY, "q": kw, "per_page": 10, "safesearch": "true"},
                timeout=30,
            )
            hits = r.json().get("hits", [])
            if not hits:
                continue
            hit = random.choice(hits[:5])
            v = hit["videos"].get("medium") or hit["videos"].get("small")
            data = requests.get(v["url"], timeout=120).content
            f = work / f"bg{len(clips)}.mp4"
            f.write_bytes(data)
            clips.append(f)
        except Exception as e:
            print("Pixabay skip:", kw, e)
    return clips


def build_background(total):
    segs = []
    clips = fetch_clips()
    vf_tail = f"fps=30,setsar=1,format=yuv420p"
    if clips:
        seg_len = total / len(clips)
        for i, c in enumerate(clips):
            out = work / f"seg{i}.mp4"
            run([
                "ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(c), "-t", f"{seg_len:.2f}",
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=brightness=-0.18,{vf_tail}",
                "-an", "-c:v", "libx264", "-preset", "veryfast", str(out),
            ])
            segs.append(out)
    else:
        palettes = [("0x0f172a", "0x2563eb"), ("0x1e1b4b", "0x9333ea"), ("0x022c22", "0x10b981"), ("0x1f2937", "0xf97316")]
        c0, c1 = random.choice(palettes)
        out = work / "seg0.mp4"
        run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"gradients=s={W}x{H}:d={total:.2f}:c0={c0}:c1={c1}:speed=0.03:rate=30",
            "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", str(out),
        ])
        segs.append(out)
    bg = work / "bg.mp4"
    concat(segs, bg, ["-c", "copy"])
    return bg


def main():
    sentences = split_sentences(SCRIPT)
    audio_files = asyncio.run(make_audio(sentences))
    durs = [duration(f) for f in audio_files]
    audio = work / "audio.mp3"
    concat(audio_files, audio, ["-c:a", "libmp3lame", "-q:a", "3"])
    total = sum(durs) + 0.6
    build_ass(sentences, durs, total)
    bg = build_background(total)
    run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(bg), "-i", str(audio),
        "-vf", f"ass={work}/subs.ass:fontsdir=/usr/share/fonts",
        "-af", "apad=pad_dur=0.6",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", "out.mp4",
    ])
    print("Done. Duration:", total)


if __name__ == "__main__":
    main()
