#!/usr/bin/env python3
"""CTB listings agent — runs in GitHub Actions (open internet).

Processes job files in listings/queue/:
  {"action":"add","url":"https://www.propertyguru.com.sg/listing/..."}
  {"action":"update","id":"500206939","fields":{"status":"sold","price":830000}}
  {"action":"remove","id":"500206939"}

For 'add': fetches the PG page, parses it (JSON-LD / __NEXT_DATA__ / og: metas /
text patterns, in that order of trust), downloads photos + floor plan into
listings/assets/<slug>/, and upserts the entry in listings/listings.json.
Writes a parse report to listings/reports/ for every add so failures are debuggable.
"""
import json, re, os, sys, glob, html, datetime, pathlib

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
LJSON = ROOT / "listings" / "listings.json"
QUEUE = ROOT / "listings" / "queue"
ASSETS = ROOT / "listings" / "assets"
REPORTS = ROOT / "listings" / "reports"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-SG,en;q=0.9"}

def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load_db():
    db = json.loads(LJSON.read_text())
    db.setdefault("listings", [])
    return db

def save_db(db):
    db["updated"] = now()
    db["listings"].sort(key=lambda l: (l.get("status") != "available", l.get("added","")), reverse=False)
    LJSON.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n")

def rx(pattern, text, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None

def parse_pg(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    page = r.text
    notes = []
    d = {"pgUrl": url.split("?")[0]}
    d["id"] = rx(r"listing/(?:[a-z0-9\-]*?)(\d{6,})", url) or rx(r"Listing ID[^0-9]*(\d{6,})", page) or "unknown"

    # 1. JSON-LD blocks
    ld = {}
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            j = json.loads(m.group(1))
            for obj in (j if isinstance(j, list) else [j]):
                ld.update({k: v for k, v in obj.items() if k in ("name","description","address","offers","image")})
        except Exception:
            pass
    if ld: notes.append("json-ld found")

    # 2. og metas
    def og(prop):
        return rx(r'<meta[^>]+(?:property|name)="og:%s"[^>]+content="([^"]*)"' % prop, page) or \
               rx(r'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="og:%s"' % prop, page)
    ogd = og("description") or ""
    d["title"] = rx(r"<title>([^|<]+?)(?:HDB|Condo|Apartment|For Sale|\|)", page) or og("title") or (ld.get("name") or "")
    d["title"] = html.unescape(d["title"]).strip()

    text = html.unescape(re.sub(r"<[^>]+>", " ", page))

    d["price"] = None
    for pat in (r'"price"\s*:\s*"?([\d,.]+)"?', r"S\$\s*([\d,]{6,})"):
        v = rx(pat, page)
        if v:
            try:
                d["price"] = int(float(v.replace(",", ""))); break
            except ValueError: continue
    d["beds"]  = rx(r'"bedrooms?"\s*:\s*"?(\d+)', page) or rx(r"(\d+)\s*Beds", text)
    d["baths"] = rx(r'"bathrooms?"\s*:\s*"?(\d+)', page) or rx(r"(\d+)\s*Baths", text)
    d["sqft"]  = rx(r'"floorArea"\s*:\s*"?([\d,]+)', page) or rx(r"([\d,]{3,6})\s*sqft", text)
    d["psf"]   = rx(r"S\$\s*([\d,]+(?:\.\d+)?)\s*psf", text)
    d["district"] = rx(r"\(D(\d{1,2})", text)
    d["ptype"] = rx(r"(\d(?:[A-Z])?(?:\s*Room)?\s*HDB|Executive Condominium|Condominium|Apartment|Terrace(?:d)? House|Semi-Detached House|Detached House|Bungalow)\s+for sale", text) or \
                 ("HDB" if "hdb" in url else "Condominium")
    d["tenure"] = rx(r"(\d{2,4}-year lease|Freehold)", text)
    d["top"] = rx(r"(?:TOP|Built|completed)[^\d]{0,8}(\d{4})", text)
    d["listedOn"] = rx(r"Listed on\s+(\d{1,2}\s+\w{3}\s+\d{4})", text)
    d["description"] = html.unescape(ogd).strip()

    # media
    photos = []
    for m in re.finditer(r'https://sg\d-cdn\.pgimgs\.com/listing/%s/UPHO[^"\s\\]+?\.(?:V800|V550)[^"\s\\]*' % d["id"], page):
        u = m.group(0).replace(".V550", ".V800")
        if u not in photos: photos.append(u)
    d["photoUrls"] = photos[:12]
    d["floorplanUrl"] = rx(r'(https://sg\d-cdn\.pgimgs\.com/listing/%s/UFLOO[^"\s\\]+)' % d["id"], page)
    d["tour"] = rx(r'(https://my\.matterport\.com/show/\?m=[A-Za-z0-9]+)', page)
    d["video"] = rx(r'(https://(?:youtu\.be/|www\.youtube\.com/watch\?v=)[A-Za-z0-9_\-]+)', page)
    return d, notes, page

def slugify(t):
    s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return s[:60] or "listing"

def download_media(d, slug):
    folder = ASSETS / slug
    folder.mkdir(parents=True, exist_ok=True)
    local = []
    for i, u in enumerate(d.get("photoUrls", []), 1):
        try:
            b = requests.get(u, headers=UA, timeout=30).content
            p = folder / f"photo_{i:02d}.jpg"
            p.write_bytes(b)
            local.append(f"listings/assets/{slug}/photo_{i:02d}.jpg")
        except Exception as e:
            print(f"  photo {i} failed: {e}")
    fp = None
    if d.get("floorplanUrl"):
        try:
            b = requests.get(d["floorplanUrl"], headers=UA, timeout=30).content
            (folder / "floorplan.jpg").write_bytes(b)
            fp = f"listings/assets/{slug}/floorplan.jpg"
        except Exception as e:
            print(f"  floorplan failed: {e}")
    return local, fp

def do_add(job):
    d, notes, page = parse_pg(job["url"])
    slug = slugify(d.get("title") or d["id"])
    photos, floorplan = download_media(d, slug)
    entry = {
        "id": d["id"], "slug": slug, "title": d.get("title"),
        "price": d.get("price"),
        "beds": int(d["beds"]) if d.get("beds") else None,
        "baths": int(d["baths"]) if d.get("baths") else None,
        "sqft": d.get("sqft"), "psf": d.get("psf"),
        "district": d.get("district"), "ptype": d.get("ptype"),
        "tenure": d.get("tenure"), "top": d.get("top"),
        "listedOn": d.get("listedOn"),
        "description": d.get("description"),
        "photos": photos, "floorplan": floorplan,
        "photoUrls": d.get("photoUrls"), "floorplanUrl": d.get("floorplanUrl"),
        "tour": d.get("tour"), "video": d.get("video"),
        "pgUrl": d.get("pgUrl"),
        "status": "available", "featured": True,
        "agent": "Chloe Teo", "added": now(),
    }
    # allow manual overrides supplied with the job
    entry.update(job.get("fields", {}))
    db = load_db()
    db["listings"] = [l for l in db["listings"] if l.get("id") != entry["id"]] + [entry]
    save_db(db)
    REPORTS.mkdir(parents=True, exist_ok=True)
    missing = [k for k in ("title","price","beds","sqft","psf","description") if not entry.get(k)]
    (REPORTS / f"{entry['id']}.md").write_text(
        f"# Parse report — {entry['id']}\n\nURL: {job['url']}\nWhen: {now()}\nNotes: {', '.join(notes) or 'regex path'}\n"
        f"Photos: {len(photos)} downloaded / {len(d.get('photoUrls') or [])} found · floorplan: {'yes' if floorplan else 'no'}\n"
        f"Missing fields: {', '.join(missing) if missing else 'none'}\n")
    print(f"added {entry['id']} ({slug}): {len(photos)} photos, missing={missing}")

def do_update(job):
    db = load_db()
    for l in db["listings"]:
        if l.get("id") == str(job.get("id")):
            l.update(job.get("fields", {}))
            print(f"updated {l['id']}: {job.get('fields')}")
    save_db(db)

def do_remove(job):
    db = load_db()
    before = len(db["listings"])
    db["listings"] = [l for l in db["listings"] if l.get("id") != str(job.get("id"))]
    save_db(db)
    print(f"removed {job.get('id')} ({before-len(db['listings'])} entries)")

def main():
    jobs = sorted(glob.glob(str(QUEUE / "*.json")))
    if not jobs:
        print("queue empty"); return
    for jf in jobs:
        try:
            job = json.loads(open(jf).read())
            print(f"processing {os.path.basename(jf)}: {job.get('action')}")
            {"add": do_add, "update": do_update, "remove": do_remove}[job["action"]](job)
        except Exception as e:
            REPORTS.mkdir(parents=True, exist_ok=True)
            (REPORTS / f"error-{os.path.basename(jf)}.md").write_text(f"Job failed: {e}\n\n{json.dumps(job, indent=2) if 'job' in dir() else open(jf).read()}\n")
            print(f"FAILED {jf}: {e}", file=sys.stderr)
        finally:
            os.remove(jf)

if __name__ == "__main__":
    main()
