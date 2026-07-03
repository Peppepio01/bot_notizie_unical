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
import hashlib
import logging
import argparse
import requests
from urllib.parse import urljoin, urlparse
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

# Pagine del sito da controllare per NUOVE NOTIZIE/AVVISI/BANDI.
# I bandi usano esattamente la stessa struttura di link delle notizie
# (/contents/news/view/ID-slug/), quindi vengono gestiti dallo stesso
# meccanismo, semplicemente aggiungendo le loro pagine "lista" qui sotto.
NEWS_LIST_URLS = [
    # --- Ateneo Centrale ---
    "https://www.unical.it/contents/news/list",
    "https://www.unical.it/contents/news/list?category_name=Avvisi",

    # --- Dipartimenti Area Scienze e Tecnologie: Avvisi ---
    "https://ctc.unical.it/contents/news/list/?category_name=Avvisi",      # Chimica (CTC)
    "https://demacs.unical.it/contents/news/list/?category_name=Avvisi",   # Matematica e Informatica (DeMaCS)
    "https://dibest.unical.it/contents/news/list/?category_name=Avvisi",   # Biologia, Ecologia, Scienze della Terra (DiBEST)
    "https://fisica.unical.it/contents/news/list/?category_name=Avvisi",   # Fisica

    # --- Dipartimenti Area Ingegneria: Avvisi ---
    "https://diam.unical.it/contents/news/list/?category_name=Avvisi",     # Ingegneria dell'Ambiente (DIAm)
    "https://dimeg.unical.it/contents/news/list/?category_name=Avvisi",    # Ingegneria Meccanica, Energetica, Gestionale (DIMEG)
    "https://dimes.unical.it/contents/news/list/?category_name=Avvisi",    # Ingegneria Informatica, Elettronica, Sistemistica (DIMES)
    "https://dinci.unical.it/contents/news/list/?category_name=Avvisi",    # Ingegneria Civile (DINCI)

    # --- Dipartimenti Area Economico-Sociale e Giuridica: Avvisi ---
    "https://desf.unical.it/contents/news/list/?category_name=Avvisi",     # Economia, Statistica e Finanza (DESF)
    "https://discag.unical.it/contents/news/list/?category_name=Avvisi",   # Scienze Aziendali e Giuridiche (DiScAG)
    "https://dispes.unical.it/contents/news/list/?category_name=Avvisi",   # Scienze Politiche e Sociali (DISPeS)

    # --- Dipartimenti Area Umanistica e Medica: Avvisi ---
    "https://dices.unical.it/contents/news/list/?category_name=Avvisi",    # Culture, Educazione e Società (DiCES)
    "https://disu.unical.it/contents/news/list/?category_name=Avvisi",     # Studi Umanistici (DiSU)
    "https://dfssn.unical.it/contents/news/list/?category_name=Avvisi",    # Farmacia e Scienze della Salute (DFSSN)

    # --- Bandi e Concorsi per Dipartimento ---
    # Stessa struttura delle pagine "Avvisi", solo sotto un percorso diverso.
    "https://ctc.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://demacs.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dibest.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://fisica.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://diam.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dimeg.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dimes.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dinci.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://desf.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://discag.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dispes.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dices.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://disu.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
    "https://dfssn.unical.it/dipartimento/organizzazione/documenti-bandi-e-concorsi/contents/news/list?category_name=avvisi",
]

