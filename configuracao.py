"""Unidades de rede e atalhos da área de trabalho, definidos num arquivo JSON.

No kit original os caminhos e links ficavam fixos dentro dos scripts .bat.
Aqui eles ficam em `kit_ti.json` (veja `kit_ti.exemplo.json`), para o mesmo
programa servir a qualquer empresa sem mexer no código.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sistema import Sistema

ARQUIVO_PADRAO = Path(__file__).resolve().parent / "kit_ti.json"
ARQUIVO_EXEMPLO = Path(__file__).resolve().parent / "kit_ti.exemplo.json"

_LETRA = re.compile(r"^[D-Zd-z]:$")
_UNC = re.compile(r"^\\\\[^\\/:*?\"<>|\s]+(\\[^\\/:*?\"<>|]+)+$")
_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)
_NOME_ARQUIVO_INVALIDO = re.compile(r'[\\/:*?"<>|]')


class ErroConfiguracao(Exception):
    pass


@dataclass
class Unidade:
    letra: str
    caminho: str
    descricao: str = ""


@dataclass
class Atalho:
    nome: str
    url: str = ""           # atalho de site (.url)
    menu_iniciar: str = ""  # nome de um atalho do Menu Iniciar a copiar (ex.: "Word")


@dataclass
class Configuracao:
    unidades: list[Unidade] = field(default_factory=list)
    atalhos: list[Atalho] = field(default_factory=list)
    remover: list[str] = field(default_factory=list)  # atalhos antigos a apagar da área de trabalho
    area_de_trabalho: str = "usuario"                  # "usuario" ou "publica" (todos os usuários)


def validar(dados: dict) -> Configuracao:
    erros = []
    unidades = []
    for i, item in enumerate(dados.get("unidades", []), start=1):
        letra = str(item.get("letra", "")).upper().strip()
        caminho = str(item.get("caminho", "")).strip()
        if not _LETRA.match(letra):
            erros.append(f"unidade {i}: letra inválida \"{letra}\" (use D: a Z:)")
        if not _UNC.match(caminho):
            erros.append(f"unidade {i}: caminho de rede inválido \"{caminho}\" (ex.: \\\\servidor\\pasta)")
        unidades.append(Unidade(letra, caminho, str(item.get("descricao", ""))))
    if len({u.letra for u in unidades}) != len(unidades):
        erros.append("há duas unidades com a mesma letra")

    atalhos = []
    for i, item in enumerate(dados.get("atalhos", []), start=1):
        nome = str(item.get("nome", "")).strip()
        url = str(item.get("url", "")).strip()
        menu = str(item.get("menu_iniciar", "")).strip()
        if not nome or _NOME_ARQUIVO_INVALIDO.search(nome):
            erros.append(f"atalho {i}: nome inválido \"{nome}\"")
        if bool(url) == bool(menu):
            erros.append(f"atalho {i} ({nome}): informe \"url\" ou \"menu_iniciar\", um dos dois")
        elif url and not _URL.match(url):
            erros.append(f"atalho {i} ({nome}): a URL precisa começar com http:// ou https://")
        atalhos.append(Atalho(nome, url, menu))

    remover = [str(n).strip() for n in dados.get("remover", []) if str(n).strip()]
    for nome in remover:
        if _NOME_ARQUIVO_INVALIDO.search(nome.replace(".lnk", "").replace(".url", "")):
            erros.append(f"remover: nome inválido \"{nome}\"")
    area = dados.get("area_de_trabalho", "usuario")
    if area not in ("usuario", "publica"):
        erros.append("area_de_trabalho deve ser \"usuario\" ou \"publica\"")
    if erros:
        raise ErroConfiguracao("\n".join(erros))
    return Configuracao(unidades, atalhos, remover, area)


def carregar(caminho: Path = ARQUIVO_PADRAO) -> Configuracao:
    if not caminho.exists():
        return Configuracao()
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except ValueError as erro:
        raise ErroConfiguracao(f"{caminho.name} não é um JSON válido: {erro}") from erro
    if not isinstance(dados, dict):
        raise ErroConfiguracao(f"{caminho.name} deve conter um objeto JSON")
    return validar(dados)


def criar_a_partir_do_exemplo(destino: Path = ARQUIVO_PADRAO, exemplo: Path = ARQUIVO_EXEMPLO) -> Path:
    if not destino.exists():
        shutil.copyfile(exemplo, destino)
    return destino


# --------------------------------------------------------------------------
# Ações
# --------------------------------------------------------------------------

def mapear(sis: Sistema, unidade: Unidade) -> tuple[bool, str]:
    """Mapeia a unidade (substituindo um mapeamento antigo na mesma letra)."""
    sis.rodar(["net", "use", unidade.letra, "/delete", "/y"])
    codigo, saida = sis.rodar(["net", "use", unidade.letra, unidade.caminho, "/persistent:yes"])
    if codigo == 0:
        return True, f"{unidade.letra} mapeada para {unidade.caminho}"
    return False, f"Falha ao mapear {unidade.letra}: {saida.strip() or 'erro ' + str(codigo)}"


def pasta_desktop(sis: Sistema, config: Configuracao) -> Path:
    return sis.pastas["desktop_publico" if config.area_de_trabalho == "publica" else "desktop_usuario"]


def criar_atalho(sis: Sistema, atalho: Atalho, destino: Path) -> tuple[bool, str]:
    destino.mkdir(parents=True, exist_ok=True)
    if atalho.url:
        arquivo = destino / f"{atalho.nome}.url"
        arquivo.write_text(f"[InternetShortcut]\r\nURL={atalho.url}\r\n", encoding="utf-8")
        return True, f"Atalho criado: {arquivo.name}"
    origem = encontrar_no_menu_iniciar(sis, atalho.menu_iniciar)
    if origem is None:
        return False, f"\"{atalho.menu_iniciar}\" não foi encontrado no Menu Iniciar"
    shutil.copy2(origem, destino / f"{atalho.nome}.lnk")
    return True, f"Atalho criado: {atalho.nome}.lnk"


def encontrar_no_menu_iniciar(sis: Sistema, nome: str) -> Path | None:
    raiz = sis.pastas.get("menu_iniciar")
    if not raiz or not raiz.exists():
        return None
    alvo = f"{nome}.lnk".lower()
    for arquivo in sorted(raiz.rglob("*.lnk")):
        if arquivo.name.lower() == alvo:
            return arquivo
    return None


def remover_antigos(destino: Path, nomes: list[str]) -> list[str]:
    removidos = []
    for nome in nomes:
        candidatos = [destino / nome] if Path(nome).suffix.lower() in (".lnk", ".url") else \
            [destino / f"{nome}.lnk", destino / f"{nome}.url"]
        for arquivo in candidatos:
            if arquivo.is_file():
                arquivo.unlink()
                removidos.append(arquivo.name)
    return removidos
