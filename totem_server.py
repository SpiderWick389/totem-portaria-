"""Servidor do totem de pedestres (FastAPI).

Instalar:  pip install fastapi uvicorn
Rodar:     uvicorn totem_server:app --host 127.0.0.1 --port 8000
Totem:     chromium --kiosk --use-fake-ui-for-media-stream "http://localhost:8000/?api=/api"
           (a flag libera a câmera sem perguntar; o navegador precisa de localhost ou https)
(Coloque totem-pedestres.html na mesma pasta deste arquivo.)
"""
import base64
import hashlib
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

AQUI = Path(__file__).parent
MODO_TESTE = True  # True: biometria e impressora simuladas

DB = sqlite3.connect(AQUI / "acessos.db", check_same_thread=False)
DB.execute("create table if not exists acessos(ts text, cpf_hash text, evento text, detalhe text)")
app = FastAPI()
VERIFICADOS: dict[str, float] = {}  # cpf -> momento da biometria aprovada


def log(cpf: str, evento: str, detalhe: str = ""):
    """Trilha de auditoria. Guarda só um hash do CPF (LGPD)."""
    h = hashlib.sha256(cpf.encode()).hexdigest()[:16]
    DB.execute("insert into acessos values(datetime('now','localtime'),?,?,?)", (h, evento, detalhe))
    DB.commit()


# ---- 1) INTEGRAÇÃO: sistema de pré-agendamento -----------------------------
AGENDAMENTOS = {  # dados de exemplo; troque pela consulta real
    "12345678909": {"n": "Carlos Mendes", "e": "TecnoPorto Manutenção", "g": "Prestador de serviço",
                    "c": "#0A3CA8", "d": "Armazém 3", "w": [8, 17], "day": 0,
                    "epi": ["Capacete", "Colete", "Botina", "Óculos"],
                    "doc": ["ASO", "Integração de segurança", "Seguro"]},
    # Estrangeiros: a chave é "tipo:número" (passaporte, rnm, cir)
    "passaporte:XA123456": {"n": "John Carter", "e": "Marine Survey Ltd", "g": "Inspetor naval",
                            "c": "#0A2150", "d": "Berço 2", "w": [8, 18], "day": 0,
                            "epi": ["Capacete", "Colete", "Botina", "Óculos"],
                            "doc": ["Passaporte", "Integração de segurança"]},
}


def buscar_agendamento(cpf: str):
    """Devolve o agendamento do CPF ou None.
    Real: consultar API/banco do sistema de agendamento e preencher "day"
    (0 = vale hoje, 1 = outro dia) comparando a data e a janela de horário."""
    return AGENDAMENTOS.get(cpf)


# ---- 2) INTEGRAÇÃO: leitor biométrico --------------------------------------
def verificar_biometria(cpf: str) -> bool:
    """Captura a digital e compara 1:1 com o template cadastrado desse CPF.
    Real: usar o SDK do fabricante do leitor. O template fica só no servidor;
    o navegador recebe apenas ok/não ok."""
    if MODO_TESTE:
        time.sleep(0.5)
        return True
    raise NotImplementedError("Ligar o SDK do leitor aqui")


# ---- 3) INTEGRAÇÃO: impressora de crachá -----------------------------------
def imprimir_cracha(dados: dict):
    """Real: enviar ZPL/ESC-POS/PDF para a impressora (CUPS ou driver do fabricante)."""
    print("IMPRIMIR CRACHÁ:", dados["numero"], dados["n"], dados["g"], dados["d"])


# ---- API -------------------------------------------------------------------
class Cpf(BaseModel):
    cpf: str


class Cracha(Cpf):
    numero: str


@app.get("/api/agendamento/{cpf}")
def agendamento(cpf: str):
    a = buscar_agendamento(cpf)
    if not a:
        log(cpf, "sem_agendamento")
        return {"erro": "nb"}
    if a.get("day"):
        log(cpf, "fora_da_janela")
        return {"erro": "out"}
    log(cpf, "agendamento_ok", a["g"])
    return a


@app.post("/api/biometria")
def biometria(b: Cpf):
    ok = verificar_biometria(b.cpf)
    if ok:
        VERIFICADOS[b.cpf] = time.time()
    log(b.cpf, "biometria_ok" if ok else "biometria_falhou")
    return {"ok": ok}


@app.post("/api/cracha")
def cracha(c: Cracha):
    a = buscar_agendamento(c.cpf)
    if not a or time.time() - VERIFICADOS.pop(c.cpf, 0) > 300:
        log(c.cpf, "cracha_negado")  # sem biometria aprovada nos últimos 5 min
        return {"erro": "nb"}
    imprimir_cracha({**a, "numero": c.numero})
    log(c.cpf, "cracha_emitido", c.numero)
    return {"ok": True}


class Foto(Cracha):
    foto: str  # data URL JPEG


@app.post("/api/foto")
def foto(f: Foto):
    """Guarda a foto do rosto tirada na retirada do crachá. Definir prazo de retenção (LGPD)."""
    if not buscar_agendamento(f.cpf):
        return {"erro": "nb"}
    pasta = AQUI / "fotos"
    pasta.mkdir(exist_ok=True)
    nome = "".join(ch for ch in f.numero if ch.isalnum()) + time.strftime("_%Y%m%d_%H%M%S") + ".jpg"
    (pasta / nome).write_bytes(base64.b64decode(f.foto.split(",", 1)[-1]))
    log(f.cpf, "foto_registrada", nome)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(AQUI / "totem-pedestres.html")
