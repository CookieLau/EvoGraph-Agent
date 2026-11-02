#!/usr/bin/env bash
# Assemble the EvoGraph-Quant launch video from per-scene MP4s.
#   video/out/s01_*.mp4 ... s08_*.mp4  ->  video/EvoGraph-Quant_launch.mp4 (+ _720p), poster, contact sheet
# Missing scenes are skipped. 0.4s cross-fades between scenes, 0.3s paper-colour fade at both ends.
# Usage: bash video/assemble.sh        (from anywhere)
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
XF=0.4      # cross-fade length (s)
EDGE=0.3    # fade from / to paper at start and end (s)
FPS=30

PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
# Paper colour from brand/tokens.css: a hex on --paper, else --color-paper's oklch() converted to sRGB, else #FDFDFB.
PAPER="$(grep -E -- '--paper:' "$ROOT/brand/tokens.css" 2>/dev/null | head -1 | grep -oE '#[0-9A-Fa-f]{6}' | head -1 | tr -d '#')"
if [ -z "$PAPER" ]; then
  PAPER="$("$PY" - "$ROOT/brand/tokens.css" <<'EOF' 2>/dev/null
import re, sys, math
css = open(sys.argv[1]).read()
m = re.search(r"--color-paper:\s*oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)", css)
L, C, h = float(m.group(1)) / 100, float(m.group(2)), math.radians(float(m.group(3)))
a, b = C * math.cos(h), C * math.sin(h)
l_, m_, s_ = (L + 0.3963377774*a + 0.2158037573*b) ** 3, (L - 0.1055613458*a - 0.0638541728*b) ** 3, (L - 0.0894841775*a - 1.2914855480*b) ** 3
rgb = (4.0767416621*l_ - 3.3077115913*m_ + 0.2309699292*s_, -1.2684380046*l_ + 2.6097574011*m_ - 0.3413193965*s_, -0.0041960863*l_ - 0.7034186147*m_ + 1.7076147010*s_)
enc = lambda x: 12.92*x if x <= 0.0031308 else 1.055*x**(1/2.4) - 0.055
print("".join("%02X" % round(min(1, max(0, enc(c))) * 255) for c in rgb))
EOF
)"
fi
[ -n "$PAPER" ] || PAPER="FDFDFB"
echo ">> paper colour #$PAPER"

