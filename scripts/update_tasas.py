#!/usr/bin/env python3
"""Actualiza tasas.json (historial BCV dólar/euro y referencia USDT) para las gráficas de la app.
Solo usa la librería estándar. Cada fuente es opcional: si una falla, se sigue con las demás y
NUNCA se borra lo que ya estaba guardado."""
import json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tasas.json")
KEEP_DAYS = 420  # un poco más de un año
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
VE = timezone(timedelta(hours=-4))  # America/Caracas


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load():
    try:
        with open(OUT, encoding="utf-8") as f:
            d = json.load(f)
        return {k: {a: b for a, b in d.get(k, [])} for k in ("usd", "eur", "usdt")}
    except Exception:
        return {"usd": {}, "eur": {}, "usdt": {}}


def main():
    data = load()
    before = json.dumps(data, sort_keys=True)
    today = datetime.now(VE).strftime("%Y-%m-%d")
    cutoff = (datetime.now(VE) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")

    # 1) Dólar y euro oficiales del BCV: historial diario público (coincide con lo que publica el BCV)
    try:
        hist = json.loads(get("https://raw.githubusercontent.com/grupoclip/bcv-api/HEAD/api/v1/history.json", 60))
        n = 0
        for x in hist:
            d = x.get("date")
            if not d or d < cutoff:
                continue
            if isinstance(x.get("USD"), (int, float)):
                data["usd"][d] = round(float(x["USD"]), 8); n += 1
            if isinstance(x.get("EUR"), (int, float)):
                data["eur"][d] = round(float(x["EUR"]), 8)
        print(f"BCV historial: {n} días leídos")
    except Exception as e:
        print("BCV historial falló:", e)
        # Alternativa: valor del día desde otra fuente pública
        try:
            j = json.loads(get("https://chitty400.github.io/chitty-bcv-api/latest.json"))
            data["usd"][today] = float(j["tasas"]["usd"]); data["eur"][today] = float(j["tasas"]["eur"])
            print("BCV valor del día (alternativa) OK")
        except Exception as e2:
            print("BCV alternativa falló:", e2)

    # 2) USDT/VES (mercado): valor actual del monitor; el último valor del día es el que queda
    usdt = None
    try:
        html = get("https://www.monitordedivisavenezuela.com/")
        m = re.search(r"USDT/VES[\s\S]{0,400}?([0-9]{2,4}(?:[.,][0-9]{1,4})?)\s*Bs\s*/\s*USDT", html)
        if m:
            usdt = float(m.group(1).replace(",", "."))
            print("USDT del monitor:", usdt)
        else:
            print("USDT: el monitor no trae el valor en el HTML (se carga con JavaScript)")
    except Exception as e:
        print("USDT monitor falló:", e)
    if usdt is None:
        try:
            j = json.loads(get("https://ve.dolarapi.com/v1/dolares/paralelo"))
            usdt = float(j.get("promedio"))
            print("USDT (paralelo, DolarApi):", usdt)
        except Exception as e:
            print("USDT DolarApi falló:", e)
    if usdt and 1 < usdt < 100000:
        data["usdt"][today] = round(usdt, 4)

    # Limpieza y guardado (solo si cambió algo)
    for k in data:
        data[k] = {d: v for d, v in data[k].items() if d >= cutoff}
    if json.dumps(data, sort_keys=True) == before:
        print("Sin cambios"); return
    out = {"v": 1, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for k in ("usd", "eur", "usdt"):
        out[k] = [[d, data[k][d]] for d in sorted(data[k])]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print("tasas.json actualizado:", {k: len(out[k]) for k in ("usd", "eur", "usdt")})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # nunca romper el trabajo programado
        print("Error inesperado:", e)
    sys.exit(0)
