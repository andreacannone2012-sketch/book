import requests
from Crypto.Cipher import AES
from base64 import b64encode, b64decode
import xml.etree.ElementTree as et
from urllib.parse import urlsplit
import config
import fitz
import lib

service = "rfl"
BASE_URL = "https://www.raffaellodigitale.it/media/raffaelloplayer"
REQUEST_TIMEOUT = (10, 45)

pwdkey = "cre!reDefat&2Nex"
pwdiv = "gETr?sTas*Ku8ewa"

config = config.getconfig()


class RaffaelloAPIError(RuntimeError):
    """Errore leggibile restituito dal servizio Raffaello Player."""


def _response_description(response):
    """Restituisce dettagli limitati e senza parametri sensibili dell'URL."""
    endpoint = urlsplit(response.url).path
    content_type = response.headers.get("Content-Type", "non dichiarato")
    body = (response.text or "").strip().replace("\r", " ").replace("\n", " ")
    if not body:
        body = "risposta vuota"
    else:
        body = body[:180]
    return f"endpoint {endpoint}, HTTP {response.status_code}, Content-Type: {content_type}. Risposta: {body}"


def _get(url, params=None):
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        endpoint = urlsplit(url).path
        raise RaffaelloAPIError(
            f"Impossibile contattare Raffaello Player ({endpoint}): {exc.__class__.__name__}. "
            "Controlla la connessione e riprova."
        ) from exc

    if not response.ok:
        raise RaffaelloAPIError(
            "Raffaello Player ha restituito una risposta HTTP non valida: "
            + _response_description(response)
        )
    return response


def _get_json(url, params=None):
    response = _get(url, params)
    try:
        data = response.json()
    except ValueError as exc:
        raise RaffaelloAPIError(
            "Raffaello Player non ha restituito JSON valido: "
            + _response_description(response)
            + ". Il servizio potrebbe aver cambiato il sistema di accesso/API; "
            "aggiorna il Player e verifica che l'accesso dal sito ufficiale funzioni."
        ) from exc

    if not isinstance(data, dict):
        raise RaffaelloAPIError(
            "Formato della risposta Raffaello non riconosciuto: era atteso un oggetto JSON, "
            f"ricevuto {type(data).__name__}."
        )
    return data


def _get_text(url):
    return _get(url).text


def _get_content(url):
    return _get(url).content


def getlogindata(username, encrpassword):
    return _get_json(
        f"{BASE_URL}/login.php",
        params={"username": username, "password": encrpassword},
    )