dur() {  # seconds, from ffmpeg's banner (no ffprobe on this machine)
  ffmpeg -hide_banner -i "$1" 2>&1 | sed -n -E 's/.*Duration: ([0-9]+):([0-9]+):([0-9.]+).*/\1 \2 \3/p' | head -1 \
    | awk '{ printf "%.3f", $1*3600 + $2*60 + $3 }'
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
REAL_CAPTION="Live product, recorded tonight"
INK="111111"
FONT="/System/Library/Fonts/Supplemental/Arial.ttf"; [ -f "$FONT" ] || FONT="/System/Library/Fonts/Helvetica.ttc"

# The real screen recording is not an HTML scene: fit it to 1920x1080 at 30 fps on paper, drop audio,
# caption it bottom-left, and keep a key still for the player page and contact sheet.
prep_real() {
  local src="$1" dst="$TMP/real_app.mp4"
  ffmpeg -y -hide_banner -loglevel error -i "$src" -an -vf \
"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x$PAPER,fps=$FPS,\
drawtext=fontfile='$FONT':text='$REAL_CAPTION':fontsize=34:fontcolor=0x$INK:x=72:y=h-th-72:box=1:boxcolor=0x$PAPER@0.94:boxborderw=18,format=yuv420p" \
    -c:v libx264 -preset veryfast -crf 16 -pix_fmt yuv420p "$dst" || return 1
  local d; d="$(dur "$dst")"
  ffmpeg -y -hide_banner -loglevel error -ss "$(awk -v d="$d" 'BEGIN{printf "%.2f", d*0.4}')" -i "$dst" -frames:v 1 stills/real_app.png || true
  echo "$dst"
}

# Cut order. Each entry is a glob in out/; the real recording is special-cased.
ORDER=("s01_*" "s02_*" "s03_*" "s04_*" "s05_*" "s06_*" "s07_*" "s07b_*" "real_app" "s08_*")
FILES=(); DURS=(); NAMES=()
for g in "${ORDER[@]}"; do
  if [ "$g" = "real_app" ]; then
    [ -f out/real_app.mp4 ] || { echo ">> real_app.mp4 missing, skipped"; continue; }
    f="$(prep_real out/real_app.mp4)" || { echo ">> real_app.mp4 could not be prepared, skipped"; continue; }
    name="real_app"
  else
    f="$(ls out/$g.mp4 2>/dev/null | head -1)"
    if [ -z "$f" ]; then echo ">> ${g%_\*} missing, skipped"; continue; fi
    name="$(basename "$f" .mp4)"
  fi
  d="$(dur "$f")"
  if [ -z "$d" ] || awk -v d="$d" 'BEGIN{exit !(d < 0.9)}'; then echo ">> $f unreadable or too short ($d), skipped"; continue; fi
  FILES+=("$f"); DURS+=("$d"); NAMES+=("$name"); echo ">> $name  ${d}s"
done

N=${#FILES[@]}
if [ "$N" -eq 0 ]; then echo "!! no scene videos in video/out/"; exit 1; fi

INPUTS=(); FILTER=""
for i in $(seq 0 $((N-1))); do
  INPUTS+=(-i "${FILES[$i]}")
  FILTER+="[$i:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x$PAPER,fps=$FPS,format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v$i];"
done

# Chain xfades. After merging k+1 clips the running length is L = sum(d) - k*XF; next offset = L - XF.
LEN="${DURS[0]}"; LAST="v0"
for i in $(seq 1 $((N-1))); do
  OFF="$(awk -v l="$LEN" -v x="$XF" 'BEGIN{printf "%.3f", l - x}')"
  FILTER+="[$LAST][v$i]xfade=transition=fade:duration=$XF:offset=$OFF[x$i];"
  LAST="x$i"
  LEN="$(awk -v l="$LEN" -v d="${DURS[$i]}" -v x="$XF" 'BEGIN{printf "%.3f", l + d - x}')"
done
OUTST="$(awk -v l="$LEN" -v e="$EDGE" 'BEGIN{printf "%.3f", l - e}')"
FILTER+="[$LAST]fade=t=in:st=0:d=$EDGE:color=0x$PAPER,fade=t=out:st=$OUTST:d=$EDGE:color=0x$PAPER,format=yuv420p[out]"

echo ">> assembling $N scene(s), expected length ${LEN}s"
ffmpeg -y -hide_banner -loglevel error "${INPUTS[@]}" -filter_complex "$FILTER" -map "[out]" -an \
  -r $FPS -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +faststart EvoGraph-Quant_launch.mp4 \
  || { echo "!! assemble failed"; exit 1; }

ffmpeg -y -hide_banner -loglevel error -i EvoGraph-Quant_launch.mp4 -vf scale=1280:720 -an \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -movflags +faststart EvoGraph-Quant_launch_720p.mp4 \
  || echo "!! 720p transcode failed"

# Poster: s01 still, else s04, else first available still.
POSTER="$(ls stills/s01_*.png 2>/dev/null | head -1)"
[ -n "$POSTER" ] || POSTER="$(ls stills/s04_*.png 2>/dev/null | head -1)"
[ -n "$POSTER" ] || POSTER="$(ls stills/s0*.png 2>/dev/null | head -1)"
[ -n "$POSTER" ] && cp "$POSTER" poster.png && echo ">> poster.png <- $POSTER"

# Contact sheet: the key stills of the cut, in cut order, 4 or 5 per row on paper.
"$PY" - "$PAPER" "${NAMES[@]}" <<'EOF' || echo "!! contact sheet failed"
import sys, os
from PIL import Image, ImageDraw
paper = tuple(int(sys.argv[1][i:i+2], 16) for i in (0, 2, 4))
names = sys.argv[2:]
rule = (224, 223, 218)
W, H, G, M = 480, 270, 24, 48
cols = 5 if len(names) > 8 else 4
rows = max(1, -(-len(names) // cols))
sheet = Image.new("RGB", (M*2 + cols*W + (cols-1)*G, M*2 + rows*H + (rows-1)*G), paper)
d = ImageDraw.Draw(sheet)
for k, n in enumerate(names):
    x = M + (k % cols) * (W + G); y = M + (k // cols) * (H + G)
    p = f"stills/{n}.png"
    if os.path.exists(p):
        im = Image.open(p).convert("RGB"); im.thumbnail((W, H)); sheet.paste(im, (x, y))
    d.rectangle([x, y, x + W - 1, y + H - 1], outline=rule, width=2)
sheet.save("stills/contact_sheet.png")
print(f">> stills/contact_sheet.png ({len(names)} stills, {cols}x{rows})")
EOF

# Player page, generated from the scenes that actually made it into the cut.
ITEMS=""; START="0"
for i in $(seq 0 $((N-1))); do
  base="${NAMES[$i]}"
  if [ "$base" = "real_app" ]; then title="$REAL_CAPTION"
  else title="$(grep -oE 'title: *"[^"]*"' "scenes/$base.html" 2>/dev/null | head -1 | sed -E 's/title: *"([^"]*)"/\1/')"; fi
  [ -n "$title" ] || title="$base"
  num="$(printf "%02d" $((i+1)))"
  secs="$(awk -v d="${DURS[$i]}" 'BEGIN{printf "%.0f", d}')"
  thumb=""; [ -f "stills/$base.png" ] && thumb="<img src=\"stills/$base.png\" alt=\"Key frame of scene $num, $title\" loading=\"lazy\" width=\"480\" height=\"270\">"
  ITEMS+="      <li><button type=\"button\" data-t=\"$START\">$thumb<span class=\"n\">Scene $num</span><span class=\"t\">$title</span><span class=\"d\">${secs} s</span></button></li>
"
  START="$(awk -v s="$START" -v d="${DURS[$i]}" -v x="$XF" 'BEGIN{printf "%.2f", s + d - x}')"
done
TOTAL="$(awk -v l="$LEN" 'BEGIN{printf "%.0f", l}')"
cat > index.html <<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="The EvoGraph-Quant launch film: a self-evolving ETL agent that learns from Daytona sandbox crashes on Nosana GPUs, so only verified data reaches the Neo4j risk graph.">
<title>EvoGraph-Quant launch film</title>
<link rel="stylesheet" href="../brand/tokens.css">
<style>
/* Hallmark · theme: ink (from brand/tokens.css) · ink: paper · genre: editorial · surface: video player page · signature: the film itself, full width · v2.0.0 */
/* Hallmark · pre-emit critique: P4 H4 E4 S4 R4 V4 · weakest: S, a player page is mostly the film; the scene strip doubles as chapter navigation */
html, body { overflow-x: clip; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-body); line-height: 1.5; }
main { max-width: 1240px; margin: 0 auto; padding: 56px 24px 72px; }
h1 { font-family: var(--font-display); font-weight: var(--display-weight, 700); letter-spacing: var(--tracking-display, -0.03em);
     font-size: clamp(2rem, 4.2vw, 3.4rem); line-height: 1.05; margin: 0 0 12px; text-wrap: balance; overflow-wrap: break-word; min-width: 0; }
.lede { font-size: 1.15rem; color: var(--ink-2); max-width: 46ch; margin: 0 0 32px; }
video { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; background: var(--paper-2);
        border: var(--hair) solid var(--rule); border-radius: var(--radius); }
.meta { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 16px 0 0; color: var(--muted); font-size: 0.95rem; }
.meta a { color: var(--ink); text-underline-offset: 3px; }
h2 { text-wrap: balance; font-family: var(--font-display); font-weight: 600; font-size: 1.35rem; margin: 56px 0 16px; }
ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 20px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
li button { all: unset; box-sizing: border-box; display: grid; gap: 4px; width: 100%; cursor: pointer; }
li img { display: block; width: 100%; height: auto; border: var(--hair) solid var(--rule); border-radius: calc(var(--radius) - 4px);
         background: var(--paper-2); margin-bottom: 8px; }
li .n { color: var(--muted); font-size: 0.9rem; }
li .t { font-weight: 600; }
li .d { color: var(--muted); font-size: 0.9rem; font-family: var(--font-mono); }
li button:hover .t { text-decoration: underline; text-underline-offset: 3px; }
li button:active img { border-color: var(--ink-2); }
li button:disabled { cursor: default; opacity: 0.6; }
::selection { background: var(--ok-soft); color: var(--ink); }
html { scrollbar-color: var(--rule) var(--paper); }
:focus-visible { outline: 2px solid var(--ok); outline-offset: 3px; transition: none; }
li button:focus-visible { outline: 2px solid var(--ok); outline-offset: 4px; border-radius: 6px; }
.note { margin-top: 48px; color: var(--muted); font-size: 0.95rem; max-width: 70ch; }
@media (max-width: 900px) { ol { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 520px) { main { padding: 32px 16px 56px; } ol { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
</style>
</head>
<body>
<main>
  <h1>EvoGraph-Quant</h1>
  <p class="lede">A self-evolving ETL agent that learns from sandbox crashes, so only verified data reaches the risk graph.</p>
  <video id="film" controls playsinline preload="metadata" poster="poster.png">
    <source src="EvoGraph-Quant_launch_720p.mp4" type="video/mp4">
    <source src="EvoGraph-Quant_launch.mp4" type="video/mp4">
    Your browser cannot play this video. <a href="EvoGraph-Quant_launch_720p.mp4">Download the MP4</a>.
  </video>
  <p class="meta"><span>${TOTAL} seconds at 30 fps</span><a href="EvoGraph-Quant_launch_720p.mp4" download>Download 720p</a><a href="EvoGraph-Quant_launch.mp4">1080p master, local build only</a></p>
  <h2>Scenes</h2>
  <ol>
${ITEMS}  </ol>
  <p class="note">There is no audio track: the film is narrated live during the demo. Every scene is an HTML page rendered frame by frame with headless Chrome and encoded with ffmpeg; the figures shown come from tonight’s measured runs.</p>
</main>
<script>
  // Chapter buttons seek the film; without scripting the list still shows every scene.
  const film = document.getElementById("film");
  document.querySelectorAll("li button[data-t]").forEach((b) => b.addEventListener("click", () => {
    film.currentTime = parseFloat(b.dataset.t); film.play().catch(() => {}); film.focus();
  }));
</script>
</body>
</html>
HTML
echo ">> index.html (${N} scenes)"

echo ">> done: video/EvoGraph-Quant_launch.mp4 (${LEN}s), video/EvoGraph-Quant_launch_720p.mp4"
