#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import time
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ========================
# CONFIG
# ========================

EMAIL = "nomasparami689@gmail.com"
PASSWORD = "Eliu2121."
TARGET_LOGIN = "https://www.shopify.com/login"

# Tarjeta de prueba Shopify (NO real)
TEST_CARD = {
    "first": "David",
    "last": "Luna",
    "number": "4242424242424242",
    "expiry": "12/30",
    "cvc": "123",
    "address": "Test Street 123",
    "country": "Chile",
    "zip": "8320000"
}

# Telegram
TOKEN = "8306237380:AAFE6hUB2G8QpQHoyEeWgxKrGNmzw16OV1U"
CHAT_ID = "8372126424"

# ========================
# FUNCIONES
# ========================

def start(update: Update, context: CallbackContext):
    update.message.reply_text("🤖🐼 Bot Shopify listo. Usa /pagar para login + pago test.")

def pagar(update: Update, context: CallbackContext):
    update.message.reply_text("💳🦊 Iniciando login + pago de prueba...")

    driver = None
    try:
        print("🦁 Configurando Chrome...")
        # Configuración de Chrome
        options = webdriver.ChromeOptions()
        # options.add_argument("--headless")  # descomenta si quieres sin ventana
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--start-maximized")

        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        print("🐨 Navegador lanzado correctamente.")

        print("🦉 Abriendo página de login...")
        driver.get(TARGET_LOGIN)

        # === LOGIN ===
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "account[email]")))
        driver.find_element(By.NAME, "account[email]").send_keys(EMAIL)
        driver.find_element(By.NAME, "commit").click()
        print("🐸 Email ingresado.")

        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "account[password]")))
        driver.find_element(By.NAME, "account[password]").send_keys(PASSWORD)
        driver.find_element(By.NAME, "commit").click()
        print("🐵 Password ingresado, iniciando sesión...")

        # 📸 Captura después del login
        screenshot_login = driver.get_screenshot_as_png()
        context.bot.send_photo(chat_id=CHAT_ID, photo=io.BytesIO(screenshot_login), caption="📸🐱 Captura después del login")
        print("🐯 Captura enviada a Telegram (login).")

        # === FORMULARIO TARJETA ===
        print("🦊 Rellenando formulario de pago...")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "number")))
        driver.find_element(By.NAME, "number").send_keys(TEST_CARD["number"])
        driver.find_element(By.NAME, "expiry").send_keys(TEST_CARD["expiry"])
        driver.find_element(By.NAME, "verification_value").send_keys(TEST_CARD["cvc"])
        driver.find_element(By.NAME, "name").send_keys(TEST_CARD["first"] + " " + TEST_CARD["last"])
        driver.find_element(By.NAME, "address1").send_keys(TEST_CARD["address"])
        driver.find_element(By.NAME, "zip").send_keys(TEST_CARD["zip"])

        print("🐻 Presionando botón de pagar...")
        driver.find_element(By.XPATH, "//button[contains(text(),'Pagar')]").click()

        # 📸 Captura después del pago
        screenshot_pago = driver.get_screenshot_as_png()
        context.bot.send_photo(chat_id=CHAT_ID, photo=io.BytesIO(screenshot_pago), caption="📸🐶 Captura después del pago")
        print("🦄 Captura enviada a Telegram (pago).")

        update.message.reply_text("✅🦋 Pago de prueba realizado (tarjeta test Shopify).")

    except Exception as e:
        update.message.reply_text(f"⚠️🐙 Error durante el proceso: {e}")
        print(f"❌🐧 Error en el bot: {e}")

    finally:
        if driver:
            driver.quit()
            print("🦊 Navegador cerrado correctamente.")

# ========================
# MAIN
# ========================

def main():
    print("🚀🐹 Iniciando bot de Telegram...")
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("pagar", pagar))

    updater.start_polling()
    print("🐢 Bot escuchando en Telegram... Usa /start o /pagar")
    updater.idle()

if __name__ == "__main__":
    main()
