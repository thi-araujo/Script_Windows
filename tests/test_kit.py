import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import configuracao as cf  # noqa: E402
import verificacoes as vf  # noqa: E402
from sistema import HKCU, HKLM, HKU, RegistroMemoria, Sistema, sistema_demonstracao  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


class ExecutorFalso:
    """Responde comandos conforme um trecho do texto; o resto devolve erro."""

    def __init__(self, respostas=None):
        self.respostas = respostas or {}

    def __call__(self, comando):
        texto = " ".join(comando)
        for trecho, resposta in self.respostas.items():
            if trecho in texto:
                return resposta if isinstance(resposta, tuple) else (0, json.dumps(resposta))
        return 1, "comando não simulado"


def sistema(registro=None, respostas=None, pastas=None):
    return Sistema(RegistroMemoria(registro), ExecutorFalso(respostas), pastas or {}, administrador=True)


class TestFirewall(unittest.TestCase):
    def test_todos_ligados(self):
        perfis = [{"Name": n, "Enabled": True} for n in ("Domain", "Private", "Public")]
        r = vf.verificar_firewall(sistema(respostas={"Get-NetFirewallProfile": perfis}))
        self.assertEqual(r.estado, vf.Estado.OK)
        self.assertIsNone(r.corrigir)

    def test_perfil_desligado_pode_ser_corrigido(self):
        perfis = [{"Name": "Domain", "Enabled": True}, {"Name": "Public", "Enabled": False}]
        sis = sistema(respostas={"Get-NetFirewallProfile": perfis, "advfirewall": (0, "Ok.")})
        r = vf.verificar_firewall(sis)
        self.assertEqual(r.estado, vf.Estado.ALERTA)
        self.assertIn("Pública", r.detalhe)
        r.corrigir(sis)
        self.assertEqual(sis.comandos[-1], ["netsh", "advfirewall", "set", "allprofiles", "state", "on"])

    def test_sem_powershell_usa_registro(self):
        caminho = vf.CAMINHO_FIREWALL_PERFIL.format("PublicProfile")
        r = vf.verificar_firewall(sistema(registro={(HKLM, caminho, "EnableFirewall"): 0}))
        self.assertEqual(r.estado, vf.Estado.ALERTA)


class TestUacSmartScreen(unittest.TestCase):
    def test_uac_desativado_e_corrigido(self):
        sis = sistema({(HKLM, vf.CAMINHO_SISTEMA_POLITICAS, "EnableLUA"): 0})
        r = vf.verificar_uac(sis)
        self.assertEqual(r.estado, vf.Estado.ALERTA)
        self.assertTrue(r.exige_reinicio)
        r.corrigir(sis)
        self.assertEqual(vf.verificar_uac(sis).estado, vf.Estado.OK)

    def test_uac_sem_confirmacao(self):
        sis = sistema({(HKLM, vf.CAMINHO_SISTEMA_POLITICAS, "EnableLUA"): 1,
                       (HKLM, vf.CAMINHO_SISTEMA_POLITICAS, "ConsentPromptBehaviorAdmin"): 0})
        self.assertEqual(vf.verificar_uac(sis).estado, vf.Estado.ALERTA)

    def test_uac_padrao_ok(self):
        self.assertEqual(vf.verificar_uac(sistema()).estado, vf.Estado.OK)

    def test_smartscreen(self):
        sis = sistema({(HKLM, vf.CAMINHO_SMARTSCREEN_POLITICA, "EnableSmartScreen"): 0,
                       (HKLM, vf.CAMINHO_SMARTSCREEN_EXPLORER, "SmartScreenEnabled"): "Off"})
        r = vf.verificar_smartscreen(sis)
        self.assertEqual(r.estado, vf.Estado.ALERTA)
        r.corrigir(sis)
        self.assertIsNone(sis.registro.ler(HKLM, vf.CAMINHO_SMARTSCREEN_POLITICA, "EnableSmartScreen"))
        self.assertEqual(sis.registro.ler(HKLM, vf.CAMINHO_SMARTSCREEN_EXPLORER, "SmartScreenEnabled"), "Warn")
        self.assertEqual(vf.verificar_smartscreen(sis).estado, vf.Estado.OK)


