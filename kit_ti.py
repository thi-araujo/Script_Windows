"""Kit de Suporte TI para Windows, com interface gráfica.

    python kit_ti.py

Algumas ações (UAC, SmartScreen, Numlock no login, impressoras) exigem
executar como administrador. Fora do Windows o programa abre em modo
demonstração, com um sistema simulado.
"""

from __future__ import annotations

import os
import platform
import queue
import subprocess
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import configuracao as cf
import verificacoes as vf
from sistema import WINDOWS, Sistema, reabrir_como_administrador, sistema_demonstracao, sistema_real

CORES = {
    "fundo": "#0f172a",
    "painel": "#1e293b",
    "borda": "#334155",
    "texto": "#e2e8f0",
    "texto_suave": "#94a3b8",
    "destaque": "#38bdf8",
    "verde": "#22c55e",
    "vermelho": "#ef4444",
    "ambar": "#f59e0b",
}
COR_ESTADO = {vf.Estado.OK: CORES["verde"], vf.Estado.ALERTA: CORES["ambar"],
              vf.Estado.DESCONHECIDO: CORES["texto_suave"]}
TEXTO_ESTADO = {vf.Estado.OK: "OK", vf.Estado.ALERTA: "ALERTA", vf.Estado.DESCONHECIDO: "?"}

FONTE = "Segoe UI" if WINDOWS else "Helvetica"
FONTE_MONO = "Consolas" if WINDOWS else "DejaVu Sans Mono"


def configurar_estilo(raiz: tk.Tk) -> None:
    estilo = ttk.Style(raiz)
    estilo.theme_use("clam")
    sem_borda = dict(bordercolor=CORES["painel"], lightcolor=CORES["painel"], darkcolor=CORES["painel"])
    fundo_sem_borda = dict(bordercolor=CORES["fundo"], lightcolor=CORES["fundo"], darkcolor=CORES["fundo"])
    estilo.configure(".", background=CORES["fundo"], foreground=CORES["texto"], font=(FONTE, 10))
    estilo.configure("Painel.TFrame", background=CORES["painel"])
    estilo.configure("Painel.TLabel", background=CORES["painel"])
    estilo.configure("PainelSuave.TLabel", background=CORES["painel"], foreground=CORES["texto_suave"])
    estilo.configure("Suave.TLabel", foreground=CORES["texto_suave"])
    estilo.configure("TNotebook", background=CORES["fundo"], borderwidth=0, tabmargins=(16, 8, 16, 0),
                     **fundo_sem_borda)
    estilo.configure("TNotebook.Tab", background=CORES["painel"], foreground=CORES["texto_suave"],
                     padding=(16, 7), borderwidth=0, font=(FONTE, 10, "bold"), **fundo_sem_borda)
    estilo.map("TNotebook.Tab", background=[("selected", CORES["borda"])],
               foreground=[("selected", CORES["destaque"])], expand=[("selected", (0, 0, 0, 0))])
    estilo.configure("TButton", background=CORES["borda"], foreground=CORES["texto"], borderwidth=0,
                     padding=(12, 6), font=(FONTE, 10, "bold"))
    estilo.map("TButton", background=[("disabled", CORES["painel"]), ("active", "#475569")],
               foreground=[("disabled", CORES["texto_suave"])])
    for nome, fundo, texto, ativo in (("Destaque", CORES["destaque"], CORES["fundo"], "#0ea5e9"),
                                      ("Verde", CORES["verde"], "#052e16", "#16a34a"),
                                      ("Ambar", CORES["ambar"], "#1c1917", "#d97706")):
        estilo.configure(f"{nome}.TButton", background=fundo, foreground=texto)
        estilo.map(f"{nome}.TButton", background=[("disabled", CORES["painel"]), ("active", ativo)])
    estilo.configure("Pequeno.TButton", padding=(10, 4), font=(FONTE, 9, "bold"))
    estilo.configure("Treeview", background=CORES["painel"], fieldbackground=CORES["painel"],
                     foreground=CORES["texto"], borderwidth=0, rowheight=26, **sem_borda)
    estilo.configure("Treeview.Heading", background=CORES["borda"], foreground=CORES["texto_suave"],
                     borderwidth=0, font=(FONTE, 9, "bold"))
    estilo.map("Treeview", background=[("selected", CORES["borda"])],
               foreground=[("selected", CORES["destaque"])])


