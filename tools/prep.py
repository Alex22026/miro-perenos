"""Папка с PDF из Sboard → куски в репозитории + SVG для Miro + manifest.
python3 prep.py <папка_с_pdf> <scratch>"""
import sys, os, re, json, subprocess, math, tempfile, shutil
from concurrent.futures import ProcessPoolExecutor
from PIL import Image, ImageStat
Image.MAX_IMAGE_PIXELS = None
REPO = "/home/claude/miro-perenos"
RAW = "https://raw.githubusercontent.com/Alex22026/miro-perenos/main/"

def tiles_for(pdf, bid):
    outdir = os.path.join(REPO, "pdf", bid)
    shutil.rmtree(outdir, ignore_errors=True); os.makedirs(outdir)
    tmp = tempfile.mkdtemp()
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    pw, ph = map(float, re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info).groups())
    subprocess.run(["pdftohtml", "-xml", "-zoom", "1", "-q", pdf, os.path.join(tmp, "b")], capture_output=True)
    xml = open(os.path.join(tmp, "b.xml"), encoding="utf-8", errors="ignore").read() if os.path.exists(os.path.join(tmp, "b.xml")) else ""
    s_img = 1.0
    for m in re.finditer(r'<image top="[\d.-]+" left="[\d.-]+" width="([\d.]+)" height="[\d.]+" src="([^"]+)"', xml):
        try:
            iw = Image.open(os.path.join(tmp, os.path.basename(m.group(2)))).size[0]
            s_img = max(s_img, iw / max(1.0, float(m.group(1))))
        except Exception: pass
    sizes = [float(x) for x in re.findall(r'fontspec id="\d+" size="([\d.]+)"', xml)]
    s_txt = (16.0 / min(sizes)) if sizes else 1.0
    s = max(1.5, min(3.0, max(s_img, s_txt)))
    # грубая карта содержимого
    lowdpi = max(2.0, min(20.0, 2000 * 72 / max(pw, ph)))
    subprocess.run(["pdftoppm", "-r", f"{lowdpi:.3f}", "-png", "-singlefile", pdf, os.path.join(tmp, "low")], check=True)
    low = Image.open(os.path.join(tmp, "low.png")).convert("RGB")
    bg = max(low.getcolors(1 << 22), key=lambda c: c[0])[1]
    mask = low.point(lambda v: v).convert("RGB")
    from PIL import ImageChops
    diff = ImageChops.difference(low, Image.new("RGB", low.size, bg)).convert("L").point(lambda v: 255 if v > 6 else 0)
    k = low.size[0] / pw
    C = math.floor(2400 / s)
    cells = []
    for j in range(math.ceil(ph / C)):
        for i in range(math.ceil(pw / C)):
            x0, y0 = i * C, j * C; x1, y1 = min(pw, x0 + C), min(ph, y0 + C)
            box = (int(x0 * k), int(y0 * k), max(int(x0 * k) + 1, math.ceil(x1 * k)), max(int(y0 * k) + 1, math.ceil(y1 * k)))
            if diff.crop(box).getbbox():
                cells.append((i, j, x0, y0, x1, y1))
    if not cells: return {"bid": bid, "scale": s, "tiles": []}
    minx = min(c[2] for c in cells); miny = min(c[3] for c in cells)
    maxx = max(c[4] for c in cells); maxy = max(c[5] for c in cells)
    cx0, cy0 = (minx + maxx) / 2, (miny + maxy) / 2
    out = []
    rows = {}
    for c in cells: rows.setdefault(c[1], []).append(c)
    for j, rc in sorted(rows.items()):
        rx0 = min(c[2] for c in rc); rx1 = max(c[4] for c in rc)
        y0, y1 = rc[0][3], rc[0][5]
        X0, Y0 = int(rx0 * s), int(y0 * s)
        W = math.ceil((rx1 - rx0) * s) + 2; H = math.ceil((y1 - y0) * s) + 2
        base = os.path.join(tmp, f"r{j}")
        subprocess.run(["pdftoppm", "-r", f"{72*s:.4f}", "-x", str(X0), "-y", str(Y0),
                        "-W", str(W), "-H", str(H), "-singlefile", pdf, base], check=True)
        row = Image.open(base + ".ppm").convert("RGB")
        for (i, _, x0, _, x1, _) in sorted(rc):
            a = int(x0 * s) - X0
            b = min(row.size[0], a + math.ceil((x1 - x0) * s) + 2)
            im = row.crop((a, 0, b, row.size[1]))
            if ImageStat.Stat(im).stddev == [0.0, 0.0, 0.0]: continue
            fn = f"pdf/{bid}/{j}_{i}.jpg"
            im.save(os.path.join(REPO, fn), "JPEG", quality=92)
            out.append({"f": fn, "x": round(int(x0 * s) / s - cx0, 1), "y": round(Y0 / s - cy0, 1),
                        "w": round(im.size[0] / s, 1), "h": round(im.size[1] / s, 1)})
        os.remove(base + ".ppm")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"bid": bid, "scale": round(s, 2), "tiles": out}

def svg_for(r):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg">']
    for n, t in enumerate(r["tiles"]):
        parts.append(f'<image id="t{n}" data-type="image" href="{RAW}{t["f"]}" x="{t["x"]}" y="{t["y"]}" width="{t["w"]}" height="{t["h"]}" />')
    parts.append("</svg>")
    return "\n".join(parts)

def job(pdf):
    bid = os.path.basename(pdf)[:8]
    try:
        r = tiles_for(pdf, bid); r["svg"] = svg_for(r); r["src"] = os.path.basename(pdf); return r
    except Exception as e:
        return {"bid": bid, "error": str(e), "src": os.path.basename(pdf)}

if __name__ == "__main__":
    src, scratch = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(scratch, "svg"), exist_ok=True)
    pdfs = sorted(os.path.join(src, f) for f in os.listdir(src) if f.lower().endswith(".pdf"))
    man_path = os.path.join(scratch, "manifest.json")
    man = json.load(open(man_path)) if os.path.exists(man_path) else {}
    with ProcessPoolExecutor(2) as ex:
        for r in ex.map(job, pdfs):
            if "error" in r: print("ERR", r["src"], r["error"]); continue
            open(os.path.join(scratch, "svg", r["bid"] + ".svg"), "w").write(r["svg"])
            man[r["bid"]] = {"tiles": len(r["tiles"]), "scale": r["scale"], "src": r["src"]}
            print(r["bid"], len(r["tiles"]), "tiles, scale", r["scale"], flush=True)
    json.dump(man, open(man_path, "w"), ensure_ascii=False, indent=1)