# Pagine "statiche" da monitorare per MODIFICHE AL CONTENUTO (non hanno un
# elenco di notizie datate, ma vengono aggiornate di tanto in tanto, es.
# nuovi importi/scadenze delle tasse). Il bot avvisa quando il testo della
# pagina cambia rispetto all'ultimo controllo, senza sapere esattamente
# cosa è cambiato: utile comunque come "campanello d'allarme".
PAGE_WATCH_URLS = [
    "https://www.unical.it/didattica/iscriversi-studiare-laurearsi/tasse-ed-esoneri/",
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
# STATO (notizie già notificate + hash delle pagine monitorate)
# ----------------------------------------------------------------------

def load_state():
    """Carica lo stato salvato: {"news_seen": [...], "page_hashes": {...}}.

    Gestisce la migrazione automatica dai formati precedenti, usati nelle
    versioni passate dello script:
      - Gen 1: lista JSON di soli numeri, es. ["24107", "24108"]
      - Gen 2: lista JSON di "dominio:numero", es. ["www.unical.it:24107"]
      - Gen 3 (attuale): dict con chiavi "news_seen" e "page_hashes"

    Senza questa migrazione, ogni cambio di formato farebbe sembrare tutte
    le notizie vecchie "nuove", con conseguente spam nel gruppo.
    """
    if not os.path.exists(STATE_FILE):
        return {"news_seen": set(), "page_hashes": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Impossibile leggere %s (%s), riparto da zero.", STATE_FILE, e)
        return {"news_seen": set(), "page_hashes": {}}

    if isinstance(raw, dict):
        # Già nel formato attuale.
        return {
            "news_seen": set(raw.get("news_seen", [])),
            "page_hashes": dict(raw.get("page_hashes", {})),
        }

    # Formato vecchio: lista piatta (Gen 1 o Gen 2). Migriamo al volo.
    migrated = set()
    for entry in raw:
        entry = str(entry)
        if ":" in entry:
            migrated.add(entry)
        else:
            # Vecchissimo formato: assumiamo provenisse dall'unico dominio
            # monitorato all'epoca, www.unical.it.
            migrated.add(f"www.unical.it:{entry}")

    log.info("Migrazione di %d ID al nuovo formato di stato (dict con news_seen/page_hashes).", len(migrated))
    state = {"news_seen": migrated, "page_hashes": {}}
    save_state(state)
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"news_seen": sorted(state["news_seen"]), "page_hashes": state["page_hashes"]},
            f, ensure_ascii=False, indent=2
        )


# ----------------------------------------------------------------------
# SCRAPING DEL SITO
# ----------------------------------------------------------------------