class TestAntivirusEAtualizacoes(unittest.TestCase):
    def test_defender_ok(self):
        r = vf.verificar_antivirus(sistema(respostas={"Get-MpComputerStatus": {
            "AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntivirusSignatureAge": 1}}))
        self.assertEqual(r.estado, vf.Estado.OK)

    def test_defender_desatualizado(self):
        r = vf.verificar_antivirus(sistema(respostas={"Get-MpComputerStatus": {
            "AntivirusEnabled": True, "RealTimeProtectionEnabled": True, "AntivirusSignatureAge": 20}}))
        self.assertEqual(r.estado, vf.Estado.ALERTA)
        self.assertIn("20 dias", r.detalhe)

    def test_outro_antivirus(self):
        r = vf.verificar_antivirus(sistema(respostas={"SecurityCenter2": ["Kaspersky Endpoint Security"]}))
        self.assertEqual(r.estado, vf.Estado.OK)
        self.assertIn("Kaspersky", r.detalhe)

    def test_desconhecido(self):
        self.assertEqual(vf.verificar_antivirus(sistema()).estado, vf.Estado.DESCONHECIDO)

    def test_atualizacoes(self):
        recente = sistema(respostas={"Get-HotFix": {"HotFixID": "KB1", "Dias": 10}})
        antiga = sistema(respostas={"Get-HotFix": {"HotFixID": "KB1", "Dias": 90}})
        self.assertEqual(vf.verificar_atualizacoes(recente).estado, vf.Estado.OK)
        self.assertEqual(vf.verificar_atualizacoes(antiga).estado, vf.Estado.ALERTA)

    def test_relatorio(self):
        resultados = vf.verificar_tudo(sistema({(HKLM, vf.CAMINHO_SISTEMA_POLITICAS, "EnableLUA"): 0}))
        texto = vf.relatorio(resultados, {"Computador": "PC-01"})
        self.assertIn("Computador: PC-01", texto)
        self.assertIn("[ALERTA]  Controle de Conta de Usuário (UAC)", texto)


