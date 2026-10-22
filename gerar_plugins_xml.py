# -*- coding: utf-8 -*-
"""Gera os zips e o plugins.xml do repositório de complementos QGIS da Solo.

Uso:  python gerar_plugins_xml.py            (na pasta do repositório)
      python gerar_plugins_xml.py PASTA URL  (pasta e endereço base explícitos)

O que ele faz:
1. Para cada complemento em codigo/<pasta>/ (com metadata.txt), gera o zip
   <pasta>_<versão>.zip na raiz e apaga os zips de versões antigas dele.
2. Lê o metadata.txt de dentro de cada zip da raiz e escreve o plugins.xml
   (se houver mais de uma versão do mesmo complemento, só a maior entra).

Endereço base dos downloads (o que vai no QGIS):
- dentro de um clone do GitHub: https://raw.githubusercontent.com/<dono>/<repo>/<ramo>/
  (descoberto sozinho pelo .git da pasta);
- fora do git (ex.: pasta de rede): file://... da própria pasta.
"""
import configparser
import datetime
import os
import pathlib
import re
import sys
import zipfile
from xml.sax.saxutils import escape, quoteattr

PASTA = pathlib.Path(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))))

CAMPOS = [  # (elemento do xml, chave do metadata.txt)
    ("description", "description"), ("about", "about"), ("changelog", "changelog"),
    ("qgis_minimum_version", "qgisMinimumVersion"), ("qgis_maximum_version", "qgisMaximumVersion"),
    ("homepage", "homepage"), ("author_name", "author"), ("author_email", "email"),
    ("tracker", "tracker"), ("repository", "repository"), ("tags", "tags"),
    ("category", "category"), ("experimental", "experimental"), ("deprecated", "deprecated"),
    ("plugin_dependencies", "plugin_dependencies"),
]
IGNORAR = {"__pycache__", ".git", ".idea", ".vscode"}


def url_github(pasta):
    """https://raw.githubusercontent.com/<dono>/<repo>/<ramo>/ se a pasta for um clone do GitHub."""
    cfg, head = pasta / ".git" / "config", pasta / ".git" / "HEAD"
    if not cfg.is_file() or not head.is_file():
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfg.read_text(encoding="utf-8"), re.S)
    if not m:
        return None
    m2 = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?/?$", m.group(1))
    if not m2:
        return None
    ramo = re.search(r"refs/heads/(\S+)", head.read_text(encoding="utf-8"))
    return "https://raw.githubusercontent.com/%s/%s/%s/" % (m2.group(1), m2.group(2),
                                                          ramo.group(1) if ramo else "main")


if len(sys.argv) > 2:
    URL_BASE = sys.argv[2].rstrip("/") + "/"
else:
    URL_BASE = url_github(PASTA) or PASTA.as_uri().rstrip("/") + "/"


def versao_tupla(v):
    return tuple(int(n) for n in re.findall(r"\d+", v or "0"))


def ler_cfg(texto):
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read_string(texto)
    return dict(cp["general"])


def empacotar():
    """codigo/<pasta>/ -> <pasta>_<versão>.zip na raiz (o zip contém a pasta <pasta>/)."""
    codigo = PASTA / "codigo"
    if not codigo.is_dir():
        return
    for plugin in sorted(p for p in codigo.iterdir() if (p / "metadata.txt").is_file()):
        versao = ler_cfg((plugin / "metadata.txt").read_text(encoding="utf-8-sig")).get("version", "0")
        destino = PASTA / ("%s_%s.zip" % (plugin.name, versao))
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for raiz, pastas, arquivos in os.walk(plugin):
                pastas[:] = sorted(d for d in pastas if d not in IGNORAR and not d.startswith("."))
                for nome in sorted(arquivos):
                    if nome.endswith((".pyc", ".pyo")) or nome.startswith("."):
                        continue
                    caminho = pathlib.Path(raiz) / nome
                    z.write(caminho, caminho.relative_to(codigo).as_posix())
        print("ZIP %-28s %-8s -> %s" % (plugin.name, versao, destino.name))
        for velho in PASTA.glob(plugin.name + "_*.zip"):
            if velho != destino and re.fullmatch(re.escape(plugin.name) + r"_[\d.]+\.zip", velho.name):
                velho.unlink()
                print("    apagado zip antigo:", velho.name)


def ler_metadata(caminho_zip):
    with zipfile.ZipFile(caminho_zip) as z:
        candidatos = [n for n in z.namelist() if re.fullmatch(r"[^/]+/metadata\.txt", n)]
        if len(candidatos) != 1:
            raise ValueError("o zip precisa ter uma pasta só com metadata.txt dentro")
        return candidatos[0].split("/")[0], ler_cfg(z.read(candidatos[0]).decode("utf-8-sig"))


def main():
    empacotar()
    melhores = {}
    for zp in sorted(PASTA.glob("*.zip")):
        try:
            pasta_plugin, meta = ler_metadata(zp)
        except Exception as exc:
            print("IGNORADO %s: %s" % (zp.name, exc))
            continue
        atual = melhores.get(pasta_plugin)
        if atual is None or versao_tupla(meta.get("version")) > versao_tupla(atual[1].get("version")):
            melhores[pasta_plugin] = (zp, meta)

    linhas = ['<?xml version="1.0" encoding="UTF-8"?>', "<plugins>"]
    for pasta_plugin, (zp, meta) in sorted(melhores.items()):
        versao = meta.get("version", "0")
        data = datetime.datetime.fromtimestamp(zp.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
        linhas.append('  <pyqgis_plugin name=%s version=%s>' % (quoteattr(meta.get("name", pasta_plugin)),
                                                               quoteattr(versao)))
        linhas.append("    <version>%s</version>" % escape(versao))
        for elem, chave in CAMPOS:
            valor = meta.get(chave, "")
            if elem in ("qgis_minimum_version", "qgis_maximum_version") and valor and valor.count(".") == 1:
                valor += ".0"
            linhas.append("    <%s><![CDATA[%s]]></%s>" % (elem, valor.replace("]]>", "]] >"), elem))
        # O QGIS tira o nome interno do complemento do file_name (até o 1º ponto):
        # tem que ser exatamente o nome da pasta, senão a instalação falha.
        linhas.append("    <file_name>%s.zip</file_name>" % escape(pasta_plugin))
        linhas.append("    <download_url>%s</download_url>" % escape(URL_BASE + zp.name.replace(" ", "%20")))
        linhas.append("    <uploaded_by>%s</uploaded_by>" % escape(meta.get("author", "")))
        linhas.append("    <create_date>%s</create_date>" % data)
        linhas.append("    <update_date>%s</update_date>" % data)
        linhas.append("  </pyqgis_plugin>")
        print("OK  %-28s %-8s <- %s" % (pasta_plugin, versao, zp.name))
    linhas.append("</plugins>")
    (PASTA / "plugins.xml").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print("\nplugins.xml gravado com %d complemento(s)." % len(melhores))
    print("URL para colocar no QGIS:\n  %splugins.xml" % URL_BASE)


if __name__ == "__main__":
    main()
