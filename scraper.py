import json
import re
import time
from datetime import datetime, timezone, timedelta
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

# ── Fuso horário de Manaus ───────────────────────────────────────────────────
MANAUS_TZ = timezone(timedelta(hours=-4))

def agora_manaus():
    return datetime.now(tz=MANAUS_TZ)

def hoje() -> str:
    return agora_manaus().strftime("%d/%m/%Y")

def hoje_mes_ano() -> str:
    meses = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    now = agora_manaus()
    return f"{meses[now.month-1]}/{now.year}"

# ── Classificação de impacto ─────────────────────────────────────────────────
KW_CRITICO = [
    "concessão", "decreto", "intervenção", "emergência", "suspensão",
    "cancelamento", "cassação", "embargo", "interdição", "autuação",
    "revogação", "penalidade", "multa", "infração",
]
KW_ALTO = [
    "tarifa", "reajuste", "reajuste tarifário", "revisão tarifária",
    "portaria", "resolução", "regulamentação", "fiscalização",
    "chamada pública", "licitação", "contrato", "concessão de uso",
    "autorização", "outorga",
]
KW_MEDIO = [
    "consulta pública", "audiência pública", "instrução normativa",
    "edital", "nota técnica", "despacho", "parecer", "tomada de subsídios",
    "agenda regulatória", "relatório",
]
KW_RELEVANCIA = [
    "energia", "elétrica", "distribuição", "geração", "transmissão",
    "gás", "cigás", "arsepam", "aneel", "cea", "termelétrica",
    "combustível", "petróleo", "biocombustível", "hidrelétrica",
    "renovável", "solar", "eólica", "tarifa", "concessão",
    "amazonas", "manaus", "semig", "anp", "ccee", "ons", "mme",
    "gasoduto", "urucu", "coari", "sistema isolado", "subsistema",
    "despacho", "cde", "lrcap", "pld", "acl", "acr",
]

def extrair_tags(texto: str, impacto: str) -> list[str]:
    t = texto.lower()
    tags = [impacto]
    if any(k in t for k in ["energia", "elétrica", "termelétrica", "hidrelétrica", "geração", "distribuição"]):
        tags.append("energia")
    if any(k in t for k in ["gás", "gasoduto", "cigás", "urucu"]):
        tags.append("gas")
    if any(k in t for k in ["consulta pública", "audiência pública", "tomada de subsídios"]):
        tags.append("consulta")
    if any(k in t for k in ["tarifa", "reajuste", "revisão tarifária"]):
        tags.append("tarifa")
    if any(k in t for k in ["meio ambiente", "licenciamento", "ibama"]):
        tags.append("ambiental")
    return list(dict.fromkeys(tags))

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

def normalizar_url(href: str, base_url: str) -> str:
    if not href:
        return base_url
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        base = re.match(r"https?://[^/]+", base_url)
        return (base.group(0) if base else base_url) + href
    if href.startswith("#") or href == "":
        return base_url
    return base_url.rstrip("/") + "/" + href

def get(url: str, timeout: int = 25) -> requests.Response | None:
    for tentativa in range(3):
        try:
            time.sleep(2 + tentativa * 2)
            r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  ↳ Tentativa {tentativa+1}/3 falhou: {e}")
    return None

def extrair_conteudo_artigo(soup: BeautifulSoup) -> str:
    """Extrai o texto principal de um artigo individual."""
    # Tenta seletores comuns de conteúdo de artigo
    for sel in [
        "article", ".article-body", ".entry-content", ".content-body",
        ".noticia-conteudo", ".post-content", ".materia-conteudo",
        "main p", ".texto", "#conteudo", ".corpo-noticia",
    ]:
        el = soup.select_one(sel)
        if el:
            texto = el.get_text(" ", strip=True)
            if len(texto) > 100:
                return texto[:800]
    # Fallback: pega todos os parágrafos
    paragrafos = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 50]
    return " ".join(paragrafos[:5])[:800]

