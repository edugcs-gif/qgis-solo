# -*- coding: utf-8 -*-
"""
Historico do Desconto de Beiral: guarda a geometria ORIGINAL de cada edificacao
descontada, para o botao "Restaurar original" poder voltar exatamente como era.

Fica num arquivo SQLite na pasta de configuracoes do QGIS deste computador
(sobrevive a fechar o QGIS). Se o registro nao existir (outro computador,
edificacao editada depois), a restauracao usa edificacao + beiral.
"""

import hashlib
import os
import sqlite3
import time

from qgis.core import QgsApplication


def _arquivo():
    pasta = QgsApplication.qgisSettingsDirPath()
    return os.path.join(pasta, "desconto_beiral_historico.sqlite")


def chave_camada(camada):
    """Identificador estavel da camada (sem expor senha de conexao)."""
    return hashlib.sha1(camada.source().encode("utf-8")).hexdigest()


def _conectar():
    con = sqlite3.connect(_arquivo(), timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS historico (
                       camada TEXT NOT NULL,
                       fid    INTEGER NOT NULL,
                       wkt_original TEXT NOT NULL,
                       wkt_descontada TEXT NOT NULL,
                       quando REAL NOT NULL,
                       PRIMARY KEY (camada, fid))""")
    return con


def salvar(chave, registros):
    """registros: lista de (fid, wkt_original, wkt_descontada)."""
    if not registros:
        return
    con = _conectar()
    try:
        agora = time.time()
        con.executemany("INSERT OR REPLACE INTO historico VALUES (?,?,?,?,?)",
                        [(chave, int(f), o, d, agora) for f, o, d in registros])
        con.commit()
    finally:
        con.close()


def buscar(chave, fid):
    """Devolve (wkt_original, wkt_descontada) ou None."""
    if not os.path.exists(_arquivo()):
        return None
    con = _conectar()
    try:
        linha = con.execute("SELECT wkt_original, wkt_descontada FROM historico "
                            "WHERE camada=? AND fid=?", (chave, int(fid))).fetchone()
        return linha
    finally:
        con.close()
