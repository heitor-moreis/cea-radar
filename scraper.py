import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Headers para simular navegador real ─────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.google.com",
}

# ── Classificação de impacto ─────────────────────────────────────────────────
KW_CRITICO = [
    "concessão", "decreto", "intervenção", "emergência", "suspensão",
    "cancelamento", "cassação", "embargo", "interdição", "autuação",
]
KW_ALTO = [
    "tarifa", "reajuste", "reajuste tarifário", "revisão tarifária",
    "portaria", "resolução", "regulamentação", "fiscalização",
    "chamada pública", "licitação", "contrato",
]
KW_MEDIO = [
    "consulta pública", "audiência pública", "instrução normativa",
    "edital", "nota técnica", "despacho", "parecer",
]
KW_RELEVANCIA = [
    "energia", "elétrica", "distribuição", "geração", "transmissão",
    "gás", "cigás", "arsepam", "aneel", "cea", "termelétrica",
    "combustível", "petróleo", "biocombustível", "hidrelétrica",
    "renovável", "solar", "eólica", "tarifa", "concessão",
    "amazonas", "manaus", "semig", "anp", "ona", "mme",
]

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
    return any(k in t for k in KW_RELEVANCIA + KW_CRITICO + KW_ALTO + KW_MEDIO)

def hoje() -> str:
    return datetime.now().strftime("%d/%m/%Y")

def get(url: str, timeout: int = 25) -> requests.Response | None:
    """Requisição com retry automático e delay entre tentativas."""
    for tentativa in range(3):
        try:
            time.sleep(2 + tentativa * 2)
            r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  ↳ Tentativa {tentativa+1}/3 falhou: {e}")
    return None

def extrair_links_relevantes(soup: BeautifulSoup, base_url: str,
                              orgao: str, tema: str) -> list[dict]:
    """Extrai links relevantes de qualquer página HTML."""
    publicacoes = []
    vistos = set()

    for a in soup.find_all("a", href=True):
        texto = a.get_text(" ", strip=True)
        href  = a["href"].strip()

        if len(texto) < 15 or texto in vistos:
            continue
        if not is_relevante(texto):
            continue
        vistos.add(texto)

        if href.startswith("/"):
            href = base_url.rstrip("/") + href
        elif not href.startswith("http"):
            href = base_url

        publicacoes.append({
            "titulo":  texto[:180],
            "orgao":   orgao,
            "tema":    tema,
            "data":    hoje(),
            "impacto": classificar_impacto(texto),
            "tags":    [classificar_impacto(texto)],
            "url":     href,
            "desc":    texto[:500],
        })

    return publicacoes

def scrape_generico(url: str, orgao: str, tema: str,
                    seletores: list[str] | None = None) -> list[dict]:
    """Scraper genérico reutilizável para qualquer site."""
    publicacoes = []
    r = get(url)
    if not r:
        print(f"  [{orgao}] Inacessível.")
        return registrar_falha(orgao, tema, url)

    soup = BeautifulSoup(r.text, "html.parser")

    # Tenta seletores específicos primeiro, depois fallback geral
    itens = []
    for sel in (seletores or []):
        itens = soup.select(sel)
        if itens:
            break
    if not itens:
        itens = soup.find_all(["a", "li", "article", "div"], limit=120)

    vistos = set()
    for item in itens:
        texto = item.get_text(" ", strip=True)
        if len(texto) < 15 or texto in vistos:
            continue
        if not is_relevante(texto):
            continue
        vistos.add(texto)

        link = item if item.name == "a" else item.find("a")
        href = link["href"] if link and link.get("href") else url
        if href.startswith("/"):
            base = re.match(r"https?://[^/]+", url)
            href = (base.group(0) if base else url) + href
        elif not href.startswith("http"):
            href = url

        titulo = texto[:180]
        publicacoes.append({
            "titulo":  titulo,
            "orgao":   orgao,
            "tema":    tema,
            "data":    hoje(),
            "impacto": classificar_impacto(texto),
            "tags":    [classificar_impacto(texto)],
            "url":     href,
            "desc":    texto[:500],
        })

    print(f"  [{orgao}] {len(publicacoes)} itens relevantes.")
    if not publicacoes:
        return registrar_falha(orgao, tema, url, sem_conteudo=True)
    return publicacoes

