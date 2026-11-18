# Schoolbook Studio (Docker)

Interfaccia web per il progetto `schoolbook_to_pdf`: scegli l'editore, accedi con le credenziali del tuo account, carica la libreria e avvia il download dei libri selezionati.

## Avvio rapido

1. Installa Docker Desktop (Windows/macOS) oppure Docker Engine + Docker Compose (Linux).
2. Estrai questa cartella.
3. Apri un terminale nella cartella che contiene `docker-compose.yml`.
4. Esegui:

   ```bash
   docker compose up -d --build
   ```

5. Apri <http://localhost:8080>.

Per fermare il servizio:

```bash
docker compose down
```

I PDF completati rimangono nella cartella `data/downloads/`, anche dopo il riavvio del container.

## Uso

- Seleziona l'editore.
- Inserisci email/username e password dell'account dell'editore (Palumbo Saggi Digitali, nel codice sorgente, usa un catalogo pubblico e non richiede credenziali).
- Premi **Accedi e carica libri**.
- Cerca e seleziona uno o più libri, quindi avvia il download.
- Puoi scaricare ogni PDF dalla scheda del relativo processo.

Il servizio esegue due download in parallelo per impostazione predefinita. Modifica `DOWNLOAD_WORKERS` nel file `docker-compose.yml` per cambiare il numero (da 1 a 6).

## Sicurezza e limiti

- Le password non vengono scritte su file o database. Sono inviate al solo adattatore dell'editore per ottenere la sessione.
- I token di sessione sono conservati solo nella memoria del processo e spariscono al riavvio del container; il cookie del browser è HttpOnly.
- I PDF vengono salvati nel volume locale `./data`.
- Usa il servizio solo per contenuti ai quali hai diritto di accesso e nel rispetto delle condizioni dell'editore e della normativa applicabile.
- Non esporre direttamente la porta 8080 su Internet. Per accesso remoto configura HTTPS e una protezione aggiuntiva (VPN o reverse proxy con autenticazione).
- Il progetto originale e alcuni adattatori per editori sono datati: un editore può cambiare API o autenticazione e rendere temporaneamente non disponibile il download. Il wrapper web non può correggere un'API lato editore che è stata dismessa.

## Configurazione

- `DOWNLOAD_WORKERS`: download simultanei (1–6; predefinito 2).
- `SESSION_TTL_SECONDS`: durata massima inattiva della sessione (predefinito 8 ore).
- `COOKIE_SECURE=1`: abilita il flag Secure del cookie quando il servizio è pubblicato dietro HTTPS.
- `DATA_DIR`: cartella persistente per i file (in Compose è `/data`).
