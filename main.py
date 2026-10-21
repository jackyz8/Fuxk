#!/usr/bin/env python3
"""GLORY HOST Cloud premium panel. Python standard library only."""
import os, sys, json, time, uuid, secrets, shutil, zipfile, io, re, shlex, threading, subprocess, logging
from pathlib import Path
from collections import deque
from urllib.parse import urlparse, parse_qs, quote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from email.parser import BytesParser
from email.policy import default

# ==================== ADMIN CONFIG ====================
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "CHANGE_THIS_PASSWORD_NOW"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
MAX_UPLOAD = 100 * 1024 * 1024
ROOT = Path(__file__).resolve().parent / "workspace"
# ======================================================
ROOT.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("glory-host")
SESSIONS, APPS, JOBS = {}, {}, {}
LOCK = threading.RLock()


def safe_path(raw=""):
    raw = str(raw or "").replace("\\", "/")
    if "\x00" in raw or raw.startswith("/") or any(p == ".." for p in raw.split("/")):
        raise ValueError("Invalid path: use a workspace-relative path.")
    p = (ROOT / raw).resolve()
    try: p.relative_to(ROOT.resolve())
    except ValueError: raise ValueError("Path must remain inside workspace.")
    cur = ROOT.resolve()
    for part in Path(raw).parts:
        if part in ("", "."): continue
        cur = cur / part
        if cur.is_symlink(): raise ValueError("Symbolic links are not supported.")
    return p


def rel(p):
    return "" if p.resolve() == ROOT.resolve() else p.resolve().relative_to(ROOT.resolve()).as_posix()


def valid_name(n):
    n = str(n or "").strip()
    if not n or n in (".", "..") or "/" in n or "\\" in n or "\x00" in n:
        raise ValueError("Enter a simple file or folder name.")
    return n


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB": return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def file_count():
    return sum(len(fs) for _, _, fs in os.walk(ROOT))


def tree_size():
    total = 0
    for r, ds, fs in os.walk(ROOT):
        ds[:] = [d for d in ds if not (Path(r)/d).is_symlink()]
        for f in fs:
            p = Path(r)/f
            try:
                if not p.is_symlink(): total += p.stat().st_size
            except OSError: pass
    return total


def alive(a): return bool(a.get("proc") and a["proc"].poll() is None)


def stop_app(key):
    with LOCK:
        a = APPS.get(key)
        if not a: return False
        p = a.get("proc")
        if p and p.poll() is None:
            try: p.terminate(); p.wait(timeout=4)
            except Exception:
                try: p.kill()
                except Exception: pass
        APPS.pop(key, None)
        return True


def background_job(label, argv, cwd):
    jid = uuid.uuid4().hex[:12]
    logs = deque(maxlen=1500)
    job = {"id": jid, "label": label, "status": "running", "returncode": None,
           "started": time.time(), "logs": logs, "proc": None}
    with LOCK: JOBS[jid] = job
    def worker():
        logs.append("$ " + " ".join(shlex.quote(str(x)) for x in argv))
        try:
            p = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                errors="replace", bufsize=1, shell=False, close_fds=True)
            job["proc"] = p
            for line in iter(p.stdout.readline, ""):
                with LOCK: logs.append(line.rstrip("\n"))
            code = p.wait(); job["returncode"] = code
            job["status"] = "success" if code == 0 else "failed"
            logs.append(f"[process exited with code {code}]")
        except Exception as e:
            job["status"] = "failed"; logs.append("[error] " + str(e))
    threading.Thread(target=worker, daemon=True).start()
    return job

