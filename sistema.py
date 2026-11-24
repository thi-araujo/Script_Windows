"""Acesso ao Windows: registro, comandos e PowerShell.

Tudo o que o kit faz no sistema passa pela classe Sistema. No Windows ela usa
o registro de verdade (winreg) e o PowerShell; fora dele, ou nos testes, usa
versões em memória. Assim a lógica pode ser testada em qualquer computador e
a interface abre em "modo demonstração" no Linux/macOS.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HKLM, HKCU, HKU = "HKLM", "HKCU", "HKU"
DWORD, SZ = "DWORD", "SZ"
WINDOWS = platform.system() == "Windows"
SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW: não abre a janela preta do console


class Registro:
    """Interface do registro. `ler` devolve None quando a chave ou o valor não existe."""

    def ler(self, raiz: str, caminho: str, nome: str) -> Any:
        raise NotImplementedError

    def gravar(self, raiz: str, caminho: str, nome: str, valor: Any, tipo: str = DWORD) -> None:
        raise NotImplementedError

    def apagar(self, raiz: str, caminho: str, nome: str) -> None:
        raise NotImplementedError


class RegistroWindows(Registro):
    def __init__(self) -> None:
        import winreg

        self._w = winreg
        self._raizes = {HKLM: winreg.HKEY_LOCAL_MACHINE, HKCU: winreg.HKEY_CURRENT_USER, HKU: winreg.HKEY_USERS}
        self._tipos = {DWORD: winreg.REG_DWORD, SZ: winreg.REG_SZ}

    def ler(self, raiz, caminho, nome):
        try:
            with self._w.OpenKey(self._raizes[raiz], caminho) as chave:
                return self._w.QueryValueEx(chave, nome)[0]
        except FileNotFoundError:
            return None

    def gravar(self, raiz, caminho, nome, valor, tipo=DWORD):
        with self._w.CreateKeyEx(self._raizes[raiz], caminho, 0, self._w.KEY_SET_VALUE) as chave:
            self._w.SetValueEx(chave, nome, 0, self._tipos[tipo], valor)

    def apagar(self, raiz, caminho, nome):
        try:
            with self._w.OpenKey(self._raizes[raiz], caminho, 0, self._w.KEY_SET_VALUE) as chave:
                self._w.DeleteValue(chave, nome)
        except FileNotFoundError:
            pass


class RegistroMemoria(Registro):
    def __init__(self, valores: dict | None = None) -> None:
        self.valores: dict[tuple[str, str, str], Any] = {}
        for (raiz, caminho, nome), valor in (valores or {}).items():
            self.valores[(raiz, caminho.lower(), nome.lower())] = valor

    def ler(self, raiz, caminho, nome):
        return self.valores.get((raiz, caminho.lower(), nome.lower()))

    def gravar(self, raiz, caminho, nome, valor, tipo=DWORD):
        self.valores[(raiz, caminho.lower(), nome.lower())] = valor

    def apagar(self, raiz, caminho, nome):
        self.valores.pop((raiz, caminho.lower(), nome.lower()), None)


Executor = Callable[[list[str]], "tuple[int, str]"]


def executor_windows(comando: list[str]) -> tuple[int, str]:
    try:
        resultado = subprocess.run(comando, capture_output=True, timeout=60, creationflags=SEM_JANELA,
                                   encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as erro:
        return 1, str(erro)
    return resultado.returncode, (resultado.stdout or "") + (resultado.stderr or "")


def comando_powershell(script: str) -> list[str]:
    preambulo = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", preambulo + script]


@dataclass
class Sistema:
    registro: Registro
    executar: Executor
    pastas: dict[str, Path] = field(default_factory=dict)
    administrador: bool = False
    demonstracao: bool = False
    comandos: list[list[str]] = field(default_factory=list)  # histórico, útil nos testes

    def rodar(self, comando: list[str]) -> tuple[int, str]:
        self.comandos.append(comando)
        return self.executar(comando)

    def powershell_json(self, script: str) -> Any:
        """Roda um script PowerShell que termina em ConvertTo-Json e devolve o objeto, ou None."""
        codigo, saida = self.rodar(comando_powershell(script))
        if codigo != 0 or not saida.strip():
            return None
        try:
            return json.loads(saida)
        except ValueError:
            return None


def eh_administrador() -> bool:
    if not WINDOWS:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def reabrir_como_administrador() -> bool:
    if not WINDOWS:
        return False
    executavel = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(executavel):
        executavel = sys.executable
    parametros = " ".join(f'"{a}"' for a in [os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", executavel, parametros, None, 1) > 32


def pastas_windows() -> dict[str, Path]:
    perfil = Path(os.environ.get("USERPROFILE", Path.home()))
    publico = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    programdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    return {
        "desktop_usuario": perfil / "Desktop",
        "desktop_publico": publico / "Desktop",
        "menu_iniciar": programdata / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    }


def sistema_real() -> Sistema:
    return Sistema(RegistroWindows(), executor_windows, pastas_windows(), administrador=eh_administrador())


def sistema_demonstracao(pasta_base: Path) -> Sistema:
    """Sistema simulado com alguns problemas de propósito, para mostrar o kit fora do Windows."""
    from verificacoes import (CAMINHO_FIREWALL_PERFIL, CAMINHO_SISTEMA_POLITICAS, CAMINHO_SMARTSCREEN_POLITICA,
                              CAMINHO_TECLADO_PADRAO)

    registro = RegistroMemoria({
        (HKLM, CAMINHO_SISTEMA_POLITICAS, "EnableLUA"): 1,
        (HKLM, CAMINHO_SMARTSCREEN_POLITICA, "EnableSmartScreen"): 0,
        (HKU, CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators"): "2147483648",
        (HKLM, CAMINHO_FIREWALL_PERFIL.format("PublicProfile"), "EnableFirewall"): 0,
    })
    estado = {"firewall_publico": False}

    def executar(comando: list[str]) -> tuple[int, str]:
        texto = " ".join(comando)
        if "Get-NetFirewallProfile" in texto:
            return 0, json.dumps([{"Name": "Domain", "Enabled": True}, {"Name": "Private", "Enabled": True},
                                  {"Name": "Public", "Enabled": estado["firewall_publico"]}])
        if "Set-NetFirewallProfile" in texto or "advfirewall" in texto:
            estado["firewall_publico"] = True
            return 0, ""
        if "Get-MpComputerStatus" in texto:
            return 0, json.dumps({"AntivirusEnabled": True, "RealTimeProtectionEnabled": True,
                                  "AntivirusSignatureAge": 0})
        if "Get-HotFix" in texto:
            return 0, json.dumps({"HotFixID": "KB5046617", "Dias": 12})
        if "Win32_OperatingSystem" in texto:
            return 0, json.dumps({"Computador": "PC-DEMO", "Usuario": "demo", "Windows": "Windows 11 Pro 24H2",
                                  "LigadoHa": "3 d 4 h", "IP": "192.168.0.15"})
        return 0, ""

    pastas = {"desktop_usuario": pasta_base / "Desktop", "desktop_publico": pasta_base / "Public" / "Desktop",
              "menu_iniciar": pasta_base / "StartMenu"}
    for pasta in pastas.values():
        pasta.mkdir(parents=True, exist_ok=True)
    for nome in ("Word", "Excel", "Accessories/Quick Assist"):
        atalho = pastas["menu_iniciar"] / f"{nome}.lnk"
        atalho.parent.mkdir(parents=True, exist_ok=True)
        atalho.write_bytes(b"atalho de demonstracao")
    return Sistema(registro, executar, pastas, administrador=True, demonstracao=True)
