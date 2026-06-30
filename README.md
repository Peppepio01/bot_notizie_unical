# Bot Telegram – Notizie UniCal (via GitHub Actions)

Monitora `https://www.unical.it/contents/news/list` e invia un messaggio nel
vostro gruppo Telegram ogni volta che viene pubblicata una nuova notizia.

Gira interamente su **GitHub Actions**: nessun PC deve restare acceso.
GitHub stesso esegue lo script ogni 20 minuti, gratuitamente.

## 1. Crea il bot Telegram (se non l'hai già fatto)

1. Apri Telegram, cerca **@BotFather**.
2. Manda `/newbot`, segui le istruzioni.
3. Ti darà un **token**, tipo `123456789:AAFFwexample-token-qui`. Tienilo
   da parte, ti servirà al passo 4.

## 2. Aggiungi il bot al gruppo e trova il chat_id

1. Aggiungi il bot al gruppo Telegram.
2. Manda nel gruppo un messaggio che inizi con `/`, es: `/id` (serve perché
   per i messaggi normali nei gruppi il bot ha la "privacy mode" attiva e
   non li riceve).
3. Apri nel browser, sostituendo `<TOKEN>`:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Cerca nel risultato il blocco con `"type":"group"`. Il numero in
   `"chat":{"id": ... }` (negativo, tipo `-1001234567890`) è il tuo
   `chat_id`.

## 3. Crea il repository su GitHub

1. Su github.com, crea un nuovo repository (può essere **privato**, va
   benissimo — anzi consigliato, dato che riguarda un vostro gruppo).
2. Carica dentro tutti i file di questa cartella, mantenendo la struttura:
   ```
   bot.py
   requirements.txt
   seen_news.json
   .github/workflows/unical-news.yml
   ```
   La cartella `.github/workflows/` è quella che GitHub riconosce
   automaticamente per far partire le esecuzioni programmate — non
   rinominarla.

   Modo più semplice se non hai familiarità con git: su GitHub, dalla
   pagina del repository, usa **Add file → Upload files** e trascina tutto
   (per la cartella `.github/workflows/unical-news.yml` puoi crearla a mano
   con **Add file → Create new file** e scrivere quel percorso come nome
   del file).

## 4. Configura i secrets (token e chat_id)

Nel repository su GitHub:

1. **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**
   - Nome: `TELEGRAM_BOT_TOKEN` — valore: il token di BotFather
3. **New repository secret**
   - Nome: `TELEGRAM_CHAT_ID` — valore: il chat_id del gruppo (col meno
     davanti, es. `-1001234567890`)

Così le credenziali restano cifrate e non finiscono mai nel codice visibile.

## 5. Primo avvio

Vai sulla tab **Actions** del repository, seleziona il workflow
**"Unical News Bot"**, e clicca **Run workflow** per lanciarlo manualmente
la prima volta.

Al primissimo avvio lo script salva tutte le notizie *attuali* come "già
viste" senza inviare nulla al gruppo (altrimenti vi arriverebbero tutte
insieme le notizie storiche). Da quel momento in poi, ogni nuova notizia
pubblicata sul sito verrà inviata al gruppo entro 20 minuti.

Puoi verificare che sia andato tutto bene controllando i log
dell'esecuzione nella tab Actions, e controllando che il file
`seen_news.json` nel repository si sia aggiornato con degli ID.

## 6. Da qui in poi

Non devi fare più nulla. GitHub esegue il workflow ogni 20 minuti in
automatico, in modo del tutto indipendente dal vostro PC.

Unica accortezza: se il repository resta completamente inattivo per
**60 giorni consecutivi**, GitHub disattiva automaticamente i workflow
schedulati. Nel nostro caso però il workflow stesso aggiorna
`seen_news.json` ad ogni esecuzione (quando trova notizie nuove), quindi
finché ci sono notizie pubblicate su unical.it il repository resta
"attivo" e non scatta questo limite. Se invece notate che le notifiche si
sono fermate dopo molto tempo di silenzio, basta andare sulla tab Actions
e cliccare di nuovo **Run workflow** per riattivarlo.

## Personalizzazioni

- Per cambiare la frequenza dei controlli, modifica la riga `cron` in
  `.github/workflows/unical-news.yml` (il formato è minuti-ore-giorno-mese-
  giorno_settimana; `*/20 * * * *` = ogni 20 minuti).
- Per monitorare anche solo la sezione "Avvisi" (bandi, scadenze), in
  `bot.py` decommenta la riga:
  ```python
  # "https://www.unical.it/contents/news/list?category_name=Avvisi",
  ```
- Se il sito unical.it cambia struttura HTML in futuro, potrebbe servire
  un piccolo aggiornamento al selettore nella funzione `fetch_news()`.
