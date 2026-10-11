"""GitHub Actions: создаёт доски в Miro по REST API из svg/<bid>_*.svg.
Секрет MIRO_TOKEN. Вход: jobs/upload.json = [{"bid","name","sheet"}]. Выход: results/links.json, results/log_<run>.txt
Уже созданные доски (есть в links.json со статусом done) пропускаются; недоделанная доска пересоздаётся."""
import os, re, json, urllib.parse, time, html, glob, threading, urllib.request, urllib.error, concurrent.futures as cf

TOKEN = os.environ["MIRO_TOKEN"]
API = "https://api.miro.com/v2"
LOCK = threading.Lock()
LOGF = open(f"results/log_{os.environ.get('GITHUB_RUN_ID','local')}.txt", "a")


def log(*a):
    s = " ".join(str(x) for x in a)
    with LOCK:
        print(s, flush=True); LOGF.write(s + "\n"); LOGF.flush()


def call(method, path, body=None, tries=6):
    data = json.dumps(body).encode() if body is not None else None
    for t in range(tries):
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                txt = r.read().decode()
                return r.status, (json.loads(txt) if txt else {})
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="ignore")
            if e.code == 429 or e.code >= 500:
                wait = float(e.headers.get("Retry-After") or (2 + 3 * t))
                time.sleep(min(wait, 60)); continue
            try: return e.code, json.loads(txt)
            except Exception: return e.code, {"message": txt[:300]}
        except Exception as e:
            time.sleep(2 + 3 * t); last = str(e)
    return 0, {"message": "network: " + locals().get("last", "?")}


ATTR = re.compile(r'([\w:-]+)="([^"]*)"')


def attrs(tag):
    return {k: html.unescape(v) for k, v in ATTR.findall(tag)}


def to_html(body):
    """наш html (<b>,<i>,<u>,<s>,<br/>) → html Miro с абзацами"""
    body = body.replace("<br/>", "\n")
    paras = body.split("\n")
    return "".join(f"<p>{p if p.strip() else '&nbsp;'}</p>" for p in paras) or "<p></p>"


def fnum(v, d=0.0):
    try: return float(v)
    except Exception: return d


def items_from_svg(path):
    raw = open(path, encoding="utf-8").read()
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("<image"):
            a = attrs(line)
            x, y, w, h = fnum(a["x"]), fnum(a["y"]), max(8, fnum(a["width"])), max(8, fnum(a["height"]))
            yield ("images", {"data": {"url": urllib.parse.quote(a["href"], safe=":/?&=%#+@,;~")}, "position": {"x": x + w / 2, "y": y + h / 2, "origin": "center"},
                              "geometry": {"width": w}})
        elif line.startswith("<textArea"):
            m = re.match(r"<textArea([^>]*)>(.*)</textArea>$", line)
            if not m: continue
            a = attrs(m.group(1)); body = m.group(2)
            x, y, w = fnum(a["x"]), fnum(a["y"]), max(10, fnum(a["width"]))
            fs = fnum(a.get("font-size"), 14)
            w = max(w, fs * 1.6)          # Miro не принимает текст уже ~1.5 кегля
            # высоту заранее не знаем: ставим по верхнему краю через оценку одной строки
            yield ("texts", {"data": {"content": to_html(body)},
                             "style": {"color": a.get("fill", "#1a1a1a"), "fontFamily": a.get("font-family", "open_sans"),
                                       "fontSize": str(max(1, round(fs))), "textAlign": a.get("text-align", "left")},
                             "position": {"x": x + w / 2, "y": y + (body.count("<br/>") + 1) * fs * 1.43 / 2, "origin": "center"},
                             "geometry": {"width": w, **({"rotation": fnum(a["data-rotation"])} if a.get("data-rotation") else {})},
                             "_fs": fs})
        elif line.startswith("<rect") or line.startswith("<ellipse"):
            a = attrs(line)
            if a.get("data-type") == "sticky":
                x, y, w = fnum(a["x"]), fnum(a["y"]), max(60, fnum(a["width"]))
                yield ("sticky_notes", {"data": {"content": to_html(a.get("data-content", "")), "shape": "square"},
                                        "style": {"fillColor": a.get("data-color", "light_yellow"), "textAlign": "center", "textAlignVertical": "middle"},
                                        "position": {"x": x + w / 2, "y": y + w * 1.15 / 2, "origin": "center"}, "geometry": {"width": w}})
                continue
            if line.startswith("<ellipse"):
                cx, cy, rx, ry = fnum(a["cx"]), fnum(a["cy"]), fnum(a["rx"]), fnum(a["ry"])
                shape, x, y, w, h = "circle", cx - rx, cy - ry, 2 * rx, 2 * ry
            else:
                shape = a.get("data-shape", "rectangle")
                x, y, w, h = fnum(a["x"]), fnum(a["y"]), fnum(a["width"]), fnum(a["height"])
            fill = a.get("fill", "#ffffff"); stroke = a.get("stroke", "none")
            style = {"fillColor": fill if fill.startswith("#") else "#ffffff", "fillOpacity": "0.0" if fill == "none" else "1.0",
                     "borderColor": stroke if stroke.startswith("#") else "#000000",
                     "borderOpacity": "0.0" if stroke == "none" else "1.0",
                     "borderWidth": str(int(min(24, max(1, fnum(a.get("stroke-width"), 2))))),
                     "borderStyle": {"5,5": "dashed", "2,2": "dotted"}.get(a.get("stroke-dasharray", ""), "normal")}
            data = {"shape": shape}
            if a.get("data-content"):
                data["content"] = to_html(a["data-content"])
                style.update({"color": a.get("data-text-color", "#1a1a1a"), "fontFamily": a.get("data-font-family", "open_sans"),
                              "fontSize": str(int(min(288, max(10, round(fnum(a.get("data-font-size"), 14)))))),
                              "textAlign": a.get("data-text-align", "center"), "textAlignVertical": a.get("data-text-align-vertical", "middle")})
            geom = {"width": max(8, w), "height": max(8, h)}
            if a.get("data-rotation"): geom["rotation"] = fnum(a["data-rotation"])
            yield ("shapes", {"data": data, "style": style, "position": {"x": x + w / 2, "y": y + h / 2, "origin": "center"}, "geometry": geom})


