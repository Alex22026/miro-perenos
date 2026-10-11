"""Запускается в GitHub Actions: скачивает картинки Sboard, обрезает/поворачивает, кладёт в img/<key>.jpg"""
import json, os, io, urllib.request, urllib.parse, concurrent.futures as cf
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
jobs = json.load(open("jobs/images.json"))
os.makedirs("img", exist_ok=True)
todo = {k: v for k, v in jobs.items() if not os.path.exists(f"img/{k}.jpg")}
print("to do:", len(todo))
def work(item):
    k, j = item
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(urllib.request.Request(urllib.parse.quote(j["url"], safe=":/?&=%#+@,;~"), headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
            im = Image.open(io.BytesIO(data))
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
            if j.get("crop"):
                x, y, w, h = j["crop"]; iw, ih = j.get("initial") or im.size
                sx, sy = im.size[0] / iw, im.size[1] / ih
                im = im.crop((round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)))
            a = int(j.get("angle") or 0) % 360
            if a:
                im = im.rotate(-a, expand=True)     # Sboard: угол по часовой
            if im.mode == "RGBA":
                bg = Image.new("RGB", im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[-1]); im = bg
            im.save(f"img/{k}.jpg", "JPEG", quality=92)
            return k, "ok"
        except Exception as e:
            err = str(e)
    return k, "ERR " + err
with cf.ThreadPoolExecutor(16) as ex:
    res = list(ex.map(work, todo.items()))
bad = [r for r in res if r[1] != "ok"]
print("done", len(res) - len(bad), "errors", len(bad))
for b in bad[:20]: print(b)
json.dump(dict(bad), open("jobs/errors.json", "w"), indent=0)
