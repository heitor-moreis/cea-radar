import json
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# ── Palavras-chave que determinam relevância e impacto ──────────────────────
KW_CRITICO  = ["concessão", "decreto", "intervenção", "emergência", "suspensão"]
KW_ALTO     = ["tarifa", "reajuste", "reajuste tarifário", "revisão", "portaria",
               "resolução", "regulamentação", "fiscalização"]
KW_MEDIO    = ["consulta pública", "audiência", "instrução normativa", "edital"]
KW_ENERGIA  = ["energia", "elétrica", "distribuição", "geração", "transmissão",
               "gás", "cigás", "arsepam", "aneel", "cea", "termelétrica"]

def classificar_impacto(texto: str) -> str:
    t = texto.lower()
    if any(k in t for k in KW_CRITICO):
        return "critico"
    if any(k in t for k in KW_ALTO):
        return "alto"
    if any(k in t for k in KW_MEDIO):
        return "medio"
    return "baixo"

def is_relevante(texto: str) -> bool:
    t = texto.lower()
    return any(k in t for k in KW_ENERGIA + KW_CRITICO + KW_ALTO + KW_MEDIO)

def hoje() -> str:
    return datetime.now().strftime("%d/%m/%Y")

# ── Scraper 1: ARSEPAM ───────────────────────────────────────────────────────
def scrape_arsepam() -> list[dict]:
    url = "https://www.arsepam.am.gov.br/legislacao/"
    publicacoes = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # A página lista atos em elementos de lista ou tabela
        # Tenta capturar links e títulos de atos normativos
        itens = soup.find_all(["li", "tr", "article", "div"], limit=80)

        vistos = set()
        for item in itens:
            texto = item.get_text(" ", strip=True)
            if len(texto) < 20 or texto in vistos:
                continue
            vistos.add(texto)

            if not is_relevante(texto):
                continue

            # Tenta extrair link
            link = item.find("a")
            href = link["href"] if link and link.get("href") else url
            if href.startswith("/"):
                href = "https://www.arsepam.am.gov.br" + href

            titulo = (link.get_text(strip=True) if link else texto[:120])
            if len(titulo) < 10:
                titulo = texto[:120]

            publicacoes.append({
                "titulo":  titulo,
                "orgao":   "ARSEPAM",
                "tema":    "Regulação Estadual",
                "data":    hoje(),
                "impacto": classificar_impacto(texto),
                "tags":    [classificar_impacto(texto), "energia"],
                "url":     href,
                "desc":    texto[:400],
            })

        print(f"[ARSEPAM] {len(publicacoes)} itens relevantes encontrados.")
    except Exception as e:
        print(f"[ARSEPAM] Erro: {e}")

    return publicacoes


# ── Scraper 2: DOE-AM ────────────────────────────────────────────────────────
def scrape_doe_am() -> list[dict]:
    url = "https://diario.imprensaoficial.am.gov.br"
    publicacoes = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Detecta número e data da edição atual na página inicial
        edicao_num  = ""
        edicao_data = hoje()

        # Padrão comum: "Edição nº 36.112" ou "Edição 36112"
        texto_pagina = soup.get_text(" ", strip=True)
        match_edicao = re.search(r"[Ee]di[çc][aã]o\s+n[º°.]?\s*([\d\.]+)", texto_pagina)
        if match_edicao:
            edicao_num = match_edicao.group(1)

        # Tenta buscar link para a edição do dia em HTML ou PDF
        link_edicao = url
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(x in href.lower() for x in ["edicao", "diario", "download", "portal"]):
                link_edicao = href if href.startswith("http") else url + "/" + href.lstrip("/")
                break

        # Tenta acessar a edição para extrair atos
        atos_extraidos = []
        try:
            r2 = requests.get(link_edicao, headers=HEADERS, timeout=25)
            soup2 = BeautifulSoup(r2.text, "html.parser")

            # Varre parágrafos e divs em busca de atos
            blocos = soup2.find_all(["p", "div", "section", "article"], limit=200)
            vistos = set()
            for bloco in blocos:
                texto = bloco.get_text(" ", strip=True)
                if len(texto) < 30 or texto in vistos:
                    continue
                vistos.add(texto)
                if is_relevante(texto):
                    atos_extraidos.append(texto)
        except Exception:
            # Se não conseguiu acessar a edição, usa os dados da página inicial
            atos_extraidos = [
                p.get_text(" ", strip=True)
                for p in soup.find_all(["p", "div"], limit=100)
                if len(p.get_text(strip=True)) > 30 and is_relevante(p.get_text(strip=True))
            ]

        # Converte atos encontrados em publicações
        for texto in atos_extraidos[:20]:   # limita a 20 por edição
            impacto = classificar_impacto(texto)
            prefixo = (
                f"DOE-AM Edição nº {edicao_num} — " if edicao_num else "DOE-AM — "
            )
            titulo = prefixo + texto[:100]

            publicacoes.append({
                "titulo":  titulo,
                "orgao":   "DOE-AM",
                "tema":    "Atos Oficiais Estaduais",
                "data":    edicao_data,
                "impacto": impacto,
                "tags":    [impacto, "diario-oficial"],
                "url":     link_edicao,
                "desc":    texto[:500],
            })

        # Sempre registra pelo menos a edição do dia mesmo sem atos filtrados
        if not publicacoes:
            publicacoes.append({
                "titulo":  f"DOE-AM Edição nº {edicao_num} — Nenhum ato relevante identificado hoje",
                "orgao":   "DOE-AM",
                "tema":    "Atos Oficiais Estaduais",
                "data":    edicao_data,
                "impacto": "baixo",
                "tags":    ["baixo", "diario-oficial"],
                "url":     url,
                "desc":    "Edição do dia verificada. Nenhum ato com palavras-chave relevantes foi encontrado nesta edição.",
            })

        print(f"[DOE-AM]  {len(publicacoes)} itens relevantes encontrados.")
    except Exception as e:
        print(f"[DOE-AM] Erro: {e}")

    return publicacoes


# ── Junta tudo e salva ───────────────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"  CEA Radar — Scraping iniciado: {datetime.now():%d/%m/%Y %H:%M}")
    print(f"{'='*50}\n")

    todas = []
    todas.extend(scrape_doe_am())
    todas.extend(scrape_arsepam())

    # Ordena: críticos primeiro, depois por data
    ordem = {"critico": 0, "alto": 1, "medio": 2, "baixo": 3}
    todas.sort(key=lambda p: (ordem.get(p["impacto"], 9), p["data"]))

    # Salva no arquivo JSON
    out = Path("data/publicacoes.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(todas, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ {len(todas)} publicações salvas em {out}")
    print(f"   Concluído: {datetime.now():%d/%m/%Y %H:%M}\n")


if __name__ == "__main__":
    main()
