# bot_render_ready_full_safe.py
"""
Bot Telegram + FastAPI listo para Render — versión FULL (seguro):
- Scrapea precio real de AliExpress, Amazon, eBay, Shein y MercadoLibre.
- Convierte siempre a moneda de la tienda.
- Aplica margen de ganancia fijo (%).
- Guarda cost_price en moneda de la tienda en metafield.
- Orders/create → calcula ganancia exacta y avisa por Telegram.
"""

import re
import threading
import logging
import hmac
import hashlib
import base64
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Dict, Any
import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ---- DECIMAL PRECISION ----
getcontext().prec = 12

# ---- LOGGING ----
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dropship-bot-full")

# ---- CONFIG (TUS DATOS) ----
TELEGRAM_TOKEN = "8349334622:AAG00FW8ItpNFMLWYwQ_G_ODPGkKLfnh58g"
TELEGRAM_CHAT_ID = 7863949269
SHOPIFY_STORE = "4dtxbz-hh.myshopify.com"
SHOPIFY_TOKEN = "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"
SHOPIFY_SHARED_SECRET = "25890a32b9e633afba6f3fe8e565d409"

MARGIN_PERCENT = Decimal("30")  # margen fijo %
PORT = 8000

# ---- REQUESTS SESSION con RETRIES ----
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.6, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; dropship-bot-full/1.0)"}

# ---- UTILIDADES ----
def q2_decimal(x) -> Decimal:
    d = Decimal(str(x).replace(",", "").strip())
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def aplicar_margen_decimal(precio_coste: Decimal) -> Decimal:
    return q2_decimal(precio_coste * (Decimal(1) + (MARGIN_PERCENT / Decimal(100))))

def shopify_headers():
    return {
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
        "Content-Type": "application/json"
    }

