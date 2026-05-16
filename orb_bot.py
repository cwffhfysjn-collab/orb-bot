import os
import hmac
import hashlib
import time
import json
import logging
import requests
from flask import Flask, request, jsonify

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
SYMBOL           = os.getenv("SYMBOL", "BTC-USDT")
LEVERAGE         = int(os.getenv("LEVERAGE", "5"))
USDT_PER_TRADE   = float(os.getenv("USDT_PER_TRADE", "50"))
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "clave123bot")
USE_DEMO         = os.getenv("USE_DEMO", "true").lower() == "true"

BASE_URL = "https://open-api-vst.bingx.com" if USE_DEMO else "https://open-api.bingx.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

def _sign(params):
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(BINGX_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def _ts():
    return str(int(time.time() * 1000))

def bingx_post(endpoint, params):
    params["timestamp"] = _ts()
    params["signature"] = _sign(params)
    headers = {"X-BX-APIKEY": BINGX_API_KEY, "Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(BASE_URL + endpoint, headers=headers, data=params, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get("code", 0) != 0:
        raise Exception(f"BingX error {result.get('code')}: {result.get('msg')}")
    return result

def bingx_get(endpoint, params):
    params["timestamp"] = _ts()
    params["signature"] = _sign(params)
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = requests.get(BASE_URL + endpoint, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get("code", 0) != 0:
        raise Exception(f"BingX error {result.get('code')}: {result.get('msg')}")
    return result

def set_leverage():
    try:
        bingx_post("/openApi/swap/v2/trade/leverage", {"symbol": SYMBOL, "side": "LONG", "leverage": str(LEVERAGE)})
        bingx_post("/openApi/swap/v2/trade/leverage", {"symbol": SYMBOL, "side": "SHORT", "leverage": str(LEVERAGE)})
        log.info(f"Leverage seteado a {LEVERAGE}x")
    except Exception as e:
        log.warning(f"Leverage: {e}")

def get_price():
    resp = bingx_get("/openApi/swap/v2/quote/price", {"symbol": SYMBOL})
    return float(resp["data"]["price"])

def close_positions():
    try:
        resp = bingx_get("/openApi/swap/v2/user/positions", {"symbol": SYMBOL})
        for pos in resp.get("data", []):
            size = float(pos.get("positionAmt", 0))
            if abs(size) > 0:
                pos_side = pos.get("positionSide", "")
                close_side = "SELL" if pos_side == "LONG" else "BUY"
                bingx_post("/openApi/swap/v2/trade/order", {
                    "symbol": SYMBOL, "side": close_side,
                    "positionSide": pos_side, "type": "MARKET", "quantity": str(abs(size))
                })
                log.info(f"Cerrada posicion {pos_side} {abs(size)}")
    except Exception as e:
        log.error(f"Error cerrando: {e}")

def place_order(side, position_side, qty, sl, tp):
    resp = bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL, "side": side,
        "positionSide": position_side, "type": "MARKET", "quantity": str(qty)
    })
    log.info(f"Orden entrada: {resp}")
    sl_side = "SELL" if position_side == "LONG" else "BUY"
    try:
        bingx_post("/openApi/swap/v2/trade/order", {
            "symbol": SYMBOL, "side": sl_side, "positionSide": position_side,
            "type": "STOP_MARKET", "stopPrice": str(round(sl, 2)),
            "closePosition": "true", "workingType": "MARK_PRICE"
        })
        log.info(f"SL seteado en {sl}")
    except Exception as e:
        log.warning(f"Error SL: {e}")
    try:
        bingx_post("/openApi/swap/v2/trade/order", {
            "symbol": SYMBOL, "side": sl_side, "positionSide": position_side,
            "type": "TAKE_PROFIT_MARKET", "stopPrice": str(round(tp, 2)),
            "closePosition": "true", "workingType": "MARK_PRICE"
        })
        log.info(f"TP seteado en {tp}")
    except Exception as e:
        log.warning(f"Error TP: {e}")
    return resp

def process_signal(data):
    if data.get("secret") != WEBHOOK_SECRET:
        log.warning("Clave incorrecta")
        return {"status": "error", "msg": "clave incorrecta"}
    action = str(data.get("action", "")).upper()
    sl = float(data.get("sl", 0))
    tp = float(data.get("tp", 0))
    log.info(f"Senal: action={action} sl={sl} tp={tp}")
    if action == "CLOSE":
        close_positions()
        return {"status": "ok", "msg": "cerrado"}
    if action not in ("BUY", "SELL"):
        return {"status": "error", "msg": f"action desconocida: {action}"}
    if sl == 0 or tp == 0:
        return {"status": "error", "msg": "sl o tp son 0"}
    close_positions()
    time.sleep(0.5)
    price = get_price()
    min_qty = 0.001
    import math
    raw_qty = (USDT_PER_TRADE * LEVERAGE) / price
    qty = math.floor(raw_qty * 1000) / 1000
    if qty < min_qty:
        return {"status": "error", "msg": f"cantidad {qty} menor al minimo"}
    set_leverage()
    side = "BUY" if action == "BUY" else "SELL"
    position_side = "LONG" if action == "BUY" else "SHORT"
    resp = place_order(side, position_side, qty, sl, tp)
    return {"status": "ok", "qty": qty, "price": price}

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "msg": "body vacio"}), 400
        result = process_signal(data)
        return jsonify(result), 200
    except Exception as e:
        log.exception("Error webhook")
        return jsonify({"status": "error", "msg": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "symbol": SYMBOL, "demo": USE_DEMO}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    log.info(f"Bot iniciado | Puerto:{port} | Demo:{USE_DEMO} | Par:{SYMBOL}")
    app.run(host="0.0.0.0", port=port)
