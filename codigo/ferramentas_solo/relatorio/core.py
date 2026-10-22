# -*- coding: utf-8 -*-
"""Lógica de agregação (sem dependência do QGIS)."""
import datetime as dt
from collections import defaultdict

SEM_USUARIO = "(sem usuário)"

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


def agregar(registros, modo="dia", camadas=None):
    """
    registros: iterável de (camada, usuario, date|None)
    Retorna dict com resumo por usuário e matriz usuário x período.
    """
    camadas = list(camadas or [])
    por_camada = defaultdict(lambda: defaultdict(int))   # user -> camada -> n
    por_periodo = defaultdict(lambda: defaultdict(int))  # user -> chave -> n
    dias = defaultdict(set)                               # user -> {dates}
    primeiro, ultimo = {}, {}
    rotulos = {}
    sem_data = defaultdict(int)
    total = 0

    for camada, user, d in registros:
        user = str(user).strip() if user not in (None, "") else SEM_USUARIO
        if not user:
            user = SEM_USUARIO
        if camada not in camadas:
            camadas.append(camada)
        por_camada[user][camada] += 1
        total += 1
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

    return {
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
    }