class Aplicativo(tk.Tk):
    def __init__(self, sistema: Sistema) -> None:
        super().__init__()
        self.sis = sistema
        self.title("Kit de Suporte TI")
        self.geometry("920x680")
        self.minsize(860, 620)
        self.configure(bg=CORES["fundo"])
        configurar_estilo(self)

        self.respostas: queue.Queue = queue.Queue()
        self.resultados: list[vf.Resultado] = []
        self.info: dict[str, str] = {}
        self.config_rede = cf.Configuracao()

        self._montar()
        self.after(100, self._processar_respostas)
        self.verificar()
        self.carregar_configuracao()

    # ------------------------------------------------------------------ tarefas

    def em_segundo_plano(self, funcao: Callable[[], Any], ao_concluir: Callable[[Any], None]) -> None:
        def tarefa():
            try:
                self.respostas.put((ao_concluir, funcao()))
            except Exception as erro:  # noqa: BLE001 - qualquer falha vira mensagem no registro
                self.respostas.put((self._falhou, erro))
        threading.Thread(target=tarefa, daemon=True).start()

    def _processar_respostas(self) -> None:
        while True:
            try:
                retorno, valor = self.respostas.get_nowait()
            except queue.Empty:
                break
            retorno(valor)
        self.after(100, self._processar_respostas)

    def _falhou(self, erro: Exception) -> None:
        mensagem = "Permissão negada: execute como administrador." if isinstance(erro, PermissionError) \
            else f"Erro: {erro}"
        self.registrar(mensagem, "vermelho")

    def registrar(self, mensagem: str, cor: str = "texto_suave") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {mensagem}\n", cor)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------ montagem

    def _montar(self) -> None:
        topo = ttk.Frame(self, padding=(16, 12, 16, 0))
        topo.pack(fill="x")
        tk.Label(topo, text="🛠  Kit de Suporte TI", font=(FONTE, 15, "bold"), bg=CORES["fundo"],
                 fg=CORES["destaque"]).pack(side="left")
        if self.sis.demonstracao:
            tk.Label(topo, text=" MODO DEMONSTRAÇÃO ", font=(FONTE, 9, "bold"), bg=CORES["ambar"],
                     fg="#1c1917").pack(side="left", padx=12)
        if self.sis.administrador:
            tk.Label(topo, text="● Administrador", font=(FONTE, 10, "bold"), bg=CORES["fundo"],
                     fg=CORES["verde"]).pack(side="right")
        else:
            ttk.Button(topo, text="Reabrir como administrador", style="Ambar.TButton",
                       command=self._reabrir_admin).pack(side="right")
            tk.Label(topo, text="● Usuário comum", font=(FONTE, 10, "bold"), bg=CORES["fundo"],
                     fg=CORES["ambar"]).pack(side="right", padx=10)

        rodape = ttk.Frame(self, padding=(16, 0, 16, 12))
        rodape.pack(side="bottom", fill="x")
        botoes = ttk.Frame(rodape)
        botoes.pack(fill="x", pady=(0, 6))
        ttk.Label(botoes, text="Registro", style="Suave.TLabel").pack(side="left")
        ttk.Button(botoes, text="Reiniciar o PC", style="Pequeno.TButton",
                   command=lambda: self._desligar("reiniciar")).pack(side="right")
        ttk.Button(botoes, text="Sair da sessão", style="Pequeno.TButton",
                   command=lambda: self._desligar("sair")).pack(side="right", padx=8)
        self.log = tk.Text(rodape, height=6, bg=CORES["painel"], fg=CORES["texto_suave"], relief="flat",
                           font=(FONTE_MONO, 9), padx=10, pady=6, state="disabled", highlightthickness=0)
        for nome, cor in CORES.items():
            self.log.tag_configure(nome, foreground=cor)
        self.log.pack(fill="x")

        self.abas = ttk.Notebook(self)
        self.abas.pack(fill="both", expand=True)
        for titulo, montar in (("Segurança", self._aba_seguranca), ("Ajustes", self._aba_ajustes),
                               ("Rede e atalhos", self._aba_rede)):
            aba = ttk.Frame(self.abas, padding=16)
            montar(aba)
            self.abas.add(aba, text=titulo)

    # ------------------------------------------------------------------ Segurança

    def _aba_seguranca(self, aba: ttk.Frame) -> None:
        linha = ttk.Frame(aba)
        linha.pack(fill="x")
        self.lbl_info = ttk.Label(linha, text="", style="Suave.TLabel")
        self.lbl_info.pack(side="left")
        ttk.Button(linha, text="Salvar relatório", command=self.salvar_relatorio).pack(side="right")
        self.btn_verificar = ttk.Button(linha, text="Verificar agora", style="Destaque.TButton",
                                        command=self.verificar)
        self.btn_verificar.pack(side="right", padx=8)
        self.lbl_resumo = tk.Label(aba, font=(FONTE, 13, "bold"), bg=CORES["fundo"], fg=CORES["texto"], anchor="w")
        self.lbl_resumo.pack(fill="x", pady=(12, 8))
        self.quadro_resultados = ttk.Frame(aba)
        self.quadro_resultados.pack(fill="both", expand=True)

    def verificar(self) -> None:
        self.btn_verificar.state(["disabled"])
        self.lbl_resumo.configure(text="Verificando...", fg=CORES["destaque"])

        def tarefa():
            return vf.informacoes_sistema(self.sis), vf.verificar_tudo(self.sis)

        self.em_segundo_plano(tarefa, self._mostrar_resultados)

    def _mostrar_resultados(self, dados) -> None:
        self.info, self.resultados = dados
        self.btn_verificar.state(["!disabled"])
        if self.info:
            self.lbl_info.configure(text="  ·  ".join(f"{k}: {v}" for k, v in self.info.items()
                                                      if k in ("Computador", "Sistema", "Endereço IP")))
        for filho in self.quadro_resultados.winfo_children():
            filho.destroy()
        alertas = sum(r.estado is vf.Estado.ALERTA for r in self.resultados)
        if alertas:
            self.lbl_resumo.configure(text=f"⚠  {alertas} ponto(s) de atenção", fg=CORES["ambar"])
        else:
            self.lbl_resumo.configure(text="✔  Proteções do Windows em ordem", fg=CORES["verde"])
        for r in self.resultados:
            self._cartao(r)
        self.registrar(f"Verificação concluída: {alertas} alerta(s).", "ambar" if alertas else "verde")

    def _cartao(self, r: vf.Resultado) -> None:
        cartao = tk.Frame(self.quadro_resultados, bg=CORES["painel"], padx=14, pady=10)
        cartao.pack(fill="x", pady=3)
        tk.Frame(cartao, bg=COR_ESTADO[r.estado], width=4).pack(side="left", fill="y", padx=(0, 12))
        textos = tk.Frame(cartao, bg=CORES["painel"])
        textos.pack(side="left", fill="x", expand=True)
        tk.Label(textos, text=r.nome, font=(FONTE, 11, "bold"), bg=CORES["painel"], fg=CORES["texto"],
                 anchor="w").pack(fill="x")
        tk.Label(textos, text=r.detalhe, font=(FONTE, 9), bg=CORES["painel"], fg=CORES["texto_suave"],
                 anchor="w").pack(fill="x")
        if r.corrigir:
            botao = ttk.Button(cartao, text="Corrigir", style="Ambar.TButton",
                               command=lambda: self._corrigir(r))
            botao.pack(side="right")
            if not self.sis.administrador:
                botao.state(["disabled"])
        tk.Label(cartao, text=TEXTO_ESTADO[r.estado], font=(FONTE, 10, "bold"), bg=CORES["painel"],
                 fg=COR_ESTADO[r.estado], width=8).pack(side="right")

    def _corrigir(self, r: vf.Resultado) -> None:
        self.registrar(f"Corrigindo: {r.nome}...", "destaque")

        def pronto(_valor) -> None:
            extra = " Reinicie o computador para concluir." if r.exige_reinicio else ""
            self.registrar(f"{r.nome}: correção aplicada.{extra}", "verde")
            self.verificar()

        self.em_segundo_plano(lambda: r.corrigir(self.sis), pronto)

    def salvar_relatorio(self) -> None:
        if not self.resultados:
            return
        nome = f"relatorio-seguranca-{self.info.get('Computador', 'pc')}-{datetime.now():%Y%m%d-%H%M}.txt"
        destino = filedialog.asksaveasfilename(parent=self, title="Salvar relatório", initialfile=nome,
                                               defaultextension=".txt", filetypes=[("Texto", "*.txt")])
        if destino:
            Path(destino).write_text(vf.relatorio(self.resultados, self.info), encoding="utf-8")
            self.registrar(f"Relatório salvo em {destino}", "verde")

    # ------------------------------------------------------------------ Ajustes

    def _aba_ajustes(self, aba: ttk.Frame) -> None:
        ttk.Label(aba, text="Ajustes de conveniência do kit original. Todos podem ser desfeitos.",
                  style="Suave.TLabel").pack(anchor="w", pady=(0, 10))
        self.linhas_ajuste = []
        for ajuste in vf.AJUSTES:
            cartao = tk.Frame(aba, bg=CORES["painel"], padx=14, pady=10)
            cartao.pack(fill="x", pady=3)
            textos = tk.Frame(cartao, bg=CORES["painel"])
            textos.pack(side="left", fill="x", expand=True)
            tk.Label(textos, text=ajuste.nome, font=(FONTE, 11, "bold"), bg=CORES["painel"], fg=CORES["texto"],
                     anchor="w").pack(fill="x")
            admin = "  ·  exige administrador" if ajuste.exige_admin else ""
            tk.Label(textos, text=f"{ajuste.descricao}  Vale {ajuste.efeito}.{admin}", font=(FONTE, 9),
                     bg=CORES["painel"], fg=CORES["texto_suave"], anchor="w", justify="left",
                     wraplength=560).pack(fill="x")
            botao = ttk.Button(cartao, width=9, command=lambda a=ajuste: self._alternar_ajuste(a))
            botao.pack(side="right")
            estado = tk.Label(cartao, font=(FONTE, 10, "bold"), bg=CORES["painel"], width=12)
            estado.pack(side="right")
            self.linhas_ajuste.append((ajuste, estado, botao))
        self._atualizar_ajustes()

    def _atualizar_ajustes(self) -> None:
        for ajuste, estado, botao in self.linhas_ajuste:
            try:
                aplicado = ajuste.aplicado(self.sis)
            except OSError:
                aplicado = False
            estado.configure(text="Aplicado" if aplicado else "Não aplicado",
                             fg=CORES["verde"] if aplicado else CORES["texto_suave"])
            botao.configure(text="Desfazer" if aplicado else "Aplicar",
                            style="TButton" if aplicado else "Destaque.TButton")
            permitido = self.sis.administrador or not ajuste.exige_admin
            botao.state(["!disabled"] if permitido else ["disabled"])

    def _alternar_ajuste(self, ajuste: vf.Ajuste) -> None:
        try:
            if ajuste.aplicado(self.sis):
                ajuste.desfazer(self.sis)
                self.registrar(f"Desfeito: {ajuste.nome}.")
            else:
                ajuste.aplicar(self.sis)
                self.registrar(f"Aplicado: {ajuste.nome} (vale {ajuste.efeito}).", "verde")
        except OSError as erro:
            self._falhou(erro)
        self._atualizar_ajustes()

    # ------------------------------------------------------------------ Rede e atalhos

    def _aba_rede(self, aba: ttk.Frame) -> None:
        linha = ttk.Frame(aba)
        linha.pack(fill="x")
        self.lbl_config = ttk.Label(linha, text="", style="Suave.TLabel")
        self.lbl_config.pack(side="left")
        ttk.Button(linha, text="Recarregar", command=self.carregar_configuracao).pack(side="right")
        ttk.Button(linha, text="Editar configuração", command=self._editar_configuracao).pack(side="right", padx=8)

        ttk.Label(aba, text="Unidades de rede", style="Suave.TLabel").pack(anchor="w", pady=(12, 4))
        self.tbl_unidades = self._tabela(aba, [("letra", "Letra", 60), ("caminho", "Caminho", 280),
                                               ("descricao", "Descrição", 300)], 3)
        ttk.Button(aba, text="Mapear todas", style="Destaque.TButton", command=self._mapear).pack(anchor="e",
                                                                                                 pady=(6, 0))
        ttk.Label(aba, text="Atalhos na área de trabalho", style="Suave.TLabel").pack(anchor="w", pady=(8, 4))
        self.tbl_atalhos = self._tabela(aba, [("nome", "Nome", 200), ("destino", "Destino", 440)], 5)
        self.btn_atalhos = ttk.Button(aba, text="Atualizar área de trabalho", style="Destaque.TButton",
                                      command=self._atualizar_desktop)
        self.btn_atalhos.pack(anchor="e", pady=(6, 0))

    def _tabela(self, pai, colunas, altura) -> ttk.Treeview:
        arvore = ttk.Treeview(pai, columns=[c[0] for c in colunas], show="headings", height=altura,
                              selectmode="none")
        for chave, titulo, largura in colunas:
            arvore.heading(chave, text=titulo, anchor="w")
            arvore.column(chave, width=largura, anchor="w")
        arvore.pack(fill="x")
        return arvore

    def carregar_configuracao(self) -> None:
        caminho = cf.ARQUIVO_PADRAO
        if self.sis.demonstracao and not caminho.exists():
            caminho = cf.ARQUIVO_EXEMPLO
        try:
            self.config_rede = cf.carregar(caminho)
        except cf.ErroConfiguracao as erro:
            self.config_rede = cf.Configuracao()
            self.registrar(f"Configuração com erro:\n{erro}", "vermelho")
        origem = caminho.name if caminho.exists() else f"{caminho.name} ainda não existe (clique em Editar)"
        destino = "área de trabalho pública" if self.config_rede.area_de_trabalho == "publica" \
            else "área de trabalho do usuário"
        self.lbl_config.configure(text=f"Configuração: {origem}  ·  {destino}")
        self.tbl_unidades.delete(*self.tbl_unidades.get_children())
        for u in self.config_rede.unidades:
            self.tbl_unidades.insert("", "end", values=(u.letra, u.caminho, u.descricao))
        self.tbl_atalhos.delete(*self.tbl_atalhos.get_children())
        for a in self.config_rede.atalhos:
            self.tbl_atalhos.insert("", "end", values=(a.nome, a.url or f"Menu Iniciar: {a.menu_iniciar}"))

    def _editar_configuracao(self) -> None:
        caminho = cf.criar_a_partir_do_exemplo()
        self.registrar(f"Abrindo {caminho.name}. Salve o arquivo e clique em Recarregar.")
        if WINDOWS:
            os.startfile(caminho)  # noqa: S606 - abre no editor padrão
        elif not self.sis.demonstracao:
            subprocess.Popen(["xdg-open" if platform.system() == "Linux" else "open", str(caminho)])
        self.carregar_configuracao()

    def _mapear(self) -> None:
        if not self.config_rede.unidades:
            self.registrar("Nenhuma unidade configurada.", "ambar")
            return

        def tarefa():
            return [cf.mapear(self.sis, u) for u in self.config_rede.unidades]

        def pronto(resultados) -> None:
            for ok, mensagem in resultados:
                self.registrar(mensagem, "verde" if ok else "vermelho")

        self.em_segundo_plano(tarefa, pronto)

    def _atualizar_desktop(self) -> None:
        config = self.config_rede
        if config.area_de_trabalho == "publica" and not self.sis.administrador:
            self.registrar("A área de trabalho pública exige administrador.", "ambar")
            return
        destino = cf.pasta_desktop(self.sis, config)
        removidos = cf.remover_antigos(destino, config.remover)
        if removidos:
            self.registrar(f"Removidos: {', '.join(removidos)}")
        for atalho in config.atalhos:
            ok, mensagem = cf.criar_atalho(self.sis, atalho, destino)
            self.registrar(mensagem, "verde" if ok else "ambar")

    # ------------------------------------------------------------------ sistema

    def _desligar(self, acao: str) -> None:
        texto = "reiniciar o computador" if acao == "reiniciar" else "sair da sessão"
        if not messagebox.askyesno("Confirmar", f"Salve seus arquivos antes de continuar.\n\nDeseja {texto}?",
                                   parent=self):
            return
        if acao == "reiniciar":
            comando = ["shutdown", "/r", "/t", "30", "/c", "Reinício solicitado pelo Kit de Suporte TI."]
            self.registrar("O computador vai reiniciar em 30 segundos (para cancelar: shutdown /a).", "ambar")
        else:
            comando = ["shutdown", "/l"]
        self.sis.rodar(comando)

    def _reabrir_admin(self) -> None:
        if reabrir_como_administrador():
            self.destroy()
        else:
            self.registrar("Não foi possível reabrir como administrador.", "vermelho")


def main() -> None:
    if WINDOWS:
        sistema = sistema_real()
    else:
        sistema = sistema_demonstracao(Path(tempfile.mkdtemp(prefix="kit_ti_demo_")))
    Aplicativo(sistema).mainloop()


if __name__ == "__main__":
    main()