def registrar_falha(orgao: str, tema: str, url: str,
                    sem_conteudo: bool = False) -> list[dict]:
    msg = (
        f"{orgao} — Página verificada sem atos relevantes hoje."
        if sem_conteudo else
        f"{orgao} — Falha temporária de acesso. Próxima tentativa amanhã."
    )
    return [{
        "titulo":  msg,
        "orgao":   orgao,
        "tema":    tema,
        "data":    hoje(),
        "impacto": "baixo",
        "tags":    ["baixo"],
        "url":     url,
        "desc":    msg,
    }]


# ════════════════════════════════════════════════════════════════════════════
# SCRAPERS ESPECÍFICOS
# ════════════════════════════════════════════════════════════════════════════

def scrape_dou() -> list[dict]:
    """Diário Oficial da União — filtra atos de órgãos do setor energético."""
    print("\n[DOU] Iniciando...")
    url = "https://www.in.gov.br/consulta"
    publicacoes = []

    # Tenta buscar por palavra-chave na API do DOU
    orgaos_busca = ["ANEEL", "ANP", "MME", "CCEE", "ONS", "ANA", "IBAMA", "EPE"]
    for orgao in orgaos_busca:
        api_url = (
            f"https://www.in.gov.br/consulta/-/buscar/dou"
            f"?q={orgao}&s=todos&exactDate=dia&sortType=0"
        )
        r = get(api_url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        resultados = soup.select(".resultado-item, .search-results li, article")
        for item in resultados:
            texto = item.get_text(" ", strip=True)
            if len(texto) < 20 or not is_relevante(texto):
                continue
            link = item.find("a")
            href = link["href"] if link and link.get("href") else url
            if href.startswith("/"):
                href = "https://www.in.gov.br" + href
            publicacoes.append({
                "titulo":  f"DOU — {orgao}: {texto[:140]}",
                "orgao":   "DOU",
                "tema":    "Publicações Oficiais Federais",
                "data":    hoje(),
                "impacto": classificar_impacto(texto),
                "tags":    [classificar_impacto(texto), "federal"],
                "url":     href,
                "desc":    texto[:500],
            })
        time.sleep(1)

    print(f"  [DOU] {len(publicacoes)} atos relevantes encontrados.")
    if not publicacoes:
        return registrar_falha("DOU", "Publicações Oficiais Federais", url, sem_conteudo=True)
    return publicacoes


def scrape_doe_am() -> list[dict]:
    """Diário Oficial do Estado do Amazonas."""
    print("\n[DOE-AM] Iniciando...")
    url = "https://diario.imprensaoficial.am.gov.br"
    publicacoes = []

    r = get(url)
    if not r:
        return registrar_falha("DOE-AM", "Atos Oficiais Estaduais", url)

    soup = BeautifulSoup(r.text, "html.parser")
    texto_pagina = soup.get_text(" ", strip=True)

    # Detecta número da edição
    edicao_num = ""
    match = re.search(r"[Ee]di[çc][aã]o\s+n[º°.]?\s*([\d\.]+)", texto_pagina)
    if match:
        edicao_num = match.group(1)

    # Tenta acessar edição do dia
    link_edicao = url
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href.lower() for x in ["edicao", "diario", "download", "portal", "hoje"]):
            link_edicao = href if href.startswith("http") else url + "/" + href.lstrip("/")
            break

    r2 = get(link_edicao)
    soup2 = BeautifulSoup(r2.text, "html.parser") if r2 else soup

    vistos = set()
    for bloco in soup2.find_all(["p", "div", "li", "article"], limit=250):
        texto = bloco.get_text(" ", strip=True)
        if len(texto) < 30 or texto in vistos:
            continue
        vistos.add(texto)
        if not is_relevante(texto):
            continue
        prefixo = f"DOE-AM Edição nº {edicao_num} — " if edicao_num else "DOE-AM — "
        publicacoes.append({
            "titulo":  prefixo + texto[:130],
            "orgao":   "DOE-AM",
            "tema":    "Atos Oficiais Estaduais",
            "data":    hoje(),
            "impacto": classificar_impacto(texto),
            "tags":    [classificar_impacto(texto), "estadual"],
            "url":     link_edicao,
            "desc":    texto[:500],
        })

    print(f"  [DOE-AM] {len(publicacoes)} atos relevantes encontrados.")
    if not publicacoes:
        return registrar_falha("DOE-AM", "Atos Oficiais Estaduais", url, sem_conteudo=True)
    return publicacoes[:25]


