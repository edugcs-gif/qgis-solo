# -*- coding: utf-8 -*-
"""Lógica de agregação (sem dependência do QGIS)."""
import datetime as dt
import re
from collections import defaultdict

SEM_USUARIO = "(sem usuário)"
SEM_VALOR = "(vazio)"
SEM_DATA = "(sem data)"
FORA_DOS_SETORES = "Fora dos setores"

MESES = ["jan", "fev", "mar", "abr", "mai", "jun",
         "jul", "ago", "set", "out", "nov", "dez"]


def parse_texto_data(s):
    """Converte texto em date. Aceita ISO (2026-09-30 14:00:00) e BR (30/09/2026)."""
    s = str(s).strip()
    if not s:
        return None
    iso = s.replace("T", " ")[:19]
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10),
                   ("%d/%m/%Y %H:%M:%S", 19), ("%d/%m/%Y %H:%M", 16), ("%d/%m/%Y", 10)):
        try:
            return dt.datetime.strptime(iso[:n], fmt).date()
        except ValueError:
            pass
    return None


def chave_periodo(d, modo):
    """Retorna (chave_ordenável, rótulo) do período."""
    if modo == "semana":
        inicio = d - dt.timedelta(days=d.weekday())
        fim = inicio + dt.timedelta(days=6)
        ano, sem, _ = d.isocalendar()
        return (inicio.isoformat(),
                "S%02d/%d (%s a %s)" % (sem, ano, inicio.strftime("%d/%m"), fim.strftime("%d/%m")))
    if modo == "mes":
        return ("%04d-%02d" % (d.year, d.month), "%s/%d" % (MESES[d.month - 1], d.year))
    return (d.isoformat(), d.strftime("%d/%m/%Y"))


def por_setor(pares, valores=(), valor_pct=None):
    """
    Contagem setor x valor. pares: iterável de (setores, valor) — os nomes dos setores em que a feição
    cai (vazio = fora de todos; mais de um = contada em cada um) e o texto do "campo do valor".
    valores: as colunas, na ordem do relatório. valor_pct: o valor da coluna de percentual do setor.
    """
    cont = defaultdict(lambda: defaultdict(int))    # setor -> valor -> n
    total = defaultdict(int)                        # setor -> n
    for setores, valor in pares:
        valor = SEM_VALOR if valor in (None, "") else str(valor)
        for s in (setores or [FORA_DOS_SETORES]):
            cont[s][valor] += 1
            total[s] += 1
    # ordem natural: setor 2 antes do 10
    natural = lambda s: [int(t) if i % 2 else t.lower() for i, t in enumerate(re.split(r"(\d+)", s))]
    nomes = sorted((s for s in total if s != FORA_DOS_SETORES), key=natural)
    if FORA_DOS_SETORES in total:
        nomes.append(FORA_DOS_SETORES)
    return {"valores": list(valores), "valor_pct": valor_pct,
            "linhas": [{"setor": s, "valores": [cont[s].get(v, 0) for v in valores], "total": total[s],
                        "n_pct": cont[s].get(valor_pct, 0)} for s in nomes]}


