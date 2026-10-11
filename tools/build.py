"""Данные доски Sboard → набор SVG-команд для Miro (+ прозрачные слои с рукописными пометками).

python3 build.py <sboard_data.json> <sboard_pencil*.json|-> <scratch_out> [bid8 ...]

Для каждой доски пишет <scratch_out>/svg/<bid8>_<n>.svg (порции до ~150 КБ, по порядку слоёв),
слои пометок кладёт в репозиторий ov/<bid8>/*.png, а список картинок, которые нужно обрезать/повернуть,
дописывает в jobs/images.json (их делает GitHub Actions).
"""
import sys, os, json, math, hashlib, html, statistics, re
from PIL import Image, ImageDraw, ImageFont

REPO = "/home/claude/miro-perenos"
RAW = "https://raw.githubusercontent.com/Alex22026/miro-perenos/main/"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
R = 2.0          # пикселей на единицу Miro в слоях пометок
TILE = 1024      # размер плитки слоя пометок в единицах Miro
SVG_LIMIT = 150_000

FONTS = {"OpenSans": "open_sans", "Oswald": "roboto_condensed", "Moniqa": "caveat", "Caveat": "caveat",
         "ShantellSans": "caveat", "Arimo": "arial", "Bitter": "roboto_slab", "NotoSans": "noto_sans",
         "Montserrat": "noto_sans"}
SHAPES = {"rectangle": "rectangle", "roundedRectangle": "round_rectangle", "triangle": "triangle",
          "arrowRight": "right_arrow", "cloud": "cloud", "star": "star", "databaseFlowchart": "can",
          "parallelogram": "parallelogram", "rhombus": "rhombus", "trapezoid": "trapezoid",
          "bubble": "wedge_round_rectangle_callout", "braceRight": "right_brace", "cross": "cross",
          "actorFlowchart": "rectangle"}
STICKY = {"#fff9a3": "light_yellow", "#f2c824": "yellow", "#c21319": "red"}


def esc(s):
    return html.escape(str(s), quote=True)


def hex6(c, default="#000000"):
    if not c or not isinstance(c, str) or not c.startswith("#"):
        return default
    c = c.lower()
    if len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c[:7] if len(c) >= 7 else default


def alpha_of(c):
    if isinstance(c, str) and c.startswith("#") and len(c) == 9:
        return int(c[7:9], 16) / 255
    return 1.0


def blend(c, a, bg=(255, 255, 255)):
    c = hex6(c, "#ffffff")
    r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    r, g, b = (round(r * a + bg[0] * (1 - a)), round(g * a + bg[1] * (1 - a)), round(b * a + bg[2] * (1 - a)))
    return "#%02x%02x%02x" % (r, g, b)


def ops_to_html(ops):
    """textOps (Quill delta) → html для Miro, плюс основной цвет."""
    out, colors = [], {}
    for op in ops or []:
        t = op.get("insert")
        if not isinstance(t, str):
            continue
        a = op.get("attributes") or {}
        col = hex6(a.get("color"), None) if a.get("color") else None
        if col and t.strip():
            colors[col] = colors.get(col, 0) + len(t)
        parts = t.split("\n")
        for k, p in enumerate(parts):
            if p:
                s = esc(p)
                if a.get("bold"): s = "<b>" + s + "</b>"
                if a.get("italic"): s = "<i>" + s + "</i>"
                if a.get("underline"): s = "<u>" + s + "</u>"
                if a.get("strike"): s = "<s>" + s + "</s>"
                out.append(s)
            if k < len(parts) - 1:
                out.append("<br/>")
    h = "".join(out)
    while h.endswith("<br/>"):
        h = h[:-5]
    color = max(colors, key=colors.get) if colors else "#1a1a1a"
    return h, color


def plain_text(ops):
    return "".join(op.get("insert", "") for op in (ops or []) if isinstance(op.get("insert"), str))


def collect(rec, extra_pencil):
    seen, objs = set(), []
    for o in rec.get("drawings", []) + rec.get("lines", []) + rec.get("pencil", []) + (extra_pencil or []):
        if not isinstance(o, dict) or o.get("id") in seen or o.get("deleted_at"):
            continue
        gp = (o.get("graphicsPayload") or {}).get("graphicsParams") or {}
        if o.get("mode") == "text" and gp.get("submode") == "tableCellText":
            continue
        seen.add(o.get("id"))
        objs.append(o)
    return objs