LOGIN = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GLORY HOST · Sign in</title><style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(ellipse at top,#252046,#080b12 60%);color:#f2f4ff;font:15px system-ui}.box{width:min(92vw,410px);padding:30px;background:#111722;border:1px solid #2b3446;border-radius:22px;box-shadow:0 30px 90px #0008}.logo{font-size:25px;font-weight:950;letter-spacing:2px}.logo span{color:#a995ff}.sub{color:#8a97ae;font-size:12px;margin:8px 0 25px}label{display:block;font-size:11px;letter-spacing:1px;color:#aab5c9;margin:15px 0 7px}input{width:100%;padding:13px;background:#080d16;border:1px solid #303a4f;border-radius:10px;color:white;outline:none}button{width:100%;padding:13px;margin-top:20px;border:0;border-radius:10px;background:linear-gradient(120deg,#9878ff,#6b5cf1);color:white;font-weight:800}.err{color:#ff9bad;font-size:12px;margin-top:12px}</style></head><body><form class="box" method="post" action="/login"><div class="logo">GLORY <span>HOST</span></div><div class="sub">CLOUD CONTROL CENTER · ADMIN SIGN IN</div><label>USERNAME</label><input name="username" autocomplete="username" required><label>PASSWORD</label><input type="password" name="password" autocomplete="current-password" required><div class="err">__ERROR__</div><button>Sign in ↗</button></form></body></html>'''

class Handler(BaseHTTPRequestHandler):
    server_version = "GloryHost/2.0"
    def log_message(self, fmt, *args): log.info("%s %s", self.address_string(), fmt % args)
    def send(self, code, data, typ="application/json; charset=utf-8", extra=None):
        self.send_response(code); self.send_header("Content-Type", typ); self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store"); self.send_header("Referrer-Policy", "same-origin")
        if extra:
            for k,v in extra.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(data)
    def js(self, obj, code=200, extra=None): self.send(code, json.dumps(obj, ensure_ascii=False).encode(), extra=extra)
    def err(self, msg, code=400): self.js({"ok":False,"error":str(msg)},code)
    def body(self, limit=4*1024*1024):
        n=int(self.headers.get("Content-Length","0") or 0)
        if n < 0 or n > limit: raise ValueError("Request too large.")
        return self.rfile.read(n)
    def data(self): return json.loads(self.body().decode("utf-8") or "{}")
    def auth(self):
        token=next((x.strip().split("=",1)[1] for x in self.headers.get("Cookie","").split(";") if x.strip().startswith("gh_session=")),"")
        with LOCK:
            for k,t in list(SESSIONS.items()):
                if time.time()-t > 43200: SESSIONS.pop(k,None)
            return token in SESSIONS
    def gate(self):
        if not self.auth(): self.err("Session expired. Sign in again.",401); return False
        if self.command not in ("GET","HEAD") and self.headers.get("X-Requested-With") != "GLORY-HOST":
            self.err("CSRF check failed.",403); return False
        return True
    def multipart(self):
        n=int(self.headers.get("Content-Length","0") or 0)
        if n<=0 or n>MAX_UPLOAD+1024*1024: raise ValueError("Upload is empty or exceeds 100 MB.")
        raw=b"Content-Type: "+self.headers.get("Content-Type","").encode()+b"\r\nMIME-Version: 1.0\r\n\r\n"+self.rfile.read(n)
        msg=BytesParser(policy=default).parsebytes(raw); out={}
        if not msg.is_multipart(): raise ValueError("Expected multipart upload.")
        for part in msg.iter_parts():
            name=part.get_param("name",header="content-disposition")
            if not name: continue
            payload=part.get_payload(decode=True) or b""
            filename=part.get_filename()
            out[name]={"filename":filename,"data":payload} if filename is not None else {"value":payload.decode("utf-8","replace")}
        return out
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=="/":
            if self.auth():
                try: self.send(200,(Path(__file__).parent/"index.html").read_bytes(),"text/html; charset=utf-8")
                except Exception: self.send(500,b"index.html missing","text/plain")
            else: self.send(200,LOGIN.encode(),"text/html; charset=utf-8")
            return
        if u.path.startswith("/api/") and not self.gate(): return
        q=parse_qs(u.query)
        try:
            if u.path=="/api/overview":
                with LOCK: running=sum(alive(a) for a in APPS.values())
                self.js({"ok":True,"files":file_count(),"size":human(tree_size()),"running":running,"uptime":int(time.time()-self.server.started)})
            elif u.path=="/api/files":
                p=safe_path(q.get("path",[""])[0])
                if not p.is_dir(): raise ValueError("Folder not found.")
                items=[]
                for x in sorted(p.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
                    if x.is_symlink(): continue
                    items.append({"name":x.name,"dir":x.is_dir(),"size":human(x.stat().st_size) if x.is_file() else "—","modified":int(x.stat().st_mtime)})
                self.js({"ok":True,"path":rel(p),"items":items})
            elif u.path=="/api/read":
                p=safe_path(q.get("path",[""])[0])
                if not p.is_file() or p.stat().st_size>3*1024*1024: raise ValueError("File missing or larger than 3 MB.")
                self.js({"ok":True,"content":p.read_text(encoding="utf-8"),"bytes":p.stat().st_size})
            elif u.path=="/api/download":
                p=safe_path(q.get("path",[""])[0])
                if not p.is_file(): raise ValueError("File not found.")
                self.send(200,p.read_bytes(),"application/octet-stream",{"Content-Disposition":f"attachment; filename*=UTF-8''{quote(p.name)}"})
            elif u.path=="/api/apps":
                with LOCK: items=[{"path":k,"running":alive(a),"pid":a["proc"].pid if alive(a) else None} for k,a in APPS.items()]
                self.js({"ok":True,"items":items})
            elif u.path=="/api/jobs":
                with LOCK: items=[{"id":j["id"],"label":j["label"],"status":j["status"],"returncode":j["returncode"]} for j in list(JOBS.values())[-35:]]
                self.js({"ok":True,"items":items})
            elif u.path=="/api/job-logs":
                jid=q.get("id",[""])[0]
                with LOCK: j=JOBS.get(jid)
                if not j: raise ValueError("Task not found.")
                self.js({"ok":True,"logs":"\n".join(j["logs"]),"status":j["status"],"returncode":j["returncode"]})
            elif u.path=="/api/logs":
                key=q.get("path",[""])[0]
                with LOCK: a=APPS.get(key)
                if not a: raise ValueError("Start the app first.")
                self.js({"ok":True,"logs":"\n".join(a["logs"]),"running":alive(a)})
            else: self.err("Not found",404)
        except Exception as e: self.err(e)
    def do_POST(self):
        u=urlparse(self.path)
        if u.path=="/login":
            try:
                from urllib.parse import parse_qs
                f=parse_qs(self.body(8192).decode("utf-8","replace")); user=f.get("username",[""])[0]; pw=f.get("password",[""])[0]
                ok=secrets.compare_digest(user,ADMIN_USERNAME) and secrets.compare_digest(pw,ADMIN_PASSWORD) and ADMIN_PASSWORD!="CHANGE_THIS_PASSWORD_NOW"
                if ok:
                    token=secrets.token_urlsafe(32)
                    with LOCK: SESSIONS[token]=time.time()
                    self.send(303,b"","text/plain",{"Location":"/","Set-Cookie":f"gh_session={token}; HttpOnly; SameSite=Strict; Path=/"})
                else: self.send(200,LOGIN.replace("__ERROR__","Invalid login. Configure credentials in main.py.").encode(),"text/html; charset=utf-8")
            except Exception: self.send(400,b"Bad request","text/plain")
            return
        if u.path=="/api/logout":
            if not self.gate(): return
            token=next((x.strip().split("=",1)[1] for x in self.headers.get("Cookie","").split(";") if x.strip().startswith("gh_session=")),"")
            with LOCK: SESSIONS.pop(token,None)
            self.js({"ok":True},extra={"Set-Cookie":"gh_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"}); return
        if not self.gate(): return
        try:
            if u.path in ("/api/upload","/api/extract"):
                f=self.multipart(); up=f.get("file")
                if not up or not up.get("filename"): raise ValueError("Choose a file first.")
                data=up["data"]
                if len(data)>MAX_UPLOAD: raise ValueError("Upload exceeds 100 MB.")
                folder=safe_path(f.get("path",{}).get("value","")); folder.mkdir(parents=True,exist_ok=True)
                name=valid_name(Path(up["filename"].replace("\\","/")).name)
                if u.path=="/api/upload": (folder/name).write_bytes(data); self.js({"ok":True,"message":"Uploaded"})
                else:
                    if not name.lower().endswith(".zip"): raise ValueError("Choose a ZIP file.")
                    count=0; expanded=0
                    with zipfile.ZipFile(io.BytesIO(data)) as z:
                        for info in z.infolist():
                            if info.is_dir(): continue
                            nm=info.filename.replace("\\","/")
                            if nm.startswith("/") or any(x==".." for x in nm.split("/")): raise ValueError("Unsafe ZIP entry blocked.")
                            expanded+=info.file_size
                            if expanded>500*1024*1024: raise ValueError("ZIP expanded size exceeds 500 MB.")
                            target=safe_path((folder/nm).relative_to(ROOT).as_posix()); target.parent.mkdir(parents=True,exist_ok=True)
                            with z.open(info) as src, open(target,"wb") as dst: shutil.copyfileobj(src,dst)
                            count+=1
                            if count>3000: raise ValueError("ZIP has over 3000 files.")
                    self.js({"ok":True,"count":count,"message":f"Extracted {count} files"})
            elif u.path=="/api/write":
                d=self.data(); p=safe_path(d.get("path","")); content=d.get("content","")
                if not p.is_file() or not isinstance(content,str) or len(content.encode())>3*1024*1024: raise ValueError("Existing text file required; editor limit is 3 MB.")
                p.write_text(content,encoding="utf-8"); self.js({"ok":True})
            elif u.path=="/api/rename":
                d=self.data(); p=safe_path(d.get("path","")); dest=safe_path((p.parent/valid_name(d.get("name"))).relative_to(ROOT).as_posix())
                if not p.exists() or dest.exists(): raise ValueError("Source missing or destination already exists.")
                p.rename(dest); self.js({"ok":True})
            elif u.path=="/api/delete":
                p=safe_path(self.data().get("path",""))
                if p==ROOT or not p.exists(): raise ValueError("Item missing or root deletion blocked.")
                shutil.rmtree(p) if p.is_dir() else p.unlink(); self.js({"ok":True})
            elif u.path=="/api/mkdir":
                d=self.data(); (safe_path(d.get("path",""))/valid_name(d.get("name"))).mkdir(exist_ok=False); self.js({"ok":True})
            elif u.path.startswith("/api/app/"):
                d=self.data(); folder=safe_path(d.get("path","")); key=rel(folder); action=u.path.rsplit("/",1)[-1]
                if not folder.is_dir(): raise ValueError("App folder not found.")
                if action=="stop": self.js({"ok":True,"message":"Stopped" if stop_app(key) else "Not running"})
                elif action in ("start","restart"):
                    if action=="restart": stop_app(key)
                    with LOCK:
                        if key in APPS and alive(APPS[key]): raise ValueError("App already running")
                    entry=valid_name(d.get("entry","main.py")); script=safe_path((folder/entry).relative_to(ROOT).as_posix())
                    if not script.is_file(): raise ValueError("Entry script not found")
                    logs=deque(maxlen=1200); p=subprocess.Popen([sys.executable,"-u",str(script)],cwd=str(folder),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors="replace",bufsize=1,shell=False)
                    APPS[key]={"proc":p,"logs":logs,"started":time.time()}
                    def reader():
                        for line in iter(p.stdout.readline,""): logs.append(line.rstrip("\n"))
                    threading.Thread(target=reader,daemon=True).start(); self.js({"ok":True,"message":f"Started {key} (PID {p.pid})"})
                else: self.err("Unknown action",404)
            elif u.path=="/api/install":
                d=self.data(); folder=safe_path(d.get("path","")); req=safe_path((folder/"requirements.txt").relative_to(ROOT).as_posix())
                if not folder.is_dir() or not req.is_file(): raise ValueError("App folder and requirements.txt are required.")
                j=background_job("pip install -r requirements.txt",[sys.executable,"-u","-m","pip","install","-r",str(req)],folder)
                self.js({"ok":True,"id":j["id"],"message":"pip installation started; follow live logs."})
            elif u.path=="/api/command":
                command=str(self.data().get("command","")).strip()
                if not command or len(command)>500: raise ValueError("Enter a command up to 500 characters.")
                if any(x in command for x in ("\n","\r",";","&&","||","|",">","<","`","$")): raise ValueError("Shell operators are blocked. Run one command at a time.")
                args=shlex.split(command)
                if not args: raise ValueError("Empty command.")
                j=background_job("$ "+command,args,ROOT); self.js({"ok":True,"id":j["id"],"message":"Command started; follow live output in Tasks & Logs."})
            else: self.err("Not found",404)
        except zipfile.BadZipFile: self.err("Invalid ZIP file")
        except Exception as e: self.err(e)

def main():
    if ADMIN_PASSWORD=="CHANGE_THIS_PASSWORD_NOW": log.warning("Login disabled until you set ADMIN_PASSWORD in main.py")
    srv=ThreadingHTTPServer((HOST,PORT),Handler); srv.daemon_threads=True; srv.started=time.time()
    log.info("GLORY HOST Cloud listening on %s:%s",HOST,PORT); log.info("Workspace: %s",ROOT)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        with LOCK: keys=list(APPS)
        for k in keys: stop_app(k)
        srv.server_close()
if __name__=="__main__": main()
