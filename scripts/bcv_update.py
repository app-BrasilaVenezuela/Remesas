#!/usr/bin/env python3
"""Actualiza la tasa oficial del BCV (USD) en data.json cuando "Actualizar automáticamente" está activado.

Fuentes, en orden: 1) tasas.json (historial BCV que ya mantiene scripts/update_tasas.py)  2) página del BCV  3) DolarAPI Venezuela (oficial).
Solo usa la biblioteca estándar. Si ninguna fuente responde o el valor se ve raro, no cambia nada.
"""
import json, os, re, ssl, sys, html as htmlmod, urllib.request, datetime
from decimal import Decimal

DATA = os.environ.get("DATA_FILE", "data.json")
TASAS = os.environ.get("TASAS_FILE", "tasas.json")
MAX_JUMP = Decimal(os.environ.get("BCV_MAX_JUMP", "0.15"))   # el BCV cambia poco entre revisiones
HISTORY_LIMIT = 200
MESES = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,"noviembre":11,"diciembre":12}

def get(url, lenient=False, timeout=25):
    ctx = ssl.create_default_context()
    if lenient: ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE   # el BCV suele tener problemas de certificado
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) bcv-updater", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")

NUM = r"([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]+|[0-9]+,[0-9]+|[0-9]+\.[0-9]+)"

def to_decimal(raw):
    raw = raw.strip()
    if "," in raw: raw = raw.replace(".", "").replace(",", ".")
    return Decimal(raw)

def parse_bcv(page):
    """Devuelve (valor, fecha 'YYYY-MM-DD' o '') a partir del HTML del BCV; tolera cambios de diseño."""
    raw = None
    m = re.search(r'id="dolar"[\s\S]{0,2500}?' + NUM, page)           # bloque del dólar (diseño clásico)
    if m: raw = m.group(1)
    if raw is None:                                                   # texto plano: "USD  877,90230000"
        text = htmlmod.unescape(re.sub(r"<(script|style)[\s\S]*?</\1>", " ", page, flags=re.I))
        text = re.sub(r"<[^>]+>", " ", text); text = re.sub(r"\s+", " ", text)
        m = re.search(r"\bUSD\b\s*:?\s*(?:Bs\.?\s*)?" + NUM, text)
        if m: raw = m.group(1)
    if raw is None: return None
    val = to_decimal(raw)
    if val <= 0: return None
    date = ""
    md = re.search(r'date-display-single[^>]*content="(\d{4}-\d{2}-\d{2})', page)
    if md: date = md.group(1)
    else:
        text = re.sub(r"<[^>]+>", " ", page)
        mf = re.search(r"Fecha\s+Valor\s*:?\s*(?:[A-Za-záéíóúÁÉÍÓÚ]+,?\s+)?(\d{1,2})\s+(?:de\s+)?([A-Za-záéíóú]+)\s+(?:de\s+)?(\d{4})", text, re.I)
        if mf and mf.group(2).lower() in MESES:
            date = "%04d-%02d-%02d" % (int(mf.group(3)), MESES[mf.group(2).lower()], int(mf.group(1)))
    return val, date

def src_tasas():
    """Último dólar BCV del historial tasas.json (el mismo que dibuja las gráficas de la app)."""
    with open(TASAS, encoding="utf-8") as f: j = json.load(f)
    serie = [a for a in (j.get("usd") or []) if a and Decimal(str(a[1])) > 0]
    if not serie: raise ValueError("tasas.json sin dólar BCV")
    return Decimal(str(serie[-1][1])), serie[-1][0]

def src_bcv():
    last = None
    for lenient in (False, True):
        try:
            r = parse_bcv(get("https://www.bcv.org.ve/", lenient))
            if r: return r
            last = ValueError("no se encontró la tasa en la página")
        except Exception as e: last = e
    raise last

def src_dolarapi():
    j = json.loads(get("https://ve.dolarapi.com/v1/dolares/oficial"))
    v = j.get("promedio") or j.get("venta") or j.get("compra")
    if not v: raise ValueError("sin datos")
    return Decimal(str(v)), (j.get("fechaActualizacion") or "")[:10]

def fetch():
    fake = os.environ.get("BCV_FAKE")                 # solo para pruebas: "877.9023|2026-10-15"
    if fake: v, d = fake.split("|"); return Decimal(v), d, "prueba"
    for name, fn in (("tasas.json", src_tasas), ("bcv.org.ve", src_bcv), ("dolarapi", src_dolarapi)):
        try:
            v, d = fn()
            if v > 0: return v, d, name
        except Exception as e:
            print(f"fuente {name}: {e}", file=sys.stderr)
    return None, None, None

def fmt(x):
    s = format(x.quantize(Decimal("0.00000001")), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s

def main():
    try:
        with open(DATA, encoding="utf-8") as f: d = json.load(f)
    except FileNotFoundError:
        print("No hay data.json"); return 0
    r = (d.get("rates") or {}).get("BCV_USD_VES")
    if not r: print("No hay tasa BCV en data.json"); return 0
    if r.get("source") == "manual": print("BCV en modo manual: no se toca."); return 0
    v, date, source = fetch()
    if v is None: print("Ninguna fuente respondió; no se cambia nada."); return 0
    old = Decimal(str(r["value"]))
    if abs(v / old - 1) > MAX_JUMP:
        print(f"BCV {fmt(v)} rechazado: cambio de {fmt((v/old-1)*100)} % respecto a {r['value']}."); return 0
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    new = fmt(v)
    prev = d.get("bcvAuto") or {}
    if new == r["value"]:
        print(f"BCV sin cambios ({new}).")
        if prev.get("value") != new or prev.get("source") != source or (date and prev.get("date") != date):
            d["bcvAuto"] = {"at": prev.get("at") or now, "value": new, "date": date or prev.get("date", ""), "source": source}
            with open(DATA, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        return 0
    r["value"] = new
    d["bcvAuto"] = {"at": now, "value": new, "date": date, "source": source}
    d["updatedAt"] = now
    snap = {"at": now, "rates": {k: x["value"] for k, x in d["rates"].items()}, "fees": d.get("fees"), "auto": "bcv"}
    d.setdefault("history", []).insert(0, snap); del d["history"][HISTORY_LIMIT:]
    with open(DATA, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    print(f"BCV: {old} -> {new} ({source}, {date})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