class TestAjustes(unittest.TestCase):
    def test_aplicar_e_desfazer_todos(self):
        sis = sistema()
        for ajuste in vf.AJUSTES:
            self.assertFalse(ajuste.aplicado(sis), ajuste.nome)
            ajuste.aplicar(sis)
            self.assertTrue(ajuste.aplicado(sis), ajuste.nome)
            ajuste.desfazer(sis)
            self.assertFalse(ajuste.aplicado(sis), ajuste.nome)

    def test_numlock_valores(self):
        sis = sistema({(HKU, vf.CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators"): "2147483648"})
        numlock = vf.AJUSTES[0]
        numlock.aplicar(sis)
        self.assertEqual(sis.registro.ler(HKU, vf.CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators"), "2")
        numlock.desfazer(sis)
        self.assertEqual(sis.registro.ler(HKU, vf.CAMINHO_TECLADO_PADRAO, "InitialKeyboardIndicators"),
                         "2147483648")

    def test_busca_exige_as_duas_chaves(self):
        sis = sistema({(HKCU, vf.CAMINHO_BUSCA, "BingSearchEnabled"): 0})
        self.assertFalse(vf.AJUSTES[1].aplicado(sis))

    def test_nenhum_ajuste_desliga_protecao(self):
        sis = sistema()
        for ajuste in vf.AJUSTES:
            ajuste.aplicar(sis)
        alertas = [r.nome for r in (vf.verificar_uac(sis), vf.verificar_smartscreen(sis))
                   if r.estado is vf.Estado.ALERTA]
        self.assertEqual(alertas, [])


class TestConfiguracao(unittest.TestCase):
    def test_exemplo_e_valido(self):
        config = cf.carregar(RAIZ / "kit_ti.exemplo.json")
        self.assertEqual(len(config.unidades), 2)
        self.assertEqual(config.unidades[0].caminho, r"\\servidor-arquivos\dados")
        self.assertEqual(sum(1 for a in config.atalhos if a.url), 2)

    def test_erros_de_validacao(self):
        dados = {"unidades": [{"letra": "C:", "caminho": "servidor/dados"}, {"letra": "F:", "caminho": r"\\s\p"},
                              {"letra": "f:", "caminho": r"\\s\q"}],
                 "atalhos": [{"nome": "Site", "url": "ftp://x"}, {"nome": "a/b", "url": "https://x"},
                             {"nome": "Nada"}],
                 "area_de_trabalho": "todas"}
        with self.assertRaises(cf.ErroConfiguracao) as contexto:
            cf.validar(dados)
        mensagem = str(contexto.exception)
        for trecho in ("letra inválida", "caminho de rede inválido", "mesma letra", "http://", "nome inválido",
                       "um dos dois", "area_de_trabalho"):
            self.assertIn(trecho, mensagem)

    def test_arquivo_ausente_e_invalido(self):
        with tempfile.TemporaryDirectory() as pasta:
            self.assertEqual(cf.carregar(Path(pasta) / "nao_existe.json"), cf.Configuracao())
            ruim = Path(pasta) / "kit.json"
            ruim.write_text("{", encoding="utf-8")
            with self.assertRaises(cf.ErroConfiguracao):
                cf.carregar(ruim)


class TestAcoes(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        base = Path(self.pasta.name)
        self.menu = base / "Start Menu" / "Programs"
        (self.menu / "Accessories").mkdir(parents=True)
        (self.menu / "Accessories" / "Quick Assist.lnk").write_bytes(b"lnk-falso")
        self.desktop = base / "Desktop"
        self.sis = sistema(respostas={"net use": (0, "Comando concluído.")},
                           pastas={"menu_iniciar": self.menu, "desktop_usuario": self.desktop,
                                   "desktop_publico": base / "Public"})

    def tearDown(self):
        self.pasta.cleanup()

    def test_mapear(self):
        ok, mensagem = cf.mapear(self.sis, cf.Unidade("F:", r"\\srv\dados"))
        self.assertTrue(ok, mensagem)
        self.assertEqual(self.sis.comandos[0], ["net", "use", "F:", "/delete", "/y"])
        self.assertEqual(self.sis.comandos[1], ["net", "use", "F:", r"\\srv\dados", "/persistent:yes"])

    def test_mapear_falha(self):
        sis = sistema(respostas={"/persistent": (2, "Erro de sistema 53.")})
        ok, mensagem = cf.mapear(sis, cf.Unidade("F:", r"\\srv\dados"))
        self.assertFalse(ok)
        self.assertIn("53", mensagem)

    def test_atalho_url(self):
        ok, _ = cf.criar_atalho(self.sis, cf.Atalho("Suporte", url="https://suporte.exemplo.com"), self.desktop)
        self.assertTrue(ok)
        conteudo = (self.desktop / "Suporte.url").read_text(encoding="utf-8")
        self.assertIn("URL=https://suporte.exemplo.com", conteudo)

    def test_atalho_do_menu_iniciar(self):
        ok, _ = cf.criar_atalho(self.sis, cf.Atalho("Assistência", menu_iniciar="quick assist"), self.desktop)
        self.assertTrue(ok)
        self.assertEqual((self.desktop / "Assistência.lnk").read_bytes(), b"lnk-falso")
        ok, mensagem = cf.criar_atalho(self.sis, cf.Atalho("X", menu_iniciar="Inexistente"), self.desktop)
        self.assertFalse(ok)
        self.assertIn("não foi encontrado", mensagem)

    def test_remover_antigos(self):
        self.desktop.mkdir()
        for nome in ("Word 2016.lnk", "Intranet antiga.url", "Manter.lnk"):
            (self.desktop / nome).write_text("x")
        removidos = cf.remover_antigos(self.desktop, ["Word 2016", "Intranet antiga.url", "Inexistente"])
        self.assertEqual(sorted(removidos), ["Intranet antiga.url", "Word 2016.lnk"])
        self.assertTrue((self.desktop / "Manter.lnk").exists())


class TestDemonstracao(unittest.TestCase):
    def test_demo_tem_alertas_corrigiveis(self):
        with tempfile.TemporaryDirectory() as pasta:
            sis = sistema_demonstracao(Path(pasta))
            alertas = [r for r in vf.verificar_tudo(sis) if r.estado is vf.Estado.ALERTA]
            self.assertEqual({r.nome for r in alertas}, {"Firewall do Windows", "Microsoft Defender SmartScreen"})
            for r in alertas:
                r.corrigir(sis)
            self.assertFalse(any(r.estado is vf.Estado.ALERTA for r in vf.verificar_tudo(sis)))
            self.assertEqual(vf.informacoes_sistema(sis)["Computador"], "PC-DEMO")


if __name__ == "__main__":
    unittest.main()