def getprofile(token, userid):
    url = f"{BASE_URL}/getprofile.php"
    try:
        response = requests.get(
            url,
            params={"userId": userid, "userToken": token},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RaffaelloAPIError(
            "Impossibile verificare il token Raffaello Player: "
            f"{exc.__class__.__name__}. Controlla la connessione."
        ) from exc

    if response.status_code in (401, 403):
        return False
    if not response.ok:
        raise RaffaelloAPIError(
            "Errore durante la verifica del token Raffaello: "
            + _response_description(response)
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RaffaelloAPIError(
            "La verifica del token Raffaello non ha restituito JSON valido: "
            + _response_description(response)
        ) from exc
    return bool(data)


def getlibrary(token, userid):
    return _get_json(
        f"{BASE_URL}/getcatalog_v7.php",
        params={"userId": userid, "userToken": token},
    )


def getcover(url):
    return _get_content(url)


def getsplash(basepath, projectid):
    return _get_text(basepath + str(projectid) + "/splash.xml")


def getbookinfo(basepath, projectid, ebookid):
    return _get_text(
        basepath + str(projectid) + "/book_" + str(ebookid) + "/xml/book1.xml"
    )


def getpagepng(basepath, projectid, ebookid, data):
    return _get_content(
        basepath + str(projectid) + "/book_" + str(ebookid) + "/pages/" + str(data) + ".png"
    )


def encryptpassword(password):
    cipher = AES.new(pwdkey.encode(), AES.MODE_CBC, pwdiv.encode())
    bsize = AES.block_size
    padding = (bsize - len(password) % bsize) % bsize * b"\0"
    encrpwd = cipher.encrypt(password.encode() + padding)
    return b64encode(encrpwd).decode()


def decryptpassword(encrpassword):
    cipher = AES.new(pwdkey.encode(), AES.MODE_CBC, pwdiv.encode())
    password = cipher.decrypt(b64decode(encrpassword))
    return password.rstrip(b"\0").decode()


def login(username, password):
    data = getlogindata(username, encryptpassword(password))
    token = data.get("token")
    userid = data.get("id")
    if not token or userid is None:
        reason = data.get("message") or data.get("error") or data.get("messageError")
        if reason:
            raise RaffaelloAPIError(f"Accesso Raffaello non riuscito: {reason}")
        raise RaffaelloAPIError(
            "Raffaello Player non ha restituito token e ID utente. "
            "Controlla le credenziali e verifica l'accesso dal sito ufficiale. "
            "Se dal sito riesci ad accedere, questa versione dello script potrebbe usare "
            "un endpoint di login non più supportato."
        )
    return str(token) + "|" + str(userid)


def checktoken(token):
    split = token.split("|")
    if len(split) != 2:
        return False
    stoken, userid = split
    return bool(getprofile(stoken, userid))


def library(token):
    parts = token.split("|")
    if len(parts) != 2:
        raise RaffaelloAPIError("Token Raffaello non valido: formato errato.")
    stoken, userid = parts
    data = getlibrary(stoken, userid)
    categories = data.get("categories")
    if not isinstance(categories, list):
        raise RaffaelloAPIError(
            "La risposta della libreria Raffaello non contiene la sezione 'categories'. "
            "Il token potrebbe essere scaduto oppure l'API potrebbe essere cambiata."
        )

    books = {}
    for category in categories:
        for book in category.get("items", []):
            if not book.get("userActive") and not config.getboolean(
                service, "IncludeNotActivated", fallback=False
            ):
                continue
            book_id = str(book["id"])
            books[book_id] = {"title": book.get("name", f"Libro {book_id}")}
            if book.get("cover"):
                books[book_id]["cover"] = book["cover"]
    return books


def cover(token, bookid, data):
    # Corretto: in precedenza veniva usata la variabile inesistente "url".
    if data.get("cover"):
        return getcover(data["cover"])
    return b""


def downloadbook(token, bookid, data, progress):
    pdf = fitz.Document()
    toc = []
    labels = []

    parts = token.split("|")
    if len(parts) != 2:
        raise RaffaelloAPIError("Token Raffaello non valido: formato errato.")
    stoken, userid = parts
    progress(1, "Getting book info")
    library_data = getlibrary(stoken, userid)

    try:
        book = next(
            item
            for category in library_data["categories"]
            for item in category.get("items", [])
            if str(item.get("id")) == str(bookid)
        )
    except (KeyError, StopIteration) as exc:
        raise RaffaelloAPIError(
            f"Il libro {bookid} non è presente nella libreria restituita da Raffaello."
        ) from exc

    basepath = book["projectBasePath"]
    projects = book.get("subProjects", [])
    if not projects:
        raise RaffaelloAPIError(
            "Raffaello non ha restituito sezioni per questo libro. "
            "Il libro potrebbe non essere attivo o il catalogo potrebbe essere cambiato."
        )

    sectionwidth = 97 / len(projects)
    for i, project in enumerate(projects):
        projectid = project["projectId"]
        toc.append([1, project.get("projectTitle", f"Sezione {i + 1}"), len(pdf) + 1])

        progress(1 + sectionwidth * i, f"Processing section {i + 1}/{len(projects)}")
        try:
            splash = et.fromstring(getsplash(basepath, projectid))
        except et.ParseError as exc:
            raise RaffaelloAPIError(
                f"Il file splash.xml della sezione {i + 1} non è XML valido."
            ) from exc

        slide = splash.find("slide")
        elements = slide.find("elementi") if slide is not None else None
        if elements is None or elements.get("url") != "ebook":
            continue

        ebookid = elements.get("codice")
        if not ebookid:
            continue
        try:
            bookinfo = et.fromstring(getbookinfo(basepath, projectid, ebookid))
        except et.ParseError as exc:
            raise RaffaelloAPIError(
                f"Il file book1.xml della sezione {i + 1} non è XML valido."
            ) from exc

        pages_node = bookinfo.find("pages")
        pages = pages_node.findall("page") if pages_node is not None else []
        for j, page in enumerate(pages):
            progress(
                1 + sectionwidth * i + sectionwidth / 10
                + (sectionwidth * 9 / 10) * j / max(len(pages), 1),
                f"Downloading page {j + 1}/{len(pages)}",
            )
            page_data = page.get("data")
            if not page_data:
                continue
            pagepng = getpagepng(basepath, projectid, ebookid, page_data)
            try:
                img = fitz.open(stream=pagepng, filetype="png")
                pdfbytes = img.convert_to_pdf()
                pdf.insert_pdf(fitz.open(stream=pdfbytes, filetype="pdf"))
            except Exception as exc:
                raise RaffaelloAPIError(
                    f"Impossibile elaborare la pagina {j + 1} della sezione {i + 1}."
                ) from exc
            labels.append(page.get("number"))

    if len(pdf) == 0:
        raise RaffaelloAPIError(
            "Non è stata scaricata nessuna pagina. Il contenuto potrebbe non essere "
            "disponibile per il download da questa versione del Player."
        )

    progress(98, "Applying toc/labels")
    if toc:
        pdf.set_toc(toc)
    if labels:
        pdf.set_page_labels(lib.generatelabelsrule(labels))
    return pdf