# ---- INFO DE LA TIENDA ----
def get_shop_info() -> Dict[str, Any]:
    try:
        url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/shop.json"
        r = session.get(url, headers=shopify_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("shop", {})
        return {
            "currency": data.get("currency", "USD"),
            "locale": data.get("primary_locale", "en")
        }
    except Exception as e:
        logger.warning("No se pudo obtener info de tienda: %s. Usando defaults USD/en.", e)
        return {"currency": "USD", "locale": "en"}

SHOP_INFO = get_shop_info()
SHOP_CURRENCY = SHOP_INFO["currency"]
SHOP_LOCALE = SHOP_INFO["locale"]

# ---- CONVERSIÓN DE MONEDA ----
def convertir_moneda(amount: Decimal, from_cur: str, to_cur: str) -> Decimal:
    if from_cur.upper() == to_cur.upper():
        return q2_decimal(amount)
    try:
        url = f"https://api.exchangerate.host/convert?from={from_cur}&to={to_cur}&amount={amount}"
        r = session.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        result = data.get("result")
        if result:
            return q2_decimal(result)
    except Exception as e:
        logger.warning("Error conversión moneda: %s", e)
    return q2_decimal(amount)

# ---- TRADUCCIÓN ----
def translate_text(text: str, target_lang: str = "en") -> str:
    try:
        q = requests.utils.requote_uri(text)[:1000]
        url = f"https://api.mymemory.translated.net/get?q={q}&langpair=auto|{target_lang}"
        r = session.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        return j.get("responseData", {}).get("translatedText", text)
    except Exception:
        return text

# ---- SCRAPERS ----
def scrape_aliexpress(url: str) -> Dict[str, Any]:
    try:
        r = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        price = None
        currency = "USD"

        script = soup.find("script", text=re.compile("window.runParams"))
        if script and script.string:
            m = re.search(r'"formatedActivityPrice":"([^"]+)"', script.string) or \
                re.search(r'"formatedPrice":"([^"]+)"', script.string)
            if m:
                raw_price = m.group(1)
                parts = raw_price.replace("$", "").split()
                if len(parts) == 2:
                    currency, val = parts
                else:
                    currency, val = "USD", parts[0]
                val = val.replace(".", "").replace(",", ".")
                price = q2_decimal(val)

        if not price:
            meta_price = soup.find("meta", {"property": "og:price:amount"})
            meta_cur = soup.find("meta", {"property": "og:price:currency"})
            if meta_price:
                price = q2_decimal(meta_price["content"])
            if meta_cur:
                currency = meta_cur["content"]

        if not price or price <= 0:
            raise ValueError("No se pudo obtener precio real del producto.")

        title = soup.title.get_text(strip=True) if soup.title else "Producto AliExpress"
        images = []
        for img in soup.find_all("img")[:3]:
            src = img.get("src") or img.get("image-src")
            if src and src.startswith("http"):
                images.append(src)

        return {
            "title": title,
            "price": price,
            "currency": currency,
            "images": images or ["https://via.placeholder.com/600x600?text=AliExpress"],
            "variants": [{"option1": "Default", "price": price}]
        }
    except Exception as e:
        logger.error("Scraper AliExpress error: %s", e)
        raise

def scrape_amazon(url: str) -> Dict[str, Any]:
    try:
        r = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.find(id="productTitle").get_text(strip=True) if soup.find(id="productTitle") else "Producto Amazon"
        price_el = soup.find("span", {"class": "a-price-whole"})
        price = q2_decimal(price_el.get_text(strip=True).replace(".", "").replace(",", ".")) if price_el else Decimal("0")
        currency = "USD"
        img = soup.find("img", {"id": "landingImage"})
        images = [img["src"]] if img else ["https://via.placeholder.com/600x600?text=Amazon"]
        return {"title": title, "price": price, "currency": currency, "images": images, "variants": [{"option1": "Default", "price": price}]}
    except Exception as e:
        logger.error("Scraper Amazon error: %s", e)
        raise

def scrape_ebay(url: str) -> Dict[str, Any]:
    try:
        r = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.find("h1", {"class": "x-item-title__mainTitle"}).get_text(strip=True) if soup.find("h1", {"class": "x-item-title__mainTitle"}) else "Producto eBay"
        price_el = soup.find("span", {"itemprop": "price"})
        price = q2_decimal(price_el["content"]) if price_el and price_el.has_attr("content") else Decimal("0")
        currency = price_el["content"].replace(" ", "") if price_el and price_el.has_attr("content") else "USD"
        images = ["https://via.placeholder.com/600x600?text=eBay"]
        return {"title": title, "price": price, "currency": currency, "images": images, "variants": [{"option1": "Default", "price": price}]}
    except Exception as e:
        logger.error("Scraper eBay error: %s", e)
        raise

def scrape_shein(url: str) -> Dict[str, Any]:
    try:
        r = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else "Producto Shein"
        price_el = soup.find("span", {"class": "original"})
        price = q2_decimal(price_el.get_text(strip=True).replace("$", "").replace(",", ".")) if price_el else Decimal("0")
        currency = "USD"
        images = ["https://via.placeholder.com/600x600?text=Shein"]
        return {"title": title, "price": price, "currency": currency, "images": images, "variants": [{"option1": "Default", "price": price}]}
    except Exception as e:
        logger.error("Scraper Shein error: %s", e)
        raise

def scrape_mercadolibre(url: str) -> Dict[str, Any]:
    try:
        r = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.find("h1", {"class": "ui-pdp-title"}).get_text(strip=True) if soup.find("h1", {"class": "ui-pdp-title"}) else "Producto MercadoLibre"
        price_el = soup.find("span", {"class": "andes-money-amount__fraction"})
        price = q2_decimal(price_el.get_text(strip=True).replace(".", "").replace(",", ".")) if price_el else Decimal("0")
        currency = "USD"
        images = ["https://via.placeholder.com/600x600?text=MercadoLibre"]
        return {"title": title, "price": price, "currency": currency, "images": images, "variants": [{"option1": "Default", "price": price}]}
    except Exception as e:
        logger.error("Scraper MercadoLibre error: %s", e)
        raise

def generic_scrape(url: str) -> Dict[str, Any]:
    if "aliexpress" in url:
        return scrape_aliexpress(url)
    elif "amazon" in url:
        return scrape_amazon(url)
    elif "ebay" in url:
        return scrape_ebay(url)
    elif "shein" in url:
        return scrape_shein(url)
    elif "mercadolibre" in url or "meli" in url:
        return scrape_mercadolibre(url)
    else:
        raise ValueError("No se reconoce la tienda del link.")

# ---- CREAR PRODUCTO EN SHOPIFY ----
def shopify_create_product_with_conversion(product: Dict[str, Any]) -> Dict[str, Any]:
    target_lang = SHOP_LOCALE.split("-")[0]

    title_translated = translate_text(product["title"], target_lang=target_lang)
    body_translated = translate_text(product.get("body_html", ""), target_lang=target_lang)

    cost_in_shop_currency = convertir_moneda(product["price"], product["currency"], SHOP_CURRENCY)
    price_with_margin = aplicar_margen_decimal(cost_in_shop_currency)

    payload = {
        "product": {
            "title": title_translated,
            "body_html": body_translated,
            "images": [{"src": u} for u in product.get("images", [])],
            "variants": [{"option1": "Default", "price": str(price_with_margin)}]
        }
    }

    url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/products.json"
    r = session.post(url, headers=shopify_headers(), json=payload, timeout=20)
    r.raise_for_status()
    prod = r.json().get("product", {})

    try:
        variant_id = prod["variants"][0]["id"]
        meta_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/metafields.json"
        metafield = {
            "metafield": {
                "namespace": "global",
                "key": "cost_price",
                "value": str(cost_in_shop_currency),
                "type": "single_line_text_field",
                "owner_resource": "variant",
                "owner_id": variant_id,
            }
        }
        session.post(meta_url, headers=shopify_headers(), json=metafield, timeout=10)
    except Exception as e:
        logger.warning("No se pudo guardar metafield cost_price: %s", e)

    return prod, cost_in_shop_currency, price_with_margin

# ---- TELEGRAM ----
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐼 Bot listo. Mándame un link de producto (AliExpress, Amazon, eBay, Shein, MercadoLibre) y lo subo a Shopify con margen.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not re.match(r'https?://', text):
        await update.message.reply_text("Envíame un link válido (http/https).")
        return
    await update.message.reply_text("🔎 Procesando link...")

    try:
        parsed = generic_scrape(text)
        product = {
            "title": parsed["title"],
            "body_html": f"<p>Importado desde {text}</p>",
            "images": parsed["images"],
            "variants": parsed["variants"],
            "price": parsed["price"],
            "currency": parsed["currency"],
        }

        resp, cost_converted, price_final = shopify_create_product_with_conversion(product)

        reply = (
            f"✅ Producto creado en Shopify\n"
            f"🧾 {product['title']}\n"
            f"💲 Coste proveedor (convertido): {cost_converted} {SHOP_CURRENCY}\n"
            f"💲 Precio final en tienda: {price_final} {SHOP_CURRENCY}\n"
            f"📈 Ganancia esperada: {q2_decimal(price_final - cost_converted)} {SHOP_CURRENCY}\n"
            f"📦 ID: {resp.get('id', 'N/A')}"
        )
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Error procesando: {e}")

# ---- FASTAPI + WEBHOOK ----
app = FastAPI()

def verify_webhook(data: bytes, hmac_header: str) -> bool:
    digest = hmac.new(SHOPIFY_SHARED_SECRET.encode("utf-8"), data, hashlib.sha256).digest()
    computed_hmac = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed_hmac, hmac_header)