def post_item(bid_m, kind, body):
    body = dict(body); fs = body.pop("_fs", None)
    st, r = call("POST", f"/boards/{bid_m}/{kind}", body)
    if st in (200, 201):
        return True, None
    msg = json.dumps(r, ensure_ascii=False)[:300]
    # запасные варианты
    if kind == "texts" and fs is not None:
        b2 = json.loads(json.dumps(body)); b2["geometry"]["width"] = max(b2["geometry"]["width"], fs * 3)
        st, r = call("POST", f"/boards/{bid_m}/{kind}", b2)
        if st in (200, 201): return True, "text widened"
        log("   text err:", msg)
        for size in ("288", "14"):
            b2 = json.loads(json.dumps(body)); b2["style"]["fontSize"] = size if size == "14" else str(min(288, max(10, round(fs))))
            st, r = call("POST", f"/boards/{bid_m}/{kind}", b2)
            if st in (200, 201): return True, "fontSize->" + b2["style"]["fontSize"]
    if kind == "shapes":
        b2 = json.loads(json.dumps(body)); b2["data"]["shape"] = "rectangle" if b2["data"]["shape"] not in ("rectangle", "circle") else b2["data"]["shape"]
        b2["style"].pop("fontFamily", None)
        st, r = call("POST", f"/boards/{bid_m}/{kind}", b2)
        if st in (200, 201): return True, "shape fallback"
    if kind == "sticky_notes":
        b2 = json.loads(json.dumps(body)); b2["style"]["fillColor"] = "light_yellow"
        st, r = call("POST", f"/boards/{bid_m}/{kind}", b2)
        if st in (200, 201): return True, "sticky fallback"
    return False, msg


def do_board(job, links):
    bid, name = job["bid"], job["name"]
    files = sorted(glob.glob(f"svg/{bid}_*.svg"), key=lambda p: int(re.search(r"_(\d+)\.svg$", p).group(1)))
    if not files:
        log("NO SVG", bid, name); return
    body = {"name": name[:60], "description": name[:300], "policy": {"sharingPolicy": {"access": "edit"}}}
    st, r = call("POST", "/boards", body)
    if st not in (200, 201):
        log("board policy failed", bid, st, json.dumps(r, ensure_ascii=False)[:200])
        st, r = call("POST", "/boards", {"name": name[:60], "description": name[:300]})
    if st not in (200, 201):
        log("BOARD FAIL", bid, st, json.dumps(r, ensure_ascii=False)[:300]); return
    mid, view = r["id"], r.get("viewLink")
    with LOCK:
        links[bid] = {"name": name, "sheet": job.get("sheet"), "miro": view, "id": mid, "status": "creating"}
    ok = bad = 0; notes = []
    for fpath in files:
        for kind, b in items_from_svg(fpath):
            good, note = post_item(mid, kind, b)
            if good:
                ok += 1
                if note: notes.append(note)
            else:
                bad += 1; notes.append(f"{kind}: {note}")
    with LOCK:
        links[bid].update({"status": "done", "ok": ok, "failed": bad})
    log(f"DONE {bid} {name[:40]} ok={ok} failed={bad} {view}")
    for n in notes[:8]:
        log("   ", n)


def main():
    jobs = json.load(open("jobs/upload.json"))
    lp = "results/links.json"
    links = json.load(open(lp)) if os.path.exists(lp) else {}
    todo = [j for j in jobs if links.get(j["bid"], {}).get("status") != "done"]
    log("to do:", len(todo), "of", len(jobs))
    t0 = time.time(); done_since_commit = 0

    def save():
        with LOCK:
            json.dump(links, open(lp, "w"), ensure_ascii=False, indent=1)

    with cf.ThreadPoolExecutor(4) as ex:
        futs = [ex.submit(do_board, j, links) for j in todo]
        for k, fu in enumerate(cf.as_completed(futs)):
            try: fu.result()
            except Exception as e: log("ERROR", repr(e))
            save()
            done_since_commit += 1
            if done_since_commit >= 10:
                done_since_commit = 0
                os.system('git add results && git commit -qm "Промежуточные ссылки Miro" && (git pull -q --rebase && git push -q) || true')
            if time.time() - t0 > 5.3 * 3600:
                log("time limit, stopping"); break
    save()
    log("finished in", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
