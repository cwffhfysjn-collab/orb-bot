import os, hmac, hashlib, time, logging, requests, math
from flask import Flask, request, jsonify

API_KEY    = os.getenv("BINGX_API_KEY", "")
API_SECRET = os.getenv("BINGX_API_SECRET", "")
SYMBOL     = os.getenv("SYMBOL", "BTC-USDT")
LEVERAGE   = int(os.getenv("LEVERAGE", "5"))
USDT       = float(os.getenv("USDT_PER_TRADE", "50"))
SECRET     = os.getenv("WEBHOOK_SECRET", "clave123bot")
DEMO       = os.getenv("USE_DEMO", "true").lower() == "true"
BASE       = "https://open-api-vst.bingx.com" if DEMO else "https://open-api.bingx.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

def sign(p):
    q = "&".join(f"{k}={v}" for k,v in sorted(p.items()))
    return hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

def post(ep, p):
    p["timestamp"] = str(int(time.time()*1000))
    p["signature"] = sign(p)
    r = requests.post(BASE+ep, headers={"X-BX-APIKEY":API_KEY}, data=p, timeout=10)
    return r.json()

def get(ep, p):
    p["timestamp"] = str(int(time.time()*1000))
    p["signature"] = sign(p)
    r = requests.get(BASE+ep, headers={"X-BX-APIKEY":API_KEY}, params=p, timeout=10)
    return r.json()

def price():
    r = get("/openApi/swap/v2/quote/price", {"symbol":SYMBOL})
    return float(r["data"]["price"])

def close_all():
    try:
        r = get("/openApi/swap/v2/user/positions", {"symbol":SYMBOL})
        for p in r.get("data", []):
            sz = float(p.get("positionAmt", 0))
            if abs(sz) > 0:
                side = "SELL" if p["positionSide"]=="LONG" else "BUY"
                post("/openApi/swap/v2/trade/order", {
                    "symbol":SYMBOL,"side":side,
                    "positionSide":p["positionSide"],
                    "type":"MARKET","quantity":str(abs(sz))
                })
                log.info(f"Cerrada {p['positionSide']} {abs(sz)}")
    except Exception as e:
        log.error(f"close_all: {e}")

def open_order(side, pos_side, qty, sl, tp):
    r = post("/openApi/swap/v2/trade/order", {
        "symbol":SYMBOL,"side":side,"positionSide":pos_side,
        "type":"MARKET","quantity":str(qty)
    })
    log.info(f"Entrada: {r}")
    cl = "SELL" if pos_side=="LONG" else "BUY"
    try:
        post("/openApi/swap/v2/trade/order", {
            "symbol":SYMBOL,"side":cl,"positionSide":pos_side,
            "type":"STOP_MARKET","stopPrice":str(round(sl,2)),
            "closePosition":"true","workingType":"MARK_PRICE"
        })
        log.info(f"SL: {sl}")
    except Exception as e:
        log.warning(f"SL error: {e}")
    try:
        post("/openApi/swap/v2/trade/order", {
            "symbol":SYMBOL,"side":cl,"positionSide":pos_side,
            "type":"TAKE_PROFIT_MARKET","stopPrice":str(round(tp,2)),
            "closePosition":"true","workingType":"MARK_PRICE"
        })
        log.info(f"TP: {tp}")
    except Exception as e:
        log.warning(f"TP error: {e}")

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        d = request.get_json(force=True) or {}
        log.info(f"Recibido: {d}")
        if d.get("secret") != SECRET:
            return jsonify({"error":"clave incorrecta"}), 403
        action = str(d.get("action","")).upper()
        sl = float(d.get("sl", 0))
        tp = float(d.get("tp", 0))
        if action == "CLOSE":
            close_all()
            return jsonify({"ok":"cerrado"})
        if action not in ("BUY","SELL"):
            return jsonify({"error":"action invalida"}), 400
        close_all()
        time.sleep(1)
        try:
            post("/openApi/swap/v2/trade/leverage", {"symbol":SYMBOL,"side":"LONG","leverage":str(LEVERAGE)})
            post("/openApi/swap/v2/trade/leverage", {"symbol":SYMBOL,"side":"SHORT","leverage":str(LEVERAGE)})
        except: pass
        p = price()
        qty = math.floor((USDT * LEVERAGE / p) * 1000) / 1000
        qty = max(qty, 0.001)
        side = "BUY" if action=="BUY" else "SELL"
        ps   = "LONG" if action=="BUY" else "SHORT"
        if sl == 0:
            sl = p * (0.98 if action=="BUY" else 1.02)
        if tp == 0:
            tp = p * (1.03 if action=="BUY" else 0.97)
        open_order(side, ps, qty, sl, tp)
        return jsonify({"ok":True,"qty":qty,"price":p,"sl":sl,"tp":tp})
    except Exception as e:
        log.exception("Error webhook")
        return jsonify({"error":str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"ok":True,"demo":DEMO,"symbol":SYMBOL})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",8080)))
