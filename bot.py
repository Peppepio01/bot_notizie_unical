#!/usr/bin/env python3
"""
Bot Telegram che monitora le notizie pubblicate su unical.it e invia
un messaggio nel gruppo Telegram ogni volta che viene pubblicato un
nuovo contenuto (notizie, avvisi, ecc.).

Funzionamento:
  - Scarica una o più pagine "lista notizie" del sito unical.it
  - Estrae l'ID univoco di ogni notizia dal link (es. /contents/news/view/24107-...)
  - Confronta con l'elenco di ID già visti (salvato in seen_news.json)
  - Per ogni notizia nuova invia un messaggio Telegram al gruppo
  - Salva i nuovi ID come "già visti"

Uso tipico: eseguito periodicamente da cron (consigliato), oppure in
modalità loop continuo con --loop.
"""

import re
import os
import sys
import json
import time
import logging
import argparse
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ----------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------

# Token del bot ottenuto da @BotFather su Telegram.
# Meglio passarlo come variabile d'ambiente TELEGRAM_BOT_TOKEN invece
# di scriverlo qui in chiaro, ma puoi anche incollarlo direttamente.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ID della chat di gruppo (numero negativo tipo -1001234567890).
# Vedi il README per sapere come ottenerlo.
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Pagine del sito da controllare. Puoi aggiungerne altre (es. solo "Avvisi").
# Pagine del sito da controllare. Monitoriamo l'Ateneo principale
# e le bacheche Avvisi (bandi/scadenze) di tutti i 14 Dipartimenti.
NEWS_LIST_URLS = [
    # --- Ateneo Centrale ---
    "https://www.unical.it/contents/news/list",
    "https://www.unical.it/contents/news/list?category_name=Avvisi",
    
    # --- Dipartimenti Area Scienze e Tecnologie ---
    "https://ctc.unical.it/contents/news/list/?category_name=Avvisi",      # Chimica (CTC)
    "https://demacs.unical.it/contents/news/list/?category_name=Avvisi",   # Matematica e Informatica (DeMaCS)
    "https://dibest.unical.it/contents/news/list/?category_name=Avvisi",   # Biologia, Ecologia, Scienze della Terra (DiBEST)
    "https://fisica.unical.it/contents/news/list/?category_name=Avvisi",   # Fisica
    
    # --- Dipartimenti Area Ingegneria ---
    "https://diam.unical.it/contents/news/list/?category_name=Avvisi",     # Ingegneria dell'Ambiente (DIAm)
    "https://dimeg.unical.it/contents/news/list/?category_name=Avvisi",    # Ingegneria Meccanica, Energetica, Gestionale (DIMEG)
    "https://dimes.unical.it/contents/news/list/?category_name=Avvisi",    # Ingegneria Informatica, Elettronica, Sistemistica (DIMES)
    "https://dinci.unical.it/contents/news/list/?category_name=Avvisi",    # Ingegneria Civile (DINCI)
    
    # --- Dipartimenti Area Economico-Sociale e Giuridica ---
    "https://desf.unical.it/contents/news/list/?category_name=Avvisi",     # Economia, Statistica e Finanza (DESF)
    "https://discag.unical.it/contents/news/list/?category_name=Avvisi",   # Scienze Aziendali e Giuridiche (DiScAG)
    "https://dispes.unical.it/contents/news/list/?category_name=Avvisi",   # Scienze Politiche e Sociali (DISPeS)
    
    # --- Dipartimenti Area Umanistica e Medica ---
    "https://dices.unical.it/contents/news/list/?category_name=Avvisi",    # Culture, Educazione e Società (DiCES)
    "https://disu.unical.it/contents/news/list/?category_name=Avvisi",     # Studi Umanistici (DiSU)
    "https://dfssn.unical.it/contents/news/list/?category_name=Avvisi"     # Farmacia e Scienze della Salute (DFSSN)
]

# File dove viene salvato lo stato (quali notizie sono già state notificate).
# Tienilo nella stessa cartella dello script, non cancellarlo.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")

# Pattern che identifica il link a una notizia, es:
# /contents/news/view/24107-unical-e-regione-calabria-intesa-strategica/
NEWS_LINK_RE = re.compile(r"/contents/news/view/(\d+)-")

BASE_URL = "https://www.unical.it"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UnicalNewsTelegramBot/1.0; +https://t.me/)"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("unical_bot")


# ----------------------------------------------------------------------
# STATO (notizie già notificate)
# ----------------------------------------------------------------------

def load_seen_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Impossibile leggere %s (%s), riparto da zero.", STATE_FILE, e)
    return set()