def agregar(registros, modo="dia", camadas=None, ordem_valores=None):
    """
    registros: iterável de (camada, usuario, date|None) ou (camada, usuario, date|None, valor),
    onde valor é o texto do "campo do valor" já como aparece no QGIS (ex.: nome do mapa de valores).
    ordem_valores: ordem preferida das colunas de valor (ex.: a ordem do mapa de valores).
    Retorna dict com resumo por usuário e matriz usuário x período; com valor, também as
    contagens por usuário x valor e por usuário x período x valor.
    """
    camadas = list(camadas or [])
    por_camada = defaultdict(lambda: defaultdict(int))   # user -> camada -> n
    por_periodo = defaultdict(lambda: defaultdict(int))  # user -> chave -> n
    dias = defaultdict(set)                               # user -> {dates}
    primeiro, ultimo = {}, {}
    rotulos = {}
    sem_data = defaultdict(int)
    total = 0
    com_valor = False
    valor_usuario = defaultdict(lambda: defaultdict(int))   # user -> valor -> n
    valor_periodo = defaultdict(lambda: defaultdict(int))   # (user, chave) -> valor -> n
    vistos = []

    for reg in registros:
        camada, user, d = reg[0], reg[1], reg[2]
        user = str(user).strip() if user not in (None, "") else SEM_USUARIO
        if not user:
            user = SEM_USUARIO
        if camada not in camadas:
            camadas.append(camada)
        por_camada[user][camada] += 1
        total += 1
        if len(reg) > 3:
            com_valor = True
            valor = SEM_VALOR if reg[3] in (None, "") else str(reg[3])
            if valor not in vistos:
                vistos.append(valor)
            valor_usuario[user][valor] += 1
            chave = chave_periodo(d, modo)[0] if d is not None else "~"     # "~": sem data por último
            valor_periodo[(user, chave)][valor] += 1
        if d is None:
            sem_data[user] += 1
            continue
        k, r = chave_periodo(d, modo)
        rotulos[k] = r
        por_periodo[user][k] += 1
        dias[user].add(d)
        if user not in primeiro or d < primeiro[user]:
            primeiro[user] = d
        if user not in ultimo or d > ultimo[user]:
            ultimo[user] = d

    usuarios = sorted(por_camada, key=lambda u: (-sum(por_camada[u].values()), u.lower()))
    periodos = sorted(rotulos)

    resumo = []
    for u in usuarios:
        tot = sum(por_camada[u].values())
        nd = len(dias[u])
        com_data = tot - sem_data[u]
        resumo.append({
            "usuario": u,
            "por_camada": [por_camada[u].get(c, 0) for c in camadas],
            "total": tot,
            "dias": nd,
            "media": (com_data / nd) if nd else 0.0,
            "pct": (tot * 100.0 / total) if total else 0.0,
            "primeiro": primeiro.get(u),
            "ultimo": ultimo.get(u),
            "sem_data": sem_data[u],
        })

    matriz = []
    for u in usuarios:
        linha = [por_periodo[u].get(k, 0) for k in periodos]
        matriz.append({"usuario": u, "valores": linha, "total": sum(linha)})
    totais_periodo = [sum(por_periodo[u].get(k, 0) for u in usuarios) for k in periodos]

    todos_dias = set()
    for s in dias.values():
        todos_dias |= s

    extra = {}
    if com_valor:
        ordem = [v for v in (ordem_valores or []) if v in vistos]
        ordem += sorted((v for v in vistos if v not in ordem and v != SEM_VALOR), key=lambda v: v.lower())
        if SEM_VALOR in vistos:
            ordem.append(SEM_VALOR)
        linhas_u = []
        for u in usuarios:
            vals = [valor_usuario[u].get(v, 0) for v in ordem]
            linhas_u.append({"usuario": u, "valores": vals, "total": sum(vals)})
        linhas_p = []
        for u in usuarios:
            for chave in sorted(k for (uu, k) in valor_periodo if uu == u):
                vals = [valor_periodo[(u, chave)].get(v, 0) for v in ordem]
                linhas_p.append({"usuario": u, "periodo": SEM_DATA if chave == "~" else rotulos[chave],
                                 "valores": vals, "total": sum(vals)})
        extra = {
            "valores": ordem,
            "valor_usuario": linhas_u,
            "valor_periodo": linhas_p,
            "totais_valor": [sum(l["valores"][i] for l in linhas_u) for i in range(len(ordem))],
        }

    return dict(extra, **{
        "camadas": camadas,
        "usuarios": usuarios,
        "resumo": resumo,
        "periodos": periodos,
        "rotulos": [rotulos[k] for k in periodos],
        "matriz": matriz,
        "totais_periodo": totais_periodo,
        "total": total,
        "sem_data": sum(sem_data.values()),
        "dias_com_producao": len(todos_dias),
        "media_geral_dia": ((total - sum(sem_data.values())) / len(todos_dias)) if todos_dias else 0.0,
    })
