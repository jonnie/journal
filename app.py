"""Journal — a local markdown journaling web app.

Run:  uvicorn app:app --port 8321
First:  python scripts/set_password.py
"""
import hashlib
import hmac
import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
CONFIG = BASE / "config.json"
STATIC = BASE / "static"

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
MONTH_INDEX = {name: i for i, name in enumerate(MONTHS)}

# scrypt params — must match scripts/set_password.py
SCRYPT_KW = {"n": 2 ** 14, "r": 8, "p": 1}
SESSION_DAYS = 30
COOKIE = "journal_auth"
# serialises the conflict check-and-write across threadpool workers
FILE_LOCK = threading.Lock()


def file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def password_ok(password: str, cfg: dict) -> bool:
    stored = cfg.get("password", "")
    salt_hex, _, hash_hex = stored.partition("$")
    if not _ or not hash_hex:
        return False
    h = hashlib.scrypt(password.encode("utf-8"),
                       salt=bytes.fromhex(salt_hex), **SCRYPT_KW)
    return hmac.compare_digest(h, bytes.fromhex(hash_hex))


def make_token(secret: str) -> tuple[str, int]:
    max_age = SESSION_DAYS * 86400
    expiry = str(int(time.time()) + max_age)
    sig = hmac.new(secret.encode("utf-8"), expiry.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}", max_age


def authenticated(request: Request) -> bool:
    cfg = load_config()
    secret = cfg.get("secret", "")
    token = request.cookies.get(COOKIE, "")
    if not secret or "." not in token:
        return False
    expiry, _, sig = token.rpartition(".")
    if not expiry.isdigit() or int(expiry) < time.time():
        return False
    expected = hmac.new(secret.encode("utf-8"), expiry.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def require_auth(request: Request) -> None:
    if not authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ---------- pages ----------

@app.get("/")
def index(request: Request):
    page = "index.html" if authenticated(request) else "login.html"
    resp = FileResponse(STATIC / page)
    # content depends on auth state — never let a stale copy be served
    resp.headers["Cache-Control"] = "no-store"
    return resp


class LoginBody(BaseModel):
    password: str


@app.post("/login")
def login(body: LoginBody, response: Response):
    cfg = load_config()
    if not cfg.get("password"):
        raise HTTPException(status_code=400,
                            detail="Password not set. Run: python scripts/set_password.py")
    if not password_ok(body.password, cfg):
        raise HTTPException(status_code=401, detail="Wrong password")
    token, max_age = make_token(cfg["secret"])
    response.set_cookie(COOKIE, token, max_age=max_age,
                        httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ---------- path safety ----------

def safe_path(name: str) -> Path:
    if not name or name.startswith("/") or "\x00" in name:
        raise HTTPException(status_code=400, detail="Bad path")
    p = (DATA / name).resolve()
    if p != DATA and DATA not in p.parents:
        raise HTTPException(status_code=400, detail="Bad path")
    if p.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only .md files")
    return p


# ---------- entry APIs ----------

@app.get("/api/tree")
def tree(request: Request):
    require_auth(request)
    out = []
    if DATA.is_dir():
        for year in sorted(DATA.iterdir(),
                           key=lambda p: (0, int(p.name)) if p.name.isdigit()
                           else (1, p.name)):
            if not year.is_dir():
                continue
            ynode = {"name": year.name, "type": "dir", "children": []}
            for month in sorted(year.iterdir(),
                                key=lambda p: MONTH_INDEX.get(p.name, 99)):
                if not month.is_dir():
                    continue
                mnode = {"name": month.name, "type": "dir", "children": []}
                for f in sorted(month.iterdir(), key=lambda p: p.name):
                    if f.suffix.lower() == ".md":
                        mnode["children"].append(
                            {"name": f"{year.name}/{month.name}/{f.name}",
                             "type": "file"})
                ynode["children"].append(mnode)
            out.append(ynode)
    return out


class FileBody(BaseModel):
    name: str
    content: str
    base_hash: str | None = None  # hash client loaded; None = force overwrite


@app.get("/api/file")
def get_file(request: Request, name: str):
    require_auth(request)
    p = safe_path(name)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    content = p.read_text(encoding="utf-8")
    return {"name": name, "content": content, "hash": file_hash(content)}


@app.put("/api/file")
def put_file(request: Request, body: FileBody):
    require_auth(request)
    p = safe_path(body.name)
    with FILE_LOCK:
        if not p.parent.is_dir():
            raise HTTPException(status_code=404, detail="Not found")
        if body.base_hash is not None and p.is_file():
            disk = p.read_text(encoding="utf-8")
            disk_hash = file_hash(disk)
            if disk_hash != body.base_hash:
                # changed on disk since the client loaded it (another device)
                return JSONResponse(status_code=409, content={
                    "detail": "conflict",
                    "content": disk,
                    "hash": disk_hash,
                })
        p.write_text(body.content, encoding="utf-8")
    return {"ok": True, "hash": file_hash(body.content)}

@app.delete("/api/file")
def delete_file(request: Request, name: str):
    require_auth(request)
    p = safe_path(name)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    p.unlink()
    return {"ok": True}

def _ordinal(day: int) -> str:
    """1->1st, 2->2nd, 3->3rd, 11->11th, 21->21st, 22->22nd..."""
    if 11 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"



@app.post("/api/create")
def create_entry(request: Request, offset: int = 0):
    require_auth(request)
    target = date.today() + timedelta(days=offset)
    y, m = str(target.year), MONTHS[target.month - 1]
    ddir = DATA / y / m
    ddir.mkdir(parents=True, exist_ok=True)
    f = ddir / target.strftime("%d-%m-%Y.md")
    if not f.exists():
        f.write_text(f"# {target.strftime('%A')} {_ordinal(target.day)} {m} {y}\n",
                    encoding="utf-8")
    return {"name": f"{y}/{m}/{f.name}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8321)