def fetch_news(url, page, max_attempts=2):
    """Carica una pagina di notizie con un browser headless (necessario
    perché il sito carica i contenuti via JavaScript) e restituisce una
    lista di dict {id, title, url}, deduplicati per ID.

    Riprova automaticamente se il primo tentativo va in timeout, e usa una
    condizione di attesa più tollerante rispetto a "tutta la rete ferma"
    (alcune pagine non smettono mai di fare piccole richieste in
    background, es. analytics, e "networkidle" non scatterebbe mai)."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Aspetta che compaia almeno un link a una notizia, senza
            # bloccarsi se la sezione è legittimamente vuota (es. nessun
            # avviso al momento).
            try:
                page.wait_for_selector('a[href*="/contents/news/view/"]', timeout=8000)
            except PlaywrightTimeoutError:
                pass  # può darsi che semplicemente non ci siano notizie
            page.wait_for_timeout(1500)  # margine extra per contenuti in ritardo
            html = page.content()
            break
        except PlaywrightTimeoutError as e:
            last_error = e
            log.warning("Tentativo %d/%d fallito per %s (timeout).", attempt, max_attempts, url)
    else:
        raise last_error

    soup = BeautifulSoup(html, "lxml")

    items = {}
    for a in soup.find_all("a", href=True):
        match = NEWS_LINK_RE.search(a["href"])
        if not match:
            continue

        news_id = match.group(1)
        title = a.get_text(strip=True)
        # urljoin gestisce correttamente sia i link relativi ("/contents/...")
        # sia quelli assoluti, rispettando il dominio della pagina di
        # partenza (es. dimes.unical.it invece di www.unical.it).
        href = urljoin(url, a["href"]).split("?")[0]

        # Ogni notizia compare due volte nella pagina (link immagine + link
        # titolo): teniamo la versione con il titolo non vuoto.
        if news_id not in items or (title and not items[news_id]["title"]):
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
                    # Prefisso con il dominio: pagine diverse (es.
                    # www.unical.it e dimes.unical.it) possono avere
                    # notizie con lo stesso ID numerico ma sono contenuti
                    # diversi, quindi vanno tenuti distinti.
                    domain = urlparse(url).netloc
                    item["unique_key"] = f"{domain}:{item['id']}"
                    all_items[item["unique_key"]] = item
            except PlaywrightTimeoutError as e:
                log.error("Timeout nel recupero di %s (dopo i tentativi previsti): %s", url, e)
            except Exception as e:
                log.error("Errore nel recupero di %s: %s", url, e)
        browser.close()
    return all_items


def fetch_page_text_hash(url, page, max_attempts=2):
    """Carica una pagina "statica" (non un elenco di notizie) e restituisce
    un hash del suo contenuto testuale visibile, per poterlo confrontare
    nel tempo e rilevare eventuali modifiche."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            html = page.content()
            break
        except PlaywrightTimeoutError as e:
            last_error = e
            log.warning("Tentativo %d/%d fallito per %s (timeout).", attempt, max_attempts, url)
    else:
        raise last_error

    soup = BeautifulSoup(html, "lxml")
    # Rimuoviamo script/stili, che possono cambiare senza che il
    # contenuto informativo della pagina sia realmente cambiato.
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ", strip=True).split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_page_changes(state):
    """Controlla le pagine statiche configurate (PAGE_WATCH_URLS) e
    notifica se il loro contenuto è cambiato rispetto all'ultimo
    controllo."""
    if not PAGE_WATCH_URLS:
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        for url in PAGE_WATCH_URLS:
            try:
                current_hash = fetch_page_text_hash(url, page)
            except PlaywrightTimeoutError as e:
                log.error("Timeout nel controllo della pagina %s: %s", url, e)
                continue
            except Exception as e:
                log.error("Errore nel controllo della pagina %s: %s", url, e)
                continue

            previous_hash = state["page_hashes"].get(url)

            if previous_hash is None:
                # Prima volta che monitoriamo questa pagina: salviamo il
                # riferimento senza notificare (non abbiamo un "prima" con
                # cui confrontarla).
                log.info("Pagina %s monitorata per la prima volta: salvo il riferimento senza notificare.", url)
                state["page_hashes"][url] = current_hash
                save_state(state)
            elif previous_hash != current_hash:
                log.info("Rilevata modifica nella pagina %s, invio notifica su Telegram...", url)
                text = f"🔔 <b>Pagina aggiornata</b>\n{url}"
                try:
                    send_telegram_message(text)
                    state["page_hashes"][url] = current_hash
                    save_state(state)
                except requests.RequestException as e:
                    log.error("Invio fallito per la modifica di %s: %s", url, e)
            else:
                log.info("Nessuna modifica per %s.", url)
        browser.close()


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
    state = load_state()
    seen_ids = state["news_seen"]
    first_run = len(seen_ids) == 0

    all_items = fetch_all_news()
    if not all_items:
        log.warning("Nessuna notizia trovata: il sito potrebbe aver cambiato struttura, o errore di rete.")
    elif first_run:
        # Al primissimo avvio in assoluto salviamo tutto come "già visto"
        # senza spammare il gruppo con tutte le notizie storiche.
        log.info("Primo avvio: salvo %d notizie esistenti senza notificare.", len(all_items))
        state["news_seen"] = set(all_items.keys())
        save_state(state)
    else:
        # Domini già monitorati in precedenza (dedotti dagli ID salvati).
        known_domains = {key.split(":", 1)[0] for key in seen_ids}

        # Se è stata aggiunta una nuova pagina/dipartimento mai vista
        # prima, "fotografiamo" silenziosamente le sue notizie attuali
        # invece di notificarle tutte insieme come fossero appena uscite.
        new_domain_items = [item for item in all_items.values() if item["unique_key"].split(":", 1)[0] not in known_domains]
        if new_domain_items:
            new_domains = sorted({item["unique_key"].split(":", 1)[0] for item in new_domain_items})
            log.info(
                "Rilevati %d nuovi domini mai monitorati prima (%s): salvo %d notizie esistenti senza notificare.",
                len(new_domains), ", ".join(new_domains), len(new_domain_items)
            )
            for item in new_domain_items:
                seen_ids.add(item["unique_key"])
            save_state(state)

        new_items = [
            item for item in all_items.values()
            if item["unique_key"] not in seen_ids
            and item["unique_key"].split(":", 1)[0] in known_domains
        ]

        if not new_items:
            log.info("Nessuna novità tra le notizie (%d controllate).", len(all_items))
        else:
            log.info("Trovate %d nuove notizie, invio su Telegram...", len(new_items))
            for item in sorted(new_items, key=lambda x: (x["unique_key"].split(":")[0], int(x["id"]))):
                text = f"📰 <b>{item['title']}</b>\n{item['url']}"
                try:
                    send_telegram_message(text)
                    seen_ids.add(item["unique_key"])
                    save_state(state)  # salva subito, così se qualcosa va storto non perdi il progresso
                except requests.RequestException as e:
                    log.error("Invio fallito per la notizia %s: %s", item["unique_key"], e)
                time.sleep(1.5)  # margine di sicurezza per i rate limit di Telegram

    # Controllo separato per le pagine statiche (es. tasse), che non hanno
    # un elenco di notizie datate ma vengono comunque aggiornate a volte.
    check_page_changes(state)


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