def save_seen_ids(seen_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids, key=int), f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# SCRAPING DEL SITO
# ----------------------------------------------------------------------

def fetch_news(url, page):
    """Carica una pagina di notizie con un browser headless (necessario
    perché il sito carica i contenuti via JavaScript) e restituisce una
    lista di dict {id, title, url}, deduplicati per ID."""
    page.goto(url, wait_until="networkidle", timeout=30000)
    # Piccola attesa extra di sicurezza per contenuti caricati in ritardo.
    page.wait_for_timeout(1500)
    html = page.content()

    soup = BeautifulSoup(html, "lxml")

    items = {}
    for a in soup.find_all("a", href=True):
        match = NEWS_LINK_RE.search(a["href"])
        if not match:
            continue

        news_id = match.group(1)
        title = a.get_text(strip=True)
        href = a["href"]
        if href.startswith("/"):
            href = BASE_URL + href
        href = href.split("?")[0]  # rimuove eventuali parametri tipo ?lang=en

        # Ogni notizia compare due volte nella pagina (link immagine + link
        # titolo): teniamo la versione con il titolo non vuoto.
        if news_id not in items or (title and items[news_id]["title"] == "(senza titolo)"):
            items[news_id] = {"id": news_id, "title": title or "(senza titolo)", "url": href}
          
    log.info("Pagina %s: trovate %d notizie nell'HTML renderizzato.", url, len(items))
    return list(items.values())


def fetch_all_news():
    all_items = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        for url in NEWS_LIST_URLS:
            try:
                for item in fetch_news(url, page):
                    all_items[item["id"]] = item
            except PlaywrightTimeoutError as e:
                log.error("Timeout nel recupero di %s: %s", url, e)
            except Exception as e:
                log.error("Errore nel recupero di %s: %s", url, e)
        browser.close()
    return all_items


# ----------------------------------------------------------------------
# INVIO TELEGRAM
# ----------------------------------------------------------------------

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error(
            "Token o chat_id non configurati. Imposta le variabili d'ambiente "
            "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID (vedi README)."
        )
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, data=payload, timeout=20)
    if not resp.ok:
        log.error("Errore invio Telegram: %s - %s", resp.status_code, resp.text)
    resp.raise_for_status()


# ----------------------------------------------------------------------
# LOGICA PRINCIPALE
# ----------------------------------------------------------------------

def check_for_updates():
    seen_ids = load_seen_ids()
    first_run = len(seen_ids) == 0

    all_items = fetch_all_news()
    if not all_items:
        log.warning("Nessuna notizia trovata: il sito potrebbe aver cambiato struttura, o errore di rete.")
        return

    if first_run:
        # Al primo avvio salviamo tutto come "già visto" senza spammare
        # il gruppo con tutte le notizie storiche.
        log.info("Primo avvio: salvo %d notizie esistenti senza notificare.", len(all_items))
        save_seen_ids(set(all_items.keys()))
        return

    new_items = [item for item in all_items.values() if item["id"] not in seen_ids]

    if not new_items:
        log.info("Nessuna novità (%d notizie controllate).", len(all_items))
        return

    log.info("Trovate %d nuove notizie, invio su Telegram...", len(new_items))
    for item in sorted(new_items, key=lambda x: int(x["id"])):
        text = f"📰 <b>{item['title']}</b>\n{item['url']}"
        try:
            send_telegram_message(text)
            seen_ids.add(item["id"])
            save_seen_ids(seen_ids)  # salva subito, così se qualcosa va storto non perdi il progresso
        except requests.RequestException as e:
            log.error("Invio fallito per la notizia %s: %s", item["id"], e)
        time.sleep(1.5)  # margine di sicurezza per i rate limit di Telegram


def main():
    parser = argparse.ArgumentParser(description="Bot notizie Unical -> Telegram")
    parser.add_argument(
        "--loop", action="store_true",
        help="Esegue in loop continuo invece di controllare una volta sola e uscire."
    )
    parser.add_argument(
        "--interval", type=int, default=15,
        help="Minuti tra un controllo e l'altro in modalità --loop (default: 15)."
    )
    args = parser.parse_args()

    if args.loop:
        log.info("Avvio in modalità loop, controllo ogni %d minuti.", args.interval)
        while True:
            try:
                check_for_updates()
            except Exception as e:
                log.exception("Errore non gestito durante il controllo: %s", e)
            time.sleep(args.interval * 60)
    else:
        check_for_updates()


if __name__ == "__main__":
    main()