def scrape_com_artigos(
    url_listagem: str,
    orgao: str,
    tema: str,
    seletores_lista: list[str],
    limite: int = 10,
    seguir_links: bool = True,
) -> list[dict]:
    """
    Scraper de dois níveis:
    1. Acessa a página de listagem e coleta links de artigos relevantes
    2. Entra em cada artigo e extrai título, descrição e URL específica
    """
    publicacoes = []

    r = get(url_listagem)
    if not r:
        print(f"  [{orgao}] Inacessível.")
        return registrar_falha(orgao, tema, url_listagem)

    soup = BeautifulSoup(r.text, "html.parser")

    # Coleta candidatos a artigos na listagem
    candidatos = []
    for sel in seletores_lista:
        itens = soup.select(sel)
        if itens:
            for item in itens:
                link_tag = item if item.name == "a" else item.find("a")
                if not link_tag or not link_tag.get("href"):
                    continue
                href = normalizar_url(link_tag["href"], url_listagem)
                titulo_bruto = link_tag.get_text(" ", strip=True) or item.get_text(" ", strip=True)
                # Filtra por relevância já no título antes de entrar no artigo
                if len(titulo_bruto) > 10 and is_relevante(titulo_bruto):
                    candidatos.append((href, titulo_bruto[:200]))
            if candidatos:
                break

    # Fallback: busca todos os links relevantes da página
    if not candidatos:
        for a in soup.find_all("a", href=True):
            texto = a.get_text(" ", strip=True)
            if len(texto) > 15 and is_relevante(texto):
                href = normalizar_url(a["href"], url_listagem)
                # Ignora links que são a própria página ou âncoras
                if href != url_listagem and "#" not in href.split("?")[0][-5:]:
                    candidatos.append((href, texto[:200]))

    # Remove duplicatas de URL
    vistos_url = set()
    candidatos_unicos = []
    for href, titulo in candidatos:
        if href not in vistos_url:
            vistos_url.add(href)
            candidatos_unicos.append((href, titulo))

    print(f"  [{orgao}] {len(candidatos_unicos)} candidatos encontrados na listagem.")

    # Entra em cada artigo para extrair conteúdo e URL específica
    for href, titulo_listagem in candidatos_unicos[:limite]:
        if not seguir_links:
            # Sem seguir link — usa o título da listagem diretamente
            impacto = classificar_impacto(titulo_listagem)
            publicacoes.append({
                "titulo":  titulo_listagem,
                "orgao":   orgao,
                "tema":    tema,
                "data":    hoje(),
                "impacto": impacto,
                "tags":    extrair_tags(titulo_listagem, impacto),
                "url":     href,
                "desc":    titulo_listagem,
            })
            continue

        # Entra no artigo
        r_art = get(href)
        if not r_art:
            # Se não conseguiu acessar, usa o que tem da listagem
            impacto = classificar_impacto(titulo_listagem)
            publicacoes.append({
                "titulo":  titulo_listagem,
                "orgao":   orgao,
                "tema":    tema,
                "data":    hoje(),
                "impacto": impacto,
                "tags":    extrair_tags(titulo_listagem, impacto),
                "url":     href,
                "desc":    titulo_listagem,
            })
            continue

        soup_art = BeautifulSoup(r_art.text, "html.parser")

        # Tenta pegar o título real do artigo
        titulo_real = titulo_listagem
        for sel_titulo in ["h1", ".article-title", ".entry-title", ".titulo-noticia", ".page-title"]:
            el = soup_art.select_one(sel_titulo)
            if el:
                t = el.get_text(" ", strip=True)
                if len(t) > 10:
                    titulo_real = t[:200]
                    break

        # Extrai conteúdo do artigo
        desc = extrair_conteudo_artigo(soup_art)
        if not desc:
            desc = titulo_real

        # Usa o conteúdo completo para classificar impacto com mais precisão
        texto_completo = titulo_real + " " + desc
        impacto = classificar_impacto(texto_completo)

        # Só salva se ainda for relevante após ver o artigo completo
        if not is_relevante(texto_completo):
            continue

        publicacoes.append({
            "titulo":  titulo_real,
            "orgao":   orgao,
            "tema":    tema,
            "data":    hoje(),
            "impacto": impacto,
            "tags":    extrair_tags(texto_completo, impacto),
            "url":     href,          # ← URL específica do artigo
            "desc":    desc,
        })

        time.sleep(1)  # delay entre artigos

    print(f"  [{orgao}] {len(publicacoes)} publicações relevantes após leitura dos artigos.")
    if not publicacoes:
        return registrar_falha(orgao, tema, url_listagem, sem_conteudo=True)
    return publicacoes

