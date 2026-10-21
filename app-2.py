"""PDF Splitter - break a huge PDF into chunks under a size (and page) limit.
Run:  pip install flask pikepdf  &&  python app.py   ->  http://localhost:5000
"""
import os, sys, uuid, threading, zipfile, shutil, time, webbrowser
from flask import Flask, request, jsonify, send_from_directory, render_template_string
import pikepdf

BASE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):  # packaged Mac app: keep files in a user folder
    WORK = os.path.expanduser("~/Documents/PDF Splitter Files")
else:
    WORK = os.path.join(BASE, "jobs")
os.makedirs(WORK, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024**3  # 5 GB upload cap
JOBS = {}


def split_pdf(src, outdir, max_mb=50, max_pages=1000, progress=None):
    """Greedy split: each part <= max_mb and <= max_pages. Returns list of dicts."""
    limit = max_mb * 1024 * 1024 * 0.97
    parts = []
    with pikepdf.open(src) as pdf:
        n = len(pdf.pages)
        avg = os.path.getsize(src) / max(n, 1)
        guess = max(1, min(max_pages, int(limit / avg * 0.85)))
        i, k = 0, 1
        while i < n:
            count = min(guess, n - i, max_pages)
            while True:
                path = os.path.join(outdir, f"part_{k:03d}.pdf")
                out = pikepdf.new()
                out.pages.extend(pdf.pages[i:i + count])
                out.save(path)
                out.close()
                size = os.path.getsize(path)
                if size <= limit or count == 1:
                    break
                count = max(1, int(count * limit / size * 0.9))
            parts.append({"name": os.path.basename(path), "pages": count,
                          "from": i + 1, "to": i + count,
                          "mb": round(size / 1048576, 2),
                          "over_limit": size > limit})
            # adapt next guess to observed density
            if size < limit * 0.7:
                guess = min(max_pages, max(1, int(count * limit / size * 0.9)))
            else:
                guess = count
            i += count
            k += 1
            if progress:
                progress(i, n, parts)
    return parts


def run_job(jid, src, max_mb, max_pages):
    job = JOBS[jid]
    outdir = os.path.join(WORK, jid, "out")
    os.makedirs(outdir, exist_ok=True)

    def prog(done, total, parts):
        job.update(done=done, total=total, parts=list(parts))

    try:
        job["state"] = "splitting"
        parts = split_pdf(src, outdir, max_mb, max_pages, prog)
        zpath = os.path.join(WORK, jid, "all_parts.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
            for p in parts:
                z.write(os.path.join(outdir, p["name"]), p["name"])
        job.update(state="done", parts=parts)
    except Exception as e:
        job.update(state="error", error=str(e))
    finally:
        try:
            os.remove(src)  # drop the big original
        except OSError:
            pass


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file"), 400
    max_mb = float(request.form.get("max_mb", 50))
    max_pages = int(request.form.get("max_pages", 1000))
    jid = uuid.uuid4().hex[:12]
    os.makedirs(os.path.join(WORK, jid), exist_ok=True)
    src = os.path.join(WORK, jid, "source.pdf")
    f.save(src)
    JOBS[jid] = {"state": "queued", "done": 0, "total": 0, "parts": []}
    threading.Thread(target=run_job, args=(jid, src, max_mb, max_pages), daemon=True).start()
    return jsonify(job=jid)


@app.get("/status/<jid>")
def status(jid):
    return jsonify(JOBS.get(jid, {"state": "unknown"}))


@app.get("/download/<jid>/<name>")
def download(jid, name):
    d = os.path.join(WORK, jid, "out")
    return send_from_directory(d, name, as_attachment=True)


@app.get("/zip/<jid>")
def zipall(jid):
    return send_from_directory(os.path.join(WORK, jid), "all_parts.zip", as_attachment=True)


@app.post("/clean/<jid>")
def clean(jid):
    shutil.rmtree(os.path.join(WORK, jid), ignore_errors=True)
    JOBS.pop(jid, None)
    return jsonify(ok=True)


PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PDF Splitter</title><style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:30px auto;padding:0 16px;color:#222}
.box{border:2px dashed #999;border-radius:12px;padding:34px;text-align:center;cursor:pointer}
.box.drag{background:#eef6ff;border-color:#2b7cff}
.row{display:flex;gap:16px;margin:14px 0}label{flex:1;font-size:14px}
input[type=number]{width:100%;padding:8px;box-sizing:border-box}
.bar{height:14px;background:#eee;border-radius:7px;overflow:hidden;margin:6px 0}
.bar>div{height:100%;width:0;background:#2b7cff;transition:width .2s}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}
td,th{padding:6px 8px;border-bottom:1px solid #ddd;text-align:left}
button,.btn{background:#2b7cff;color:#fff;border:0;border-radius:8px;padding:9px 16px;text-decoration:none;font-size:14px;cursor:pointer}
.warn{color:#c0392b}.muted{color:#666;font-size:13px}
</style></head><body>
<h2>PDF Splitter <button style="float:right;background:#888" onclick="if(confirm('Close PDF Splitter?'))fetch('/quit',{method:'POST'}).then(()=>document.body.innerHTML='<p>Closed. You can close this tab.</p>')">Quit</button></h2>
<p class=muted>Upload one large PDF. Get smaller parts that fit Gemini's limits (50 MB / 1000 pages per PDF).</p>
<div class=row>
<label>Max size per part (MB)<input id=mb type=number value=50 min=1 max=50></label>
<label>Max pages per part<input id=pg type=number value=1000 min=1 max=1000></label></div>
<div class=box id=drop>Drop PDF here or click to choose<input id=file type=file accept=application/pdf hidden></div>
<div id=st style="margin-top:16px"></div><div class=bar><div id=b></div></div>
<div id=res></div>
<script>
const $=id=>document.getElementById(id),drop=$('drop'),file=$('file');
drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag')};
drop.ondragleave=()=>drop.classList.remove('drag');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag');go(e.dataTransfer.files[0])};
file.onchange=()=>go(file.files[0]);
function go(f){
 if(!f)return;
 const fd=new FormData();fd.append('file',f);fd.append('max_mb',$('mb').value);fd.append('max_pages',$('pg').value);
 const x=new XMLHttpRequest();x.open('POST','/upload');
 x.upload.onprogress=e=>{if(e.lengthComputable){$('b').style.width=(e.loaded/e.total*100)+'%';$('st').textContent='Uploading '+f.name+' '+Math.round(e.loaded/e.total*100)+'%'}};
 x.onload=()=>{const j=JSON.parse(x.responseText).job;if(!j){$('st').textContent='Upload failed';return}poll(j)};
 x.onerror=()=>$('st').textContent='Upload failed';
 x.send(fd);
}
function poll(j){
 fetch('/status/'+j).then(r=>r.json()).then(s=>{
  if(s.state=='error'){$('st').innerHTML='<span class=warn>Error: '+s.error+'</span>';return}
  if(s.total)$('b').style.width=(s.done/s.total*100)+'%';
  $('st').textContent=s.state=='done'?'Done - '+s.parts.length+' parts':'Splitting... '+s.done+' / '+s.total+' pages';
  if(s.parts.length)render(j,s);
  if(s.state!='done')setTimeout(()=>poll(j),1000);
 });
}
function render(j,s){
 let h=s.state=='done'?'<p><a class=btn href=/zip/'+j+'>Download all (ZIP)</a></p>':'';
 h+='<table><tr><th>Part<th>Pages<th>Size<th></tr>';
 s.parts.forEach(p=>h+='<tr><td>'+p.name+'<td>'+p.from+'-'+p.to+'<td class="'+(p.over_limit?'warn':'')+'">'+p.mb+' MB'+(p.over_limit?' (single page over limit)':'')+'<td><a href=/download/'+j+'/'+p.name+'>download</a></tr>');
 $('res').innerHTML=h+'</table>';
}
</script></body></html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/quit")
def quit_app():
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


def cleanup_old(hours=24):
    """Delete job folders older than N hours so disk doesn't fill up."""
    now = time.time()
    for d in os.listdir(WORK):
        p = os.path.join(WORK, d)
        if os.path.isdir(p) and now - os.path.getmtime(p) > hours * 3600:
            shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    cleanup_old()
    if getattr(sys, "frozen", False):  # double-clicked app: local only + open browser
        sys.stdout = sys.stderr = open(os.devnull, "w")  # no console in packaged app
        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
        app.run(host="127.0.0.1", port=5000, threaded=True)
    else:
        app.run(host="0.0.0.0", port=5000, threaded=True)