def scrape_anp() -> list[dict]:
    print("\n[ANP] Iniciando...")
    return scrape_generico(
        url="https://www.gov.br/anp/pt-br/assuntos/legislacao-e-normas",
        orgao="ANP",
        tema="Gás Natural / Petróleo",
        seletores=[".tileItem", ".summary", "article", ".listing-item"],
    )

def scrape_aneel() -> list[dict]:
    print("\n[ANEEL] Iniciando...")
    resultados = scrape_generico(
        url="https://www.gov.br/aneel/pt-br/assuntos/consultas-e-audiencias-publicas",
        orgao="ANEEL",
        tema="Energia Elétrica",
        seletores=[".tileItem", ".summary", "article"],
    )
    # Tenta também notas técnicas
    resultados += scrape_generico(
        url="https://www.gov.br/aneel/pt-br/assuntos/notas-tecnicas",
        orgao="ANEEL",
        tema="Energia Elétrica",
        seletores=[".tileItem", "article"],
    )
    return resultados

def scrape_ana() -> list[dict]:
    print("\n[ANA] Iniciando...")
    return scrape_generico(
        url="https://www.gov.br/ana/pt-br/assuntos/regulacao/outorga",
        orgao="ANA",
        tema="Recursos Hídricos",
        seletores=[".tileItem", "article", ".listing-item"],
    )

def scrape_ccee() -> list[dict]:
    print("\n[CCEE] Iniciando...")
    return scrape_generico(
        url="https://www.ccee.org.br/web/guest/regulacao/regras-e-procedimentos",
        orgao="CCEE",
        tema="Comercialização de Energia",
        seletores=[".portlet-body a", "article", ".asset-abstract"],
    )

def scrape_ons() -> list[dict]:
    print("\n[ONS] Iniciando...")
    return scrape_generico(
        url="https://www.ons.org.br/paginas/sobre-o-ons/normas-e-publicacoes/resolucoes",
        orgao="ONS",
        tema="Operação do Sistema",
        seletores=["table tr", ".listagem a", "article"],
    )

def scrape_tag() -> list[dict]:
    print("\n[TAG] Iniciando...")
    return scrape_generico(
        url="https://www.tag.com.br/noticias",
        orgao="TAG",
        tema="Transporte de Gás",
        seletores=[".news-item", "article", ".post"],
    )

def scrape_petrobras() -> list[dict]:
    print("\n[Petrobras] Iniciando...")
    return scrape_generico(
        url="https://petrobras.com.br/noticias",
        orgao="Petrobras",
        tema="Petróleo e Gás",
        seletores=["article", ".news-card", ".card-noticia"],
    )

def scrape_ame() -> list[dict]:
    print("\n[AmE] Iniciando...")
    return scrape_generico(
        url="https://www.amazonasenergia.com/noticias",
        orgao="AmE",
        tema="Energia Elétrica / AM",
        seletores=["article", ".post", ".news-item"],
    )

def scrape_cigas() -> list[dict]:
    print("\n[Cigás] Iniciando...")
    return scrape_generico(
        url="https://www.cigas-am.com.br",
        orgao="Cigás",
        tema="Gás Natural / AM",
        seletores=["article", ".post", ".news-card", "a"],
    )

def scrape_arsepam() -> list[dict]:
    print("\n[ARSEPAM] Iniciando...")
    return scrape_generico(
        url="https://www.arsepam.am.gov.br/legislacao/",
        orgao="ARSEPAM",
        tema="Regulação Estadual / AM",
        seletores=[".entry-content a", ".legislacao a", "table tr", "li a"],
    )

