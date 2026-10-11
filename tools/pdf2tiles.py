"""PDF доски Sboard → чёткие куски (тайлы) для Miro.
Использование: python3 pdf2tiles.py <pdf> <bid> <outdir>
Печатает JSON: [{"f": путь, "x": центр X, "y": центр Y, "w": ширина на доске}]"""
import sys, os, re, json, subprocess, math, tempfile
from PIL import Image, ImageStat
Image.MAX_IMAGE_PIXELS = None
pdf, bid, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(outdir, exist_ok=True)
tmp = tempfile.mkdtemp()

info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
pw, ph = map(float, re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info).groups())

# масштаб: чтобы встроенные картинки шли в родном разрешении, а векторный текст был чётким
subprocess.run(["pdftohtml", "-xml", "-zoom", "1", "-q", pdf, os.path.join(tmp, "b")], capture_output=True)
xml = open(os.path.join(tmp, "b.xml"), encoding="utf-8", errors="ignore").read()
s_img = 1.0
for m in re.finditer(r'<image top="([\d.-]+)" left="([\d.-]+)" width="([\d.]+)" height="([\d.]+)" src="([^"]+)"', xml):
    try:
        iw = Image.open(os.path.join(tmp, os.path.basename(m.group(5)))).size[0]
        s_img = max(s_img, iw / max(1.0, float(m.group(3))))
    except Exception:
        pass
sizes = [float(x) for x in re.findall(r'fontspec id="\d+" size="([\d.]+)"', xml)]
s_txt = (16.0 / min(sizes)) if sizes else 1.0
s = max(1.0, min(3.0, max(s_img, s_txt, 1.5)))

# грубая карта, где на доске что-то есть
lowr = 4.0  # dpi
subprocess.run(["pdftoppm", "-r", str(lowr), "-png", "-singlefile", pdf, os.path.join(tmp, "low")], check=True)
low = Image.open(os.path.join(tmp, "low.png")).convert("RGB")
bg = max(low.getcolors(1 << 20), key=lambda c: c[0])[1]
k = low.size[0] / pw  # px на pt в грубой карте

C = math.floor(2400 / s)  # размер куска в pt
nx, ny = math.ceil(pw / C), math.ceil(ph / C)
cells = []
for j in range(ny):
    for i in range(nx):
        x0, y0 = i * C, j * C
        x1, y1 = min(pw, x0 + C), min(ph, y0 + C)
        box = (int(x0 * k), int(y0 * k), max(int(x0 * k) + 1, math.ceil(x1 * k)), max(int(y0 * k) + 1, math.ceil(y1 * k)))
        reg = low.crop(box)
        diff = sum(1 for p in reg.getdata() if max(abs(p[0]-bg[0]), abs(p[1]-bg[1]), abs(p[2]-bg[2])) > 6)
        if diff:
            cells.append((i, j, x0, y0, x1, y1))
# центр доски = центр занятой области
if not cells:
    print("[]"); sys.exit()
minx = min(c[2] for c in cells); maxx = max(c[4] for c in cells)
miny = min(c[3] for c in cells); maxy = max(c[5] for c in cells)
cx0, cy0 = (minx + maxx) / 2, (miny + maxy) / 2

out = []
dpi = 72 * s
for (i, j, x0, y0, x1, y1) in cells:
    W = math.ceil((x1 - x0) * s) + 2; H = math.ceil((y1 - y0) * s) + 2   # 2px нахлёст
    base = os.path.join(tmp, f"t{j}_{i}")
    subprocess.run(["pdftoppm", "-r", f"{dpi:.4f}", "-x", str(int(x0 * s)), "-y", str(int(y0 * s)),
                    "-W", str(W), "-H", str(H), "-png", "-singlefile", pdf, base], check=True)
    im = Image.open(base + ".png").convert("RGB")
    if ImageStat.Stat(im).stddev == [0.0, 0.0, 0.0]:
        continue
    fn = os.path.join(outdir, f"{j}_{i}.jpg")
    im.save(fn, "JPEG", quality=92, optimize=True)
    wpt = im.size[0] / s
    hpt = im.size[1] / s
    out.append({"f": fn, "x": round(int(x0 * s) / s + wpt / 2 - cx0, 1), "y": round(int(y0 * s) / s + hpt / 2 - cy0, 1), "w": round(wpt, 1)})
print(json.dumps({"scale": round(s, 2), "tiles": out}, ensure_ascii=False))
