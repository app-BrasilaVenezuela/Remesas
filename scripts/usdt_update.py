#!/usr/bin/env python3
"""Actualiza las tasas ligadas al USDT en data.json (solo si usdtLink.mode == "auto").

- Consulta el precio USDT/VES (Bs por 1 USDT) en varias fuentes, en orden, y usa la primera que responda.
- Siempre guarda el último precio visto en usdt.json (lo usa el botón "Traer del mercado" de la app).
- Si el modo es "auto" y el precio cambió más que el umbral, reescala las tasas marcadas
  (directas se multiplican por nuevo/base, inversas se dividen) y anota el historial.
Solo usa la biblioteca estándar de Python.
"""
import json, os, sys, statistics, urllib.request, datetime
from decimal import Decimal, Context, ROUND_HALF_EVEN

DATA = os.environ.get("DATA_FILE", "data.json")
MARKET = os.environ.get("MARKET_FILE", "usdt.json")
TASAS = os.environ.get("TASAS_FILE", "tasas.json")
SOURCES = [s.strip() for s in os.environ.get("USDT_SOURCES", "tasas,binance,criptoya,dolarapi").split(",") if s.strip()]
MAX_JUMP = Decimal(os.environ.get("USDT_MAX_JUMP", "0.30"))      # ignora saltos mayores al 30 % de una sola vez
STALE_HOURS = float(os.environ.get("USDT_STALE_HOURS", "3"))
HISTORY_LIMIT = 200
CTX = Context(prec=10, rounding=ROUND_HALF_EVEN)

def http(url, body=None, timeout=20):
    req = urllib.request.Request(url, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (usdt-updater)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def src_tasas():
    """Último USDT/VES de tasas.json (lo llena scripts/update_tasas.py); se ignora si tiene más de 2 días."""
    with open(TASAS, encoding="utf-8") as f: j = json.load(f)
    serie = [a for a in (j.get("usdt") or []) if a and Decimal(str(a[1])) > 0]
    if not serie: raise ValueError("tasas.json sin USDT")
    d = datetime.date.fromisoformat(serie[-1][0])
    if (datetime.date.today() - d).days > 2: raise ValueError("el USDT de tasas.json está viejo (" + serie[-1][0] + ")")
    return Decimal(str(serie[-1][1]))

def src_binance():
    body = {"fiat": "VES", "page": 1, "rows": 10, "tradeType": "SELL", "asset": "USDT", "countries": [],
            "proMerchantAds": False, "shieldMerchantAds": False, "publisherType": None, "payTypes": [], "classifies": ["mass", "profession"]}
    j = http("https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search", body)
    prices = [Decimal(str(a["adv"]["price"])) for a in (j.get("data") or []) if a.get("adv", {}).get("price")]
    if len(prices) < 3: raise ValueError("pocos anuncios")
    return Decimal(str(statistics.median(prices)))

def src_criptoya():
    j = http("https://criptoya.com/api/binancep2p/USDT/VES/1")
    vals = [Decimal(str(j[k])) for k in ("ask", "bid", "totalAsk", "totalBid") if isinstance(j, dict) and j.get(k)]
    if not vals: raise ValueError("sin datos")
    return sum(vals) / len(vals)

def src_dolarapi():
    j = http("https://ve.dolarapi.com/v1/dolares/paralelo")
    v = j.get("promedio") or j.get("venta") or j.get("compra")
    if not v: raise ValueError("sin datos")
    return Decimal(str(v))

FUNCS = {"tasas": src_tasas, "binance": src_binance, "criptoya": src_criptoya, "dolarapi": src_dolarapi}

def fetch_price():
    fake = os.environ.get("USDT_FAKE_PRICE")      # solo para pruebas
    if fake: return Decimal(fake), "prueba"
    for name in SOURCES:
        try:
            p = FUNCS[name]()
            if p > 0: return p, name
        except Exception as e:
            print(f"fuente {name}: {e}", file=sys.stderr)
    return None, None

def fmt(x):
    t = format(CTX.create_decimal(x), "f")
    return t.rstrip("0").rstrip(".") if "." in t else t

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except FileNotFoundError: return default

def save(path, obj):
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    price, source = fetch_price()
    if price is None:
        print("Ninguna fuente respondió; no se cambia nada."); return 0
    # --- usdt.json: último precio y pequeño historial
    m = load(MARKET, {"value": None, "at": None, "history": []})
    hist = m.get("history") or []
    old = Decimal(m["value"]) if m.get("value") else None
    last_at = datetime.datetime.fromisoformat(m["at"].replace("Z", "+00:00")) if m.get("at") else None
    stale = last_at is None or (now - last_at).total_seconds() > STALE_HOURS * 3600
    moved = old is None or abs(price - old) / old >= Decimal("0.002")
    if moved or stale:
        hist.append([iso, fmt(price)]); hist = hist[-96:]
        save(MARKET, {"value": fmt(price), "at": iso, "source": source, "history": hist})
        print(f"usdt.json: {fmt(price)} ({source})")
    # --- data.json: solo en modo automático
    d = load(DATA, None)
    if not d: print("No hay data.json"); return 0
    L = d.get("usdtLink") or {}
    if L.get("mode") != "auto": print("Modo manual: no se tocan las tasas."); return 0
    if not L.get("ref"): print("Falta el precio base (usdtLink.ref); no se cambia nada."); return 0
    ref = Decimal(str(L["ref"]).replace(",", "."))
    ratio = price / ref
    if abs(ratio - 1) > MAX_JUMP:
        print(f"Cambio de {fmt((ratio-1)*100)} % rechazado por seguridad (máx {MAX_JUMP*100} %)."); return 0
    threshold = Decimal(str(L.get("threshold") or "0.5")) / 100
    if abs(ratio - 1) < threshold:
        print(f"Cambio de {fmt((ratio-1)*100)} % menor al umbral; no se cambia nada."); return 0
    changed = []
    for keys, inverse in ((L.get("linked") or [], False), (L.get("inverse") or [], True)):
        for k in keys:
            r = d["rates"].get(k)
            if not r or k == "BCV_USD_VES": continue
            v = Decimal(str(r["value"]))
            new = fmt(v / ratio if inverse else v * ratio)
            if new != r["value"]: changed.append((k, r["value"], new)); r["value"] = new
    if not changed: print("Sin tasas para cambiar."); return 0
    L["ref"] = fmt(price); L["updatedAt"] = iso; L["lastAuto"] = {"at": iso, "price": fmt(price), "source": source, "pct": fmt((ratio - 1) * 100)}
    d["usdtLink"] = L; d["updatedAt"] = iso
    snap = {"at": iso, "rates": {k: r["value"] for k, r in d["rates"].items()}, "fees": d.get("fees"), "auto": "usdt"}
    d.setdefault("history", []).insert(0, snap); del d["history"][HISTORY_LIMIT:]
    save(DATA, d)
    for k, a, b in changed: print(f"{k}: {a} -> {b}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