def scrape_semig() -> list[dict]:
    print("\n[SEMIG] Iniciando...")
    return scrape_generico(
        url="https://www.semig.am.gov.br",
        orgao="SEMIG",
        tema="Política Energética / AM",
        seletores=["article", ".post", "a"],
    )

def scrape_prefeitura_manaus() -> list[dict]:
    print("\n[Prefeitura Manaus] Iniciando...")
    return scrape_generico(
        url="https://www.manaus.am.gov.br/noticia",
        orgao="Prefeitura Manaus",
        tema="Gestão Municipal",
        seletores=["article", ".noticia", ".card"],
    )

def scrape_immu() -> list[dict]:
    print("\n[IMMU] Iniciando...")
    return scrape_generico(
        url="https://immu.manaus.am.gov.br",
        orgao="IMMU",
        tema="Mobilidade Urbana",
        seletores=["article", ".post", "a"],
    )

def scrape_implurb() -> list[dict]:
    print("\n[Implurb] Iniciando...")
    return scrape_generico(
        url="https://implurb.manaus.am.gov.br",
        orgao="Implurb",
        tema="Planejamento Urbano",
        seletores=["article", ".post", "a"],
    )

def scrape_sefaz_am() -> list[dict]:
    print("\n[SEFAZ-AM] Iniciando...")
    return scrape_generico(
        url="https://www.sefaz.am.gov.br/area/legislacao",
        orgao="SEFAZ-AM",
        tema="Tributação / AM",
        seletores=[".legislacao a", "table tr", "li a", "article"],
    )

def scrape_seinfra_am() -> list[dict]:
    print("\n[Seinfra-AM] Iniciando...")
    return scrape_generico(
        url="https://www.seinfra.am.gov.br",
        orgao="Seinfra-AM",
        tema="Infraestrutura / AM",
        seletores=["article", ".post", "a"],
    )


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    inicio = datetime.now()
    print(f"\n{'='*55}")
    print(f"  CEA Radar — Scraping iniciado: {inicio:%d/%m/%Y %H:%M}")
    print(f"{'='*55}")

    # Ordem: diários oficiais primeiro (maior prioridade), depois agências,
    # depois empresas, depois órgãos estaduais/municipais
    scrapers = [
        # Diários Oficiais
        scrape_dou,
        scrape_doe_am,
        # Agências reguladoras federais
        scrape_aneel,
        scrape_anp,
        scrape_ana,
        scrape_ccee,
        scrape_ons,
        # Empresas do setor
        scrape_petrobras,
        scrape_tag,
        scrape_ame,
        scrape_cigas,
        # Órgãos estaduais AM
        scrape_arsepam,
        scrape_semig,
        scrape_sefaz_am,
        scrape_seinfra_am,
        # Municipais
        scrape_prefeitura_manaus,
        scrape_immu,
        scrape_implurb,
    ]

    todas = []
    for fn in scrapers:
        try:
            resultado = fn()
            todas.extend(resultado)
        except Exception as e:
            print(f"  ⚠️  Erro inesperado em {fn.__name__}: {e}")

    # Ordena: críticos primeiro, depois alto, médio, baixo
    ordem = {"critico": 0, "alto": 1, "medio": 2, "baixo": 3}
    todas.sort(key=lambda p: (ordem.get(p["impacto"], 9), p["data"]))

    # Remove duplicatas pelo título
    vistos = set()
    unicas = []
    for p in todas:
        chave = p["titulo"][:80].lower()
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(p)

    fim = datetime.now()
    saida = {
        "ultima_coleta": fim.strftime("%d/%m/%Y %H:%M"),
        "total":         len(unicas),
        "fontes":        len(scrapers),
        "publicacoes":   unicas,
    }

    out = Path("data/publicacoes.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"  ✅ {len(unicas)} publicações únicas salvas.")
    print(f"  🕐 Duração: {(fim - inicio).seconds}s")
    print(f"  📁 Arquivo: {out}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