@app.post("/webhook/shopify")
async def shopify_webhook(req: Request):
    raw_body = await req.body()
    hmac_header = req.headers.get("X-Shopify-Hmac-Sha256", "")
    if not verify_webhook(raw_body, hmac_header):
        raise HTTPException(status_code=401, detail="Firma inválida")

    payload = await req.json()
    topic = req.headers.get("X-Shopify-Topic", "")

    if topic == "orders/create":
        try:
            order_id = payload.get("id")
            customer = payload.get("customer", {}).get("first_name", "Cliente")
            total_price = Decimal(payload.get("total_price", "0"))

            line_items = payload.get("line_items", [])
            cost_price = Decimal("0")
            if line_items:
                variant_id = line_items[0].get("variant_id")
                if variant_id:
                    meta_url = f"https://{SHOPIFY_STORE}/admin/api/2023-10/variants/{variant_id}/metafields.json"
                    r = session.get(meta_url, headers=shopify_headers(), timeout=10)
                    if r.ok:
                        metas = r.json().get("metafields", [])
                        for m in metas:
                            if m.get("namespace") == "global" and m.get("key") == "cost_price":
                                cost_price = Decimal(m.get("value", "0"))
                                break

            profit = q2_decimal(total_price - cost_price)

            text = (
                f"🎉 ¡Nueva venta en Shopify!\n"
                f"🧑 Cliente: {customer}\n"
                f"🧾 Orden #{order_id}\n"
                f"💰 Total: {total_price} {SHOP_CURRENCY}\n"
                f"💸 Costo: {cost_price} {SHOP_CURRENCY}\n"
                f"📈 Ganancia: {profit} {SHOP_CURRENCY}"
            )

            tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            session.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})
        except Exception as e:
            logger.error("Error procesando webhook: %s", e)

    return {"ok": True}

# ---- MAIN ----
def start_fastapi(port):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

def main():
    run_port = PORT
    t = threading.Thread(target=start_fastapi, args=(run_port,), daemon=True)
    t.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling()