def box(o):
    g = o["graphicsPayload"]
    p, s = g.get("position") or {}, g.get("containerSize") or {}
    return float(p.get("x", 0)), float(p.get("y", 0)), float(s.get("width", 0)), float(s.get("height", 0))


def image_crop(o):
    gp = o["graphicsPayload"].get("graphicsParams") or {}
    ini = gp.get("initialSize")
    m = gp.get("maskData") or {}
    if not ini or not m.get("size"):
        return None, ini
    mw, mh = m["size"]["width"], m["size"]["height"]
    mx, my = (m.get("position") or {}).get("x", 0), (m.get("position") or {}).get("y", 0)
    if abs(mw - ini[0]) < 2 and abs(mh - ini[1]) < 2 and abs(mx) < 2 and abs(my) < 2:
        return None, ini
    return [mx, my, mw, mh], ini


def board_scale(objs):
    ratios = []
    for o in objs:
        if o["mode"] != "image":
            continue
        x, y, w, h = box(o)
        crop, ini = image_crop(o)
        if not ini or w <= 0:
            continue
        ratios.append((crop[2] if crop else ini[0]) / w)
    f = statistics.median(ratios) if ratios else None
    fonts = sorted(o["textPayload"]["fontSize"] for o in objs
                   if o["mode"] in ("text", "shape", "sticker") and o.get("textPayload") and plain_text(o["textPayload"].get("textOps")).strip()
                   and o["textPayload"].get("fontSize"))
    if f is None:
        f = 36.0 / statistics.median(fonts) if fonts else 1.0
    if fonts:
        p10 = fonts[max(0, int(len(fonts) * 0.1) - 1)]
        f = max(f, 14.0 / p10)          # мелкий текст не должен стать меньше 14 px в Miro
    return max(0.02, min(4.0, f))