def registrar_falha(orgao: str, tema: str, url: str, sem_conteudo: bool = False) -> list[dict]:
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
    """Diário Oficial da União."""
    print("\n[DOU] Iniciando...")
    url_base = "https://www.in.gov.br"
    publicacoes = []

    orgaos_busca = ["ANEEL", "ANP", "MME", "CCEE", "ONS", "ANA", "IBAMA", "EPE", "ARSEPAM"]
    for orgao in orgaos_busca:
        api_url = (
            f"https://www.in.gov.br/consulta/-/buscar/dou"
            f"?q=%22{orgao}%22&s=todos&exactDate=dia&sortType=0"
        )
        r = get(api_url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        resultados = (
            soup.select(".resultado-item") or
            soup.select("article") or
            soup.select(".resultado") or
            soup.find_all("li", class_=re.compile("resultado|item"))
        )
        for item in resultados:
            texto = item.get_text(" ", strip=True)
            if len(texto) < 20 or not is_relevante(texto):
                continue
            link = item.find("a")
            href = normalizar_url(link["href"] if link and link.get("href") else "", url_base)
            # Para o DOU, o link já é direto para o ato oficial
            impacto = classificar_impacto(texto)
            publicacoes.append({
                "titulo":  f"DOU — {orgao}: {texto[:140]}",
                "orgao":   "DOU",
                "tema":    "Publicações Oficiais Federais",
                "data":    hoje(),
                "impacto": impacto,
                "tags":    extrair_tags(texto, impacto) + ["federal"],
                "url":     href,
                "desc":    texto[:500],
            })
        time.sleep(1)

    print(f"  [DOU] {len(publicacoes)} atos relevantes encontrados.")
    if not publicacoes:
        return registrar_falha("DOU", "Publicações Oficiais Federais",
                               "https://www.in.gov.br/consulta", sem_conteudo=True)
    return publicacoes[:20]


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

    edicao_num = ""
    match = re.search(r"[Ee]di[çc][aã]o\s+n[º°.]?\s*([\d\.]+)", texto_pagina)
    if match:
        edicao_num = match.group(1)

    link_edicao = url
    for a in soup.find_all("a", href=True):
        href_lower = a["href"].lower()
        if any(x in href_lower for x in ["edicao", "diario", "download", "portal", "hoje", "view"]):
            candidate = normalizar_url(a["href"], url)
            if candidate != url:
                link_edicao = candidate
                break

    r2 = get(link_edicao) if link_edicao != url else None
    soup2 = BeautifulSoup(r2.text, "html.parser") if r2 else soup

    vistos = set()
    for bloco in soup2.find_all(["p", "div", "li", "article", "section"], limit=250):
        texto = bloco.get_text(" ", strip=True)
        if len(texto) < 30 or texto in vistos:
            continue
        vistos.add(texto)
        if not is_relevante(texto):
            continue

        link = bloco.find("a")
        href = normalizar_url(link["href"] if link and link.get("href") else "", link_edicao)

        prefixo = f"DOE-AM Edição nº {edicao_num} — " if edicao_num else "DOE-AM — "
        impacto = classificar_impacto(texto)
        publicacoes.append({
            "titulo":  (prefixo + texto[:130]).strip(),
            "orgao":   "DOE-AM",
            "tema":    "Atos Oficiais Estaduais",
            "data":    hoje(),
            "impacto": impacto,
            "tags":    extrair_tags(texto, impacto) + ["estadual"],
            "url":     href if href != link_edicao else link_edicao,
            "desc":    texto[:500],
        })

    print(f"  [DOE-AM] {len(publicacoes)} atos relevantes encontrados.")
    if not publicacoes:
        publicacoes.append({
            "titulo":  f"DOE-AM{' Edição nº ' + edicao_num if edicao_num else ''} — Verificado hoje sem atos relevantes",
            "orgao":   "DOE-AM",
            "tema":    "Atos Oficiais Estaduais",
            "data":    hoje(),
            "impacto": "baixo",
            "tags":    ["baixo", "estadual"],
            "url":     link_edicao,
            "desc":    "Edição do dia verificada. Nenhum ato relevante encontrado.",
        })
    return publicacoes[:25]


def scrape_aneel() -> list[dict]:
    """ANEEL — Notícias e consultas públicas com links diretos."""
    print("\n[ANEEL] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.gov.br/aneel/pt-br/assuntos/noticias",
        orgao="ANEEL",
        tema="Energia Elétrica",
        seletores_lista=[".tileItem a", "article a", "h2 a", "h3 a", ".summary a"],
        limite=8,
    )


def scrape_anp() -> list[dict]:
    """ANP — Notícias e comunicados com links diretos."""
    print("\n[ANP] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.gov.br/anp/pt-br/canais_atendimento/imprensa/noticias-comunicados",
        orgao="ANP",
        tema="Gás Natural / Petróleo",
        seletores_lista=[".tileItem a", "article a", "h2 a", "h3 a", ".summary a"],
        limite=8,
    )


def scrape_ana() -> list[dict]:
    """ANA — Notícias com links diretos."""
    print("\n[ANA] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.gov.br/ana/pt-br/assuntos/noticias-e-eventos/noticias",
        orgao="ANA",
        tema="Recursos Hídricos",
        seletores_lista=[".tileItem a", "article a", "h2 a", "h3 a"],
        limite=6,
    )


def scrape_ccee() -> list[dict]:
    """CCEE — Notícias com links diretos."""
    print("\n[CCEE] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.ccee.org.br/busca-ccee?q=&dtIni=&dtFim=&structure=ccee-noticias&ordenacao=Mais%20recentes",
        orgao="CCEE",
        tema="Comercialização de Energia",
        seletores_lista=[".asset-title a", "h2 a", "h3 a", "article a", ".portlet-body a"],
        limite=8,
    )


def scrape_ons() -> list[dict]:
    """ONS — Notícias com links diretos."""
    print("\n[ONS] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.ons.org.br/paginas/imprensa/noticias",
        orgao="ONS",
        tema="Operação do Sistema",
        seletores_lista=["article a", "h2 a", "h3 a", ".listagem a", "li a"],
        limite=6,
    )


def scrape_tag() -> list[dict]:
    """TAG — Notícias com links diretos."""
    print("\n[TAG] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.tag.com.br/noticias",
        orgao="TAG",
        tema="Transporte de Gás",
        seletores_lista=["article a", "h2 a", "h3 a", ".news-item a", ".card a"],
        limite=5,
    )


def scrape_petrobras() -> list[dict]:
    """Petrobras — Notícias com links diretos."""
    print("\n[Petrobras] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://petrobras.com.br/noticias",
        orgao="Petrobras",
        tema="Petróleo e Gás",
        seletores_lista=["article a", "h2 a", "h3 a", ".news-card a", ".card a"],
        limite=5,
    )


def scrape_ame() -> list[dict]:
    """Amazonas Energia — Notícias com links diretos."""
    print("\n[AmE] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://website.ambarenergia-am.com.br/informacoes/destaques/",
        orgao="AmE",
        tema="Energia Elétrica / AM",
        seletores_lista=["article a", "h2 a", "h3 a", ".post a", ".card a"],
        limite=5,
    )


def scrape_cigas() -> list[dict]:
    """Cigás — Notícias com links diretos."""
    print("\n[Cigás] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.cigas-am.com.br",
        orgao="Cigás",
        tema="Gás Natural / AM",
        seletores_lista=["article a", "h2 a", "h3 a", ".noticia a", ".post a"],
        limite=5,
    )


def scrape_arsepam() -> list[dict]:
    """ARSEPAM — Notícias e legislação com links diretos."""
    print("\n[ARSEPAM] Iniciando...")
    time.sleep(3)
    return scrape_com_artigos(
        url_listagem="https://www.arsepam.am.gov.br/category/noticias/",
        orgao="ARSEPAM",
        tema="Regulação Estadual / AM",
        seletores_lista=["article a", "h2 a", "h3 a", ".entry-title a", "li a"],
        limite=8,
    )


def scrape_semig() -> list[dict]:
    """SEMIG — Notícias com links diretos."""
    print("\n[SEMIG] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.semig.am.gov.br/category/noticias/",
        orgao="SEMIG",
        tema="Política Energética / AM",
        seletores_lista=["article a", "h2 a", "h3 a", ".post a"],
        limite=5,
    )


def scrape_prefeitura_manaus() -> list[dict]:
    """Prefeitura de Manaus — Notícias com links diretos."""
    print("\n[Prefeitura Manaus] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.manaus.am.gov.br/noticias/",
        orgao="Prefeitura Manaus",
        tema="Gestão Municipal",
        seletores_lista=["article a", "h2 a", "h3 a", ".noticia a", ".card a"],
        limite=4,
    )


def scrape_immu() -> list[dict]:
    """IMMU — Notícias com links diretos."""
    print("\n[IMMU] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.manaus.am.gov.br/immu/noticias/",
        orgao="IMMU",
        tema="Mobilidade Urbana",
        seletores_lista=["article a", "h2 a", "h3 a", ".post a"],
        limite=3,
    )


def scrape_implurb() -> list[dict]:
    """Implurb — Notícias com links diretos."""
    print("\n[Implurb] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.manaus.am.gov.br/implurb/noticias/",
        orgao="Implurb",
        tema="Planejamento Urbano",
        seletores_lista=["article a", "h2 a", "h3 a", ".post a"],
        limite=3,
    )


def scrape_sefaz_am() -> list[dict]:
    """SEFAZ-AM — Notícias com links diretos."""
    print("\n[SEFAZ-AM] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.sefaz.am.gov.br/noticias",
        orgao="SEFAZ-AM",
        tema="Tributação / AM",
        seletores_lista=["article a", "h2 a", "h3 a", "li a", "table tr td a"],
        limite=4,
    )


def scrape_seinfra_am() -> list[dict]:
    """Seinfra-AM — Notícias com links diretos."""
    print("\n[Seinfra-AM] Iniciando...")
    return scrape_com_artigos(
        url_listagem="https://www.seinfra.am.gov.br/category/noticias/",
        orgao="Seinfra-AM",
        tema="Infraestrutura / AM",
        seletores_lista=["article a", "h2 a", "h3 a", ".post a"],
        limite=3,
    )


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    inicio = agora_manaus()
    print(f"\n{'='*55}")
    print(f"  CEA Radar — Scraping iniciado: {inicio:%d/%m/%Y %H:%M}")
    print(f"{'='*55}")

    scrapers = [
        scrape_dou,
        scrape_doe_am,
        scrape_aneel,
        scrape_anp,
        scrape_ana,
        scrape_ccee,
        scrape_ons,
        scrape_petrobras,
        scrape_tag,
        scrape_ame,
        scrape_cigas,
        scrape_arsepam,
        scrape_semig,
        scrape_sefaz_am,
        scrape_seinfra_am,
        scrape_prefeitura_manaus,
        scrape_immu,
        scrape_implurb,
    ]

    todas = []
    falhas = []
    for fn in scrapers:
        try:
            resultado = fn()
            if resultado:
                todas.extend(resultado)
        except Exception as e:
            nome = fn.__name__.replace("scrape_", "").upper()
            print(f"  ⚠️  Erro inesperado em {fn.__name__}: {e}")
            falhas.append(nome)

    # Ordena por impacto
    ordem = {"critico": 0, "alto": 1, "medio": 2, "baixo": 3}
    todas.sort(key=lambda p: (ordem.get(p["impacto"], 9), p["data"]))

    # Remove duplicatas por URL e por título
    vistos_url   = set()
    vistos_titulo = set()
    unicas = []
    for p in todas:
        chave_url    = p["url"].strip().lower()
        chave_titulo = p["titulo"][:80].lower().strip()
        if chave_url not in vistos_url and chave_titulo not in vistos_titulo:
            vistos_url.add(chave_url)
            vistos_titulo.add(chave_titulo)
            unicas.append(p)

    # Remove entradas de falha se houver dados reais do mesmo órgão
    orgaos_com_dados = {
        p["orgao"] for p in unicas
        if "falha" not in p["titulo"].lower() and "verificado" not in p["titulo"].lower()
    }
    unicas_filtradas = [
        p for p in unicas
        if not (
            ("falha" in p["titulo"].lower() or "verificado" in p["titulo"].lower())
            and p["orgao"] in orgaos_com_dados
        )
    ]

    fim = agora_manaus()

    total     = len(unicas_filtradas)
    criticas  = sum(1 for p in unicas_filtradas if p["impacto"] == "critico")
    altas     = sum(1 for p in unicas_filtradas if p["impacto"] == "alto")
    medias    = sum(1 for p in unicas_filtradas if p["impacto"] == "medio")
    baixas    = sum(1 for p in unicas_filtradas if p["impacto"] == "baixo")
    consultas = sum(1 for p in unicas_filtradas if "consulta" in p["tags"])

    por_orgao = {}
    for p in unicas_filtradas:
        por_orgao[p["orgao"]] = por_orgao.get(p["orgao"], 0) + 1

    saida = {
        "ultima_coleta":    fim.strftime("%d/%m/%Y %H:%M"),
        "mes_ano":          hoje_mes_ano(),
        "total":            total,
        "fontes":           len(scrapers),
        "fontes_com_falha": falhas,
        "stats": {
            "critico":           criticas,
            "alto":              altas,
            "medio":             medias,
            "baixo":             baixas,
            "consultas_abertas": consultas,
        },
        "por_orgao":    dict(sorted(por_orgao.items(), key=lambda x: x[1], reverse=True)),
        "publicacoes":  unicas_filtradas,
    }

    out = Path("data/publicacoes.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"  ✅ {total} publicações únicas salvas.")
    print(f"  ⚠️  Críticas: {criticas} | 🔶 Altas: {altas} | 🟡 Médias: {medias}")
    print(f"  💬 Consultas abertas: {consultas}")
    if falhas:
        print(f"  ❌ Fontes com falha: {', '.join(falhas)}")
    print(f"  🕐 Duração: {(fim - inicio).seconds}s")
    print(f"  📁 Arquivo: {out}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
