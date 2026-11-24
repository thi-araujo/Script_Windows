"""Verificações de segurança e ajustes do Windows.

A versão de 2022 deste kit DESATIVAVA o firewall, o UAC e o SmartScreen. Esta
versão faz o contrário: verifica se essas proteções estão ligadas e, se não
estiverem, oferece religá-las. Os ajustes de conveniência (Numlock, pesquisa
na web, impressoras, extensões de arquivo) foram mantidos, com opção de
desfazer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from sistema import DWORD, HKCU, HKLM, HKU, SZ, Sistema

CAMINHO_SISTEMA_POLITICAS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
CAMINHO_SMARTSCREEN_POLITICA = r"SOFTWARE\Policies\Microsoft\Windows\System"
CAMINHO_SMARTSCREEN_EXPLORER = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"
CAMINHO_FIREWALL_PERFIL = r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\{}"
CAMINHO_TECLADO_PADRAO = r".DEFAULT\Control Panel\Keyboard"
CAMINHO_BUSCA = r"Software\Microsoft\Windows\CurrentVersion\Search"
CAMINHO_EXPLORER_POLITICA = r"Software\Policies\Microsoft\Windows\Explorer"
CAMINHO_METADADOS = r"SOFTWARE\Policies\Microsoft\Windows\Device Metadata"
CAMINHO_EXPLORER_AVANCADO = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"

NUMLOCK_LIGADO = "2"
NUMLOCK_PADRAO = "2147483648"


class Estado(Enum):
    OK = "ok"
    ALERTA = "alerta"
    DESCONHECIDO = "desconhecido"


@dataclass
class Resultado:
    nome: str
    estado: Estado
    detalhe: str
    corrigir: Callable[[Sistema], None] | None = None  # presente quando dá para corrigir pelo kit
    exige_reinicio: bool = False


# --------------------------------------------------------------------------
# Segurança (somente leitura + correção)
# --------------------------------------------------------------------------

PERFIS_FIREWALL = {"Domain": "Domínio", "Private": "Privada", "Public": "Pública"}


def verificar_firewall(sis: Sistema) -> Resultado:
    dados = sis.powershell_json("Get-NetFirewallProfile | Select-Object Name, Enabled | ConvertTo-Json")
    if dados is None:
        # Sem PowerShell: lê o registro (não enxerga políticas de grupo).
        perfis = {"Domain": "DomainProfile", "Private": "StandardProfile", "Public": "PublicProfile"}
        dados = [{"Name": nome, "Enabled": sis.registro.ler(HKLM, CAMINHO_FIREWALL_PERFIL.format(chave),
                                                             "EnableFirewall") != 0}
                 for nome, chave in perfis.items()]
    if isinstance(dados, dict):
        dados = [dados]
    desligados = [PERFIS_FIREWALL.get(p.get("Name"), p.get("Name")) for p in dados
                  if not p.get("Enabled") or p.get("Enabled") in (0, "False")]
    if not dados:
        return Resultado("Firewall do Windows", Estado.DESCONHECIDO, "Não foi possível ler os perfis.")
    if desligados:
        return Resultado("Firewall do Windows", Estado.ALERTA,
                         f"Desligado na rede: {', '.join(desligados)}.", corrigir=corrigir_firewall)
    return Resultado("Firewall do Windows", Estado.OK, "Ligado nos perfis Domínio, Privada e Pública.")


def corrigir_firewall(sis: Sistema) -> None:
    codigo, saida = sis.rodar(["netsh", "advfirewall", "set", "allprofiles", "state", "on"])
    if codigo != 0:
        sis.powershell_json("Set-NetFirewallProfile -All -Enabled True")


def verificar_uac(sis: Sistema) -> Resultado:
    lua = sis.registro.ler(HKLM, CAMINHO_SISTEMA_POLITICAS, "EnableLUA")
    consentimento = sis.registro.ler(HKLM, CAMINHO_SISTEMA_POLITICAS, "ConsentPromptBehaviorAdmin")
    if lua == 0:
        return Resultado("Controle de Conta de Usuário (UAC)", Estado.ALERTA,
                         "Desativado: programas rodam como administrador sem pedir confirmação.",
                         corrigir=corrigir_uac, exige_reinicio=True)
    if consentimento == 0:
        return Resultado("Controle de Conta de Usuário (UAC)", Estado.ALERTA,
                         "Ligado, mas eleva administradores sem perguntar.", corrigir=corrigir_uac)
    return Resultado("Controle de Conta de Usuário (UAC)", Estado.OK, "Ativado.")


def corrigir_uac(sis: Sistema) -> None:
    sis.registro.gravar(HKLM, CAMINHO_SISTEMA_POLITICAS, "EnableLUA", 1, DWORD)
    sis.registro.gravar(HKLM, CAMINHO_SISTEMA_POLITICAS, "ConsentPromptBehaviorAdmin", 5, DWORD)  # padrão


def verificar_smartscreen(sis: Sistema) -> Resultado:
    politica = sis.registro.ler(HKLM, CAMINHO_SMARTSCREEN_POLITICA, "EnableSmartScreen")
    explorer = sis.registro.ler(HKLM, CAMINHO_SMARTSCREEN_EXPLORER, "SmartScreenEnabled")
    if politica == 0 or (isinstance(explorer, str) and explorer.lower() == "off"):
        return Resultado("Microsoft Defender SmartScreen", Estado.ALERTA,
                         "Desativado: downloads e programas desconhecidos não são verificados.",
                         corrigir=corrigir_smartscreen)
    return Resultado("Microsoft Defender SmartScreen", Estado.OK, "Ativado.")


def corrigir_smartscreen(sis: Sistema) -> None:
    sis.registro.apagar(HKLM, CAMINHO_SMARTSCREEN_POLITICA, "EnableSmartScreen")
    if str(sis.registro.ler(HKLM, CAMINHO_SMARTSCREEN_EXPLORER, "SmartScreenEnabled") or "").lower() == "off":
        sis.registro.gravar(HKLM, CAMINHO_SMARTSCREEN_EXPLORER, "SmartScreenEnabled", "Warn", SZ)


def verificar_antivirus(sis: Sistema) -> Resultado:
    dados = sis.powershell_json("Get-MpComputerStatus | Select-Object AntivirusEnabled, "
                                "RealTimeProtectionEnabled, AntivirusSignatureAge | ConvertTo-Json")
    if not isinstance(dados, dict):
        outros = sis.powershell_json("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct "
                                     "| Select-Object -ExpandProperty displayName | ConvertTo-Json")
        if outros:
            nomes = outros if isinstance(outros, list) else [outros]
            return Resultado("Antivírus", Estado.OK, f"Antivírus registrado: {', '.join(map(str, nomes))}.")
        return Resultado("Antivírus", Estado.DESCONHECIDO, "Não foi possível consultar o Microsoft Defender.")
    if not dados.get("AntivirusEnabled") or not dados.get("RealTimeProtectionEnabled"):
        return Resultado("Antivírus", Estado.ALERTA, "Proteção em tempo real do Defender desligada.",
                         corrigir=corrigir_antivirus)
    idade = dados.get("AntivirusSignatureAge")
    if isinstance(idade, int) and idade > 7:
        return Resultado("Antivírus", Estado.ALERTA, f"Definições de vírus desatualizadas há {idade} dias.",
                         corrigir=atualizar_definicoes)
    return Resultado("Antivírus", Estado.OK, "Defender ativo, com proteção em tempo real e definições em dia.")


def corrigir_antivirus(sis: Sistema) -> None:
    sis.powershell_json("Set-MpPreference -DisableRealtimeMonitoring $false")


def atualizar_definicoes(sis: Sistema) -> None:
    sis.powershell_json("Update-MpSignature")


def verificar_atualizacoes(sis: Sistema) -> Resultado:
    dados = sis.powershell_json(
        "Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1 "
        "@{n='HotFixID';e={$_.HotFixID}}, @{n='Dias';e={[int]((Get-Date) - $_.InstalledOn).TotalDays}} "
        "| ConvertTo-Json")
    if not isinstance(dados, dict) or not isinstance(dados.get("Dias"), int):
        return Resultado("Atualizações do Windows", Estado.DESCONHECIDO, "Não foi possível ler o histórico.")
    dias = dados["Dias"]
    texto = f"Última atualização ({dados.get('HotFixID', '?')}) instalada há {dias} dia(s)."
    return Resultado("Atualizações do Windows", Estado.ALERTA if dias > 45 else Estado.OK, texto)


VERIFICACOES = [verificar_firewall, verificar_uac, verificar_smartscreen, verificar_antivirus,
                verificar_atualizacoes]


def verificar_tudo(sis: Sistema) -> list[Resultado]:
    resultados = []
    for verificacao in VERIFICACOES:
        try:
            resultados.append(verificacao(sis))
        except OSError as erro:
            nome = verificacao.__name__.replace("verificar_", "").title()
            resultados.append(Resultado(nome, Estado.DESCONHECIDO, f"Erro: {erro}"))
    return resultados


def relatorio(resultados: list[Resultado], info: dict[str, str]) -> str:
    simbolos = {Estado.OK: "[OK]", Estado.ALERTA: "[ALERTA]", Estado.DESCONHECIDO: "[?]"}
    linhas = ["Relatório de segurança do Windows", "=" * 34, ""]
    linhas += [f"{k}: {v}" for k, v in info.items()]
    linhas.append("")
    for r in resultados:
        linhas.append(f"{simbolos[r.estado]:9} {r.nome}: {r.detalhe}")
    alertas = sum(r.estado is Estado.ALERTA for r in resultados)
    linhas += ["", f"Total de alertas: {alertas}"]
    return "\n".join(linhas) + "\n"


def informacoes_sistema(sis: Sistema) -> dict[str, str]:
    dados = sis.powershell_json(
        "$os = Get-CimInstance Win32_OperatingSystem; $t = (Get-Date) - $os.LastBootUpTime; "
        "$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' "
        "-and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1).IPAddress; "
        "[pscustomobject]@{Computador=$env:COMPUTERNAME; Usuario=$env:USERNAME; Windows=$os.Caption; "
        "LigadoHa=('{0} d {1} h' -f $t.Days, $t.Hours); IP=$ip} | ConvertTo-Json")
    if not isinstance(dados, dict):
        return {}
    nomes = {"Computador": "Computador", "Usuario": "Usuário", "Windows": "Sistema", "LigadoHa": "Ligado há",
             "IP": "Endereço IP"}
    return {rotulo: str(dados.get(chave) or "-") for chave, rotulo in nomes.items()}


# --------------------------------------------------------------------------
# Ajustes (vindos do kit original)
# --------------------------------------------------------------------------

@dataclass
class Ajuste:
    nome: str
    descricao: str
    aplicado: Callable[[Sistema], bool]
    aplicar: Callable[[Sistema], None]
    desfazer: Callable[[Sistema], None]
    exige_admin: bool
    efeito: str  # quando a mudança vale


def _numlock_aplicado(sis):
    return str(sis.registro.ler(HKU, CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators")) == NUMLOCK_LIGADO


def _busca_aplicada(sis):
    return (sis.registro.ler(HKCU, CAMINHO_BUSCA, "BingSearchEnabled") == 0
            and sis.registro.ler(HKCU, CAMINHO_EXPLORER_POLITICA, "DisableSearchBoxSuggestions") == 1)


def _aplicar_busca(sis):
    sis.registro.gravar(HKCU, CAMINHO_BUSCA, "BingSearchEnabled", 0)
    sis.registro.gravar(HKCU, CAMINHO_EXPLORER_POLITICA, "DisableSearchBoxSuggestions", 1)


def _desfazer_busca(sis):
    sis.registro.apagar(HKCU, CAMINHO_BUSCA, "BingSearchEnabled")
    sis.registro.apagar(HKCU, CAMINHO_EXPLORER_POLITICA, "DisableSearchBoxSuggestions")


AJUSTES = [
    Ajuste("Numlock ligado na tela de login",
           "O teclado numérico já vem ativo ao digitar a senha.",
           _numlock_aplicado,
           lambda s: s.registro.gravar(HKU, CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators", NUMLOCK_LIGADO, SZ),
           lambda s: s.registro.gravar(HKU, CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators", NUMLOCK_PADRAO, SZ),
           exige_admin=True, efeito="no próximo login"),
    Ajuste("Pesquisa do Iniciar só no computador",
           "Desliga as sugestões do Bing na pesquisa do menu Iniciar (mais rápido e mais privado).",
           _busca_aplicada, _aplicar_busca, _desfazer_busca,
           exige_admin=False, efeito="depois de sair e entrar de novo"),
    Ajuste("Correção da demora ao instalar impressoras",
           "Impede o Windows de baixar metadados de dispositivos da internet a cada instalação.",
           lambda s: s.registro.ler(HKLM, CAMINHO_METADADOS, "PreventDeviceMetadataFromNetwork") == 1,
           lambda s: s.registro.gravar(HKLM, CAMINHO_METADADOS, "PreventDeviceMetadataFromNetwork", 1),
           lambda s: s.registro.apagar(HKLM, CAMINHO_METADADOS, "PreventDeviceMetadataFromNetwork"),
           exige_admin=True, efeito="na próxima instalação"),
    Ajuste("Mostrar extensões de arquivos",
           "Exibe .exe, .pdf, .docx... Ajuda a perceber anexos falsos, como fatura.pdf.exe.",
           lambda s: s.registro.ler(HKCU, CAMINHO_EXPLORER_AVANCADO, "HideFileExt") == 0,
           lambda s: s.registro.gravar(HKCU, CAMINHO_EXPLORER_AVANCADO, "HideFileExt", 0),
           lambda s: s.registro.gravar(HKCU, CAMINHO_EXPLORER_AVANCADO, "HideFileExt", 1),
           exige_admin=False, efeito="ao reabrir o Explorador de Arquivos"),
]