def build(bid, rec, extra_pencil, outdir, jobs):
    objs = collect(rec, extra_pencil)
    if not objs:
        return {"bid": bid, "parts": 0, "items": 0}
    f = board_scale(objs)
    # центр доски
    xs, ys = [], []
    for o in objs:
        if o["mode"] in ("line", "bezierCurve"):
            for p in (o["graphicsPayload"].get("graphicsParams") or {}).get("anchorPoints") or []:
                xs.append(p["x"]); ys.append(p["y"])
        elif o["mode"] != "nodeConnector":
            x, y, w, h = box(o)
            if w or h:
                xs += [x, x + w]; ys += [y, y + h]
    cx, cy = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2) if xs else (0, 0)
    T = lambda x, y: ((x - cx) * f, (y - cy) * f)
    byid = {o["id"]: o for o in objs}

    elems, strokes = [], []          # elems: (z, svg); strokes рисуем в слой
    z = lambda o: (0 if o["mode"] == "frame" else 1, float(o.get("zIndex") or 0))
    n = 0
    for o in sorted(objs, key=z):
        m = o["mode"]; n += 1
        g = o["graphicsPayload"]; gp = g.get("graphicsParams") or {}; pr = o.get("graphicsProps") or {}
        tp = o.get("textPayload") or {}
        ang = round(float(g.get("angle") or 0)) % 360
        x, y, w, h = box(o)
        X, Y = T(x, y); W, H = w * f, h * f
        rot = f' data-rotation="{ang}"' if ang else ""
        if m == "image":
            crop, ini = image_crop(o)
            url = o.get("imageSrc")
            if not url:
                continue
            if crop or ang:
                key = hashlib.sha1((url + json.dumps(crop) + str(ang)).encode()).hexdigest()[:20]
                jobs[key] = {"url": url, "crop": crop, "initial": ini, "angle": ang}
                url = RAW + f"img/{key}.jpg"
                if ang in (90, 270):
                    cxo, cyo = X + W / 2, Y + H / 2
                    W, H = H, W
                    X, Y = cxo - W / 2, cyo - H / 2
            elems.append(f'<image id="o{n}" data-type="image" href="{esc(url)}" x="{X:.1f}" y="{Y:.1f}" width="{max(W,8):.1f}" height="{max(H,8):.1f}" />')
        elif m == "text":
            body, color = ops_to_html(tp.get("textOps"))
            if not body.strip():
                continue
            fs = max(6.0, float(tp.get("fontSize") or 14) * f)
            align = (tp.get("textAlign") or {}).get("horizontal", "left")
            align = align if align in ("left", "center", "right") else "left"
            fam = FONTS.get(tp.get("fontFamily"), "open_sans")
            Wt = W if W > 4 else max(40, len(plain_text(tp.get("textOps"))) * fs * 0.6)
            elems.append(f'<textArea id="o{n}" x="{X:.1f}" y="{Y:.1f}" width="{Wt:.1f}" font-size="{fs:.1f}" font-family="{fam}" fill="{color}" text-align="{align}"{rot}>{body}</textArea>')
        elif m in ("shape", "frame"):
            if m == "frame":
                fill = blend(pr.get("fillColor"), float(pr.get("fillAlpha", 0.4)))
                elems.append(f'<rect id="o{n}" x="{X:.1f}" y="{Y:.1f}" width="{max(W,8):.1f}" height="{max(H,8):.1f}" fill="{fill}" stroke="{hex6(pr.get("color"), "#c7c7c7")}" stroke-width="2" />')
                continue
            shp = gp.get("shape", "rectangle")
            fa = float(pr.get("fillAlpha", 1)) * alpha_of(pr.get("fillColor"))
            fill = "none" if fa < 0.05 else (hex6(pr.get("fillColor"), "#ffffff") if fa > 0.95 else blend(pr.get("fillColor"), fa))
            sa = float(pr.get("figuresStrokeAlpha", 1))
            stroke = "none" if sa < 0.05 else hex6(pr.get("color"), "#000000")
            sw = max(2, min(8, round(float(pr.get("stroke", 2)))))
            body, color = ops_to_html(tp.get("textOps"))
            fs = max(6.0, float(tp.get("fontSize") or 14) * f)
            fam = FONTS.get(tp.get("fontFamily"), "open_sans")
            al = tp.get("textAlign") or {}
            ha = al.get("horizontal", "center"); va = al.get("vertical", "middle")
            txt = (f' data-content="{esc(body)}" data-text-color="{color}" data-font-size="{fs:.1f}" data-font-family="{fam}"'
                   f' data-text-align="{ha if ha in ("left","center","right") else "center"}" data-text-align-vertical="{va if va in ("top","middle","bottom") else "middle"}"') if body.strip() else ""
            sattr = f'stroke="{stroke}"' + (f' stroke-width="{sw}"' if stroke != "none" else "")
            if pr.get("strokeStyle") == "dashed": sattr += ' stroke-dasharray="5,5"'
            if pr.get("strokeStyle") == "dotted": sattr += ' stroke-dasharray="2,2"'
            if shp == "ellipse":
                elems.append(f'<ellipse id="o{n}" cx="{X+W/2:.1f}" cy="{Y+H/2:.1f}" rx="{max(W,8)/2:.1f}" ry="{max(H,8)/2:.1f}" fill="{fill}" {sattr}{txt}{rot} />')
            else:
                elems.append(f'<rect id="o{n}" data-shape="{SHAPES.get(shp, "rectangle")}" x="{X:.1f}" y="{Y:.1f}" width="{max(W,8):.1f}" height="{max(H,8):.1f}" fill="{fill}" {sattr}{txt}{rot} />')
        elif m == "sticker":
            body, color = ops_to_html(tp.get("textOps"))
            col = STICKY.get(hex6(pr.get("color"), "#fff9a3"), "light_yellow")
            Ws = max(W, 60); Hs = Ws * 1.15
            elems.append(f'<rect id="o{n}" data-type="sticky" x="{X:.1f}" y="{Y:.1f}" width="{Ws:.1f}" height="{Hs:.1f}" data-color="{col}" data-content="{esc(body)}" />')
        else:
            strokes.append(o)     # pencil / line / bezierCurve / table / nodeConnector → слой

    # ---------- слой пометок ----------
    ov_items = render_overlay(bid, strokes, byid, T, f)
    elems += ov_items

    # ---------- порции SVG ----------
    os.makedirs(os.path.join(outdir, "svg"), exist_ok=True)
    parts, cur, size = [], [], 0
    for e in elems:
        if size + len(e) > SVG_LIMIT and cur:
            parts.append(cur); cur, size = [], 0
        cur.append(e); size += len(e) + 1
    if cur:
        parts.append(cur)
    for k, p in enumerate(parts):
        open(os.path.join(outdir, "svg", f"{bid}_{k}.svg"), "w").write('<svg xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(p) + "\n</svg>\n")
    return {"bid": bid, "parts": len(parts), "items": len(elems), "scale": round(f, 4), "overlay_tiles": len(ov_items)}


def bezier_pts(pts, steps=24):
    if len(pts) == 3:
        (x0, y0), (x1, y1), (x2, y2) = pts
        return [((1-t)**2*x0 + 2*(1-t)*t*x1 + t*t*x2, (1-t)**2*y0 + 2*(1-t)*t*y1 + t*t*y2) for t in [i/steps for i in range(steps+1)]]
    if len(pts) == 4:
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
        return [((1-t)**3*x0 + 3*(1-t)**2*t*x1 + 3*(1-t)*t*t*x2 + t**3*x3, (1-t)**3*y0 + 3*(1-t)**2*t*y1 + 3*(1-t)*t*t*y2 + t**3*y3) for t in [i/steps for i in range(steps+1)]]
    return pts


def render_overlay(bid, strokes, byid, T, f):
    """Рисует пометки/линии/таблицы в прозрачные плитки; возвращает <image> для SVG."""
    if not strokes:
        return []
    prims = []   # ("line", [(X,Y)...], color, width_units, arrows) | ("table", ...)
    for o in strokes:
        m = o["mode"]; g = o["graphicsPayload"]; gp = g.get("graphicsParams") or {}; pr = o.get("graphicsProps") or {}
        col = hex6(pr.get("color"), "#000000"); sw = max(0.75, float(pr.get("stroke", 2)) * f)
        if m == "pencil":
            px, py, _, _ = box(o)
            pts = [T(px + p["x"], py + p["y"]) for p in gp.get("points") or []]
            if pts:
                prims.append(("line", pts, col, sw, None, None))
        elif m in ("line", "bezierCurve"):
            ap = [(p["x"], p["y"]) for p in gp.get("anchorPoints") or []]
            if m == "bezierCurve":
                ap = bezier_pts(ap)
            pts = [T(*p) for p in ap]
            if len(pts) >= 2 and (abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1])) > 0.5:
                prims.append(("line", pts, col, sw, gp.get("arrowStartType"), gp.get("arrowEndType")))
        elif m == "nodeConnector":
            a, b = byid.get(gp.get("primaryDrawingId")), byid.get(gp.get("secondaryDrawingId"))
            if a and b:
                ax, ay, aw, ah = box(a); bx, by, bw, bh = box(b)
                prims.append(("line", [T(ax + aw / 2, ay + ah / 2), T(bx + bw / 2, by + bh / 2)], col, sw, None, None))
        elif m == "table":
            x, y, w, h = box(o)
            rows = {r["id"]: r["size"] for r in gp.get("rows") or []}
            cols = {c["id"]: c["size"] for c in gp.get("columns") or []}
            ro = gp.get("rowsOrder") or list(rows); co = gp.get("columnsOrder") or list(cols)
            cells = {(c.get("rowId"), c.get("columnId")): c.get("textPayload") or {} for c in gp.get("cellsData") or []}
            prims.append(("table", (x, y), ro, co, rows, cols, cells, pr, f, T, float(gp.get("textScale") or 1)))
    # габариты
    def bbox(p):
        if p[0] == "line":
            xs = [q[0] for q in p[1]]; ys = [q[1] for q in p[1]]; pad = p[3] * 2 + 20
            return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
        (x, y) = p[1]; X0, Y0 = p[9](x, y)
        tw = sum(p[5][c] for c in p[3] if c in p[5]) * p[8]; th = sum(p[4][r] for r in p[2] if r in p[4]) * p[8]
        return X0 - 4, Y0 - 4, X0 + tw + 4, Y0 + th + 4
    boxes = [bbox(p) for p in prims]
    if not boxes:
        return []
    tiles = {}
    for p, (x0, y0, x1, y1) in zip(prims, boxes):
        for tj in range(math.floor(y0 / TILE), math.floor(y1 / TILE) + 1):
            for ti in range(math.floor(x0 / TILE), math.floor(x1 / TILE) + 1):
                tiles.setdefault((ti, tj), []).append(p)
    od = os.path.join(REPO, "ov", bid); os.makedirs(od, exist_ok=True)
    for fn in os.listdir(od):
        os.remove(os.path.join(od, fn))
    out = []
    S = int(TILE * R)
    for (ti, tj), ps in sorted(tiles.items()):
        ox, oy = ti * TILE, tj * TILE
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        P = lambda X, Y: ((X - ox) * R, (Y - oy) * R)
        for p in ps:
            if p[0] == "line":
                pts = [P(*q) for q in p[1]]; wpx = max(1, round(p[3] * R))
                d.line(pts, fill=p[2], width=wpx, joint="curve")
                for q in (pts[0], pts[-1]):
                    d.ellipse([q[0] - wpx / 2, q[1] - wpx / 2, q[0] + wpx / 2, q[1] + wpx / 2], fill=p[2])
                for which, kind in (("s", p[4]), ("e", p[5])):
                    if kind and kind not in ("empty", "none"):
                        a, b = (pts[1], pts[0]) if which == "s" else (pts[-2], pts[-1])
                        ang = math.atan2(b[1] - a[1], b[0] - a[0]); L = max(10, wpx * 5)
                        tri = [b, (b[0] - L * math.cos(ang - 0.45), b[1] - L * math.sin(ang - 0.45)),
                               (b[0] - L * math.cos(ang + 0.45), b[1] - L * math.sin(ang + 0.45))]
                        d.polygon(tri, fill=p[2])
            else:
                _, (x, y), ro, co, rows, cols, cells, pr, ff, TT, ts = p
                X0, Y0 = P(*TT(x, y))
                line_col = hex6(pr.get("color"), "#7bb5f4")
                fillc = blend(pr.get("fillColor"), float(pr.get("fillAlpha", 1)))
                yy = Y0
                for r in ro:
                    rh = rows.get(r, 0) * ff * R; xx = X0
                    for c in co:
                        cw = cols.get(c, 0) * ff * R
                        d.rectangle([xx, yy, xx + cw, yy + rh], fill=fillc, outline=line_col, width=max(1, round(R)))
                        tp = cells.get((r, c))
                        if tp:
                            txt = plain_text(tp.get("textOps")).strip()
                            if txt:
                                fsz = max(8, int(float(tp.get("fontSize") or 14) * ts * ff * R))
                                colr = "#000000"
                                for op in tp.get("textOps") or []:
                                    if (op.get("attributes") or {}).get("color"):
                                        colr = hex6(op["attributes"]["color"]); break
                                bold = any((op.get("attributes") or {}).get("bold") for op in tp.get("textOps") or [])
                                font = ImageFont.truetype(FONT_B if bold else FONT, fsz)
                                lines = txt.split("\n")
                                tot = len(lines) * fsz * 1.2
                                for k, ln in enumerate(lines):
                                    tw = d.textlength(ln, font=font)
                                    d.text((xx + (cw - tw) / 2, yy + (rh - tot) / 2 + k * fsz * 1.2), ln, fill=colr, font=font)
                        xx += cw
                    yy += rh
        bb = im.getbbox()
        if not bb or (bb[2] - bb[0] < 8 and bb[3] - bb[1] < 8):
            continue
        im = im.crop(bb)
        fn = f"ov/{bid}/{ti}_{tj}.png"
        im.save(os.path.join(REPO, fn), optimize=True)
        X = ox + bb[0] / R; Y = oy + bb[1] / R
        oid = f"ov{ti}_{tj}".replace("-", "m")
        out.append(f'<image id="{oid}" data-type="image" href="{RAW}{fn}" x="{X:.1f}" y="{Y:.1f}" width="{im.size[0]/R:.1f}" height="{im.size[1]/R:.1f}" />')
    return out


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))["boards"]
    pencil = {}
    for pf in sys.argv[2].split(","):
        if pf and pf != "-" and os.path.exists(pf):
            for k, v in json.load(open(pf)).get("pencil", {}).items():
                pencil.setdefault(k, []).extend(v)
    outdir = sys.argv[3]
    only = set(sys.argv[4:])
    jobs_path = os.path.join(REPO, "jobs", "images.json")
    os.makedirs(os.path.dirname(jobs_path), exist_ok=True)
    jobs = json.load(open(jobs_path)) if os.path.exists(jobs_path) else {}
    report = {}
    for full, rec in data.items():
        bid = full[:8]
        if only and bid not in only:
            continue
        r = build(bid, rec, pencil.get(full), outdir, jobs)
        r["name"] = rec["name"]; r["sheet"] = rec["sheet"]
        report[bid] = r
        print(json.dumps(r, ensure_ascii=False))
    json.dump(jobs, open(jobs_path, "w"), indent=0)
    rp = os.path.join(outdir, "report.json")
    old = json.load(open(rp)) if os.path.exists(rp) else {}
    old.update(report); json.dump(old, open(rp, "w"), ensure_ascii=False, indent=1)
