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

# ── Mapeamento de palavras-chave para tags (para uso no index.html) ─────────
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
    return list(dict.fromkeys(tags))  # remove duplicatas mantendo ordem

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

def hoje_mes_ano() -> str:
    """Retorna mês/ano para exibição no dashboard."""
    meses = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    now = datetime.now()
    return f"{meses[now.month-1]}/{now.year}"

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

def normalizar_url(href: str, base_url: str) -> str:
    """Normaliza URLs relativas para absolutas."""
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

def scrape_generico(url: str, orgao: str, tema: str,
                    seletores: list[str] | None = None,
                    limite: int = 15) -> list[dict]:
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
        itens = soup.find_all(["a", "li", "article", "div"], limit=150)

    vistos = set()
    for item in itens:
        texto = item.get_text(" ", strip=True)
        if len(texto) < 15 or texto in vistos:
            continue
        if not is_relevante(texto):
            continue
        vistos.add(texto)

        # Extrai o melhor link disponível
        link_tag = item if item.name == "a" else item.find("a")
        href = normalizar_url(link_tag["href"] if link_tag and link_tag.get("href") else "", url)

        impacto = classificar_impacto(texto)
        publicacoes.append({
            "titulo":  texto[:180],
            "orgao":   orgao,
            "tema":    tema,
            "data":    hoje(),
            "impacto": impacto,
            "tags":    extrair_tags(texto, impacto),
            "url":     href,
            "desc":    texto[:500],
        })
        if len(publicacoes) >= limite:
            break

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
    """Diário Oficial da União — busca por órgãos do setor energético."""
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

        # Tenta múltiplos seletores de resultado
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

    # Detecta número da edição
    edicao_num = ""
    match = re.search(r"[Ee]di[çc][aã]o\s+n[º°.]?\s*([\d\.]+)", texto_pagina)
    if match:
        edicao_num = match.group(1)

    # Tenta encontrar link para a edição do dia
    link_edicao = url
    for a in soup.find_all("a", href=True):
        href_lower = a["href"].lower()
        if any(x in href_lower for x in ["edicao", "diario", "download", "portal", "hoje", "view"]):
            candidate = normalizar_url(a["href"], url)
            if candidate != url:
                link_edicao = candidate
                break

    # Tenta acessar a edição para extrair atos
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

        # Tenta extrair link do bloco
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
            "titulo":  f"DOE-AM{' Edição nº ' + edicao_num if edicao_num else ''} — Verificado hoje sem atos relevantes identificados",
            "orgao":   "DOE-AM",
            "tema":    "Atos Oficiais Estaduais",
            "data":    hoje(),
            "impacto": "baixo",
            "tags":    ["baixo", "estadual"],
            "url":     link_edicao,
            "desc":    "Edição do dia verificada. Nenhum ato com palavras-chave relevantes foi encontrado nesta edição.",
        })
    return publicacoes[:25]


def scrape_aneel() -> list[dict]:
    """ANEEL — Consultas públicas e notas técnicas."""
    print("\n[ANEEL] Iniciando...")
    resultados = []

    # URL correta para consultas e audiências públicas
    resultados += scrape_generico(
        url="https://www.gov.br/aneel/pt-br/acesso-a-informacao/participacao-social/consultas-publicas",
        orgao="ANEEL",
        tema="Energia Elétrica",
        seletores=[".tileItem", ".summary", "article", "li.tileItem", ".tile-title"],
        limite=10,
    )
    # Notas técnicas
    resultados += scrape_generico(
        url="https://www.gov.br/aneel/pt-br/assuntos/notas-tecnicas",
        orgao="ANEEL",
        tema="Energia Elétrica",
        seletores=[".tileItem", "article", "li.tileItem"],
        limite=5,
    )
    # Notícias recentes (backup)
    if len(resultados) < 3:
        resultados += scrape_generico(
            url="https://www.gov.br/aneel/pt-br/assuntos/noticias",
            orgao="ANEEL",
            tema="Energia Elétrica",
            seletores=["article", ".tileItem", ".summary"],
            limite=5,
        )
    return resultados


def scrape_anp() -> list[dict]:
    """ANP — Legislação, normas e chamadas públicas."""
    print("\n[ANP] Iniciando...")
    resultados = scrape_generico(
        url="https://www.gov.br/anp/pt-br/assuntos/legislacao-e-normas/normas",
        orgao="ANP",
        tema="Gás Natural / Petróleo",
        seletores=[".tileItem", ".summary", "article", "li.tileItem"],
        limite=10,
    )
    if len(resultados) < 3:
        resultados += scrape_generico(
            url="https://www.gov.br/anp/pt-br/assuntos/capacidade-de-escoamento",
            orgao="ANP",
            tema="Gás Natural / Petróleo — Capacidade Escoamento",
            seletores=[".tileItem", "article", "li"],
            limite=5,
        )
    return resultados


def scrape_ana() -> list[dict]:
    """ANA — Outorgas e regulação de recursos hídricos."""
    print("\n[ANA] Iniciando...")
    return scrape_generico(
        url="https://www.gov.br/ana/pt-br/assuntos/regulacao/outorga",
        orgao="ANA",
        tema="Recursos Hídricos",
        seletores=[".tileItem", "article", ".listing-item", "li.tileItem"],
        limite=8,
    )


def scrape_ccee() -> list[dict]:
    """CCEE — Regras e procedimentos de comercialização."""
    print("\n[CCEE] Iniciando...")
    resultados = scrape_generico(
        url="https://www.ccee.org.br/web/guest/regulacao/regras-e-procedimentos",
        orgao="CCEE",
        tema="Comercialização de Energia",
        seletores=[".portlet-body a", "article", ".asset-abstract", ".journal-content-article a"],
        limite=8,
    )
    if len(resultados) < 3:
        resultados += scrape_generico(
            url="https://www.ccee.org.br/web/guest/publicacoes-e-servicos/publicacoes/noticias",
            orgao="CCEE",
            tema="Comercialização de Energia",
            seletores=["article", ".portlet-body a"],
            limite=5,
        )
    return resultados


def scrape_ons() -> list[dict]:
    """ONS — Resoluções e publicações sobre operação do sistema."""
    print("\n[ONS] Iniciando...")
    resultados = scrape_generico(
        url="https://www.ons.org.br/paginas/sobre-o-ons/normas-e-publicacoes/resolucoes",
        orgao="ONS",
        tema="Operação do Sistema",
        seletores=["table tr", ".listagem a", "article", "li a"],
        limite=8,
    )
    if len(resultados) < 3:
        resultados += scrape_generico(
            url="https://www.ons.org.br/paginas/sobre-o-ons/normas-e-publicacoes/publicacoes",
            orgao="ONS",
            tema="Operação do Sistema",
            seletores=["article", "li a", "table tr"],
            limite=5,
        )
    return resultados


def scrape_tag() -> list[dict]:
    """TAG — Transportadora Associada de Gás."""
    print("\n[TAG] Iniciando...")
    return scrape_generico(
        url="https://www.tag.com.br/noticias",
        orgao="TAG",
        tema="Transporte de Gás",
        seletores=[".news-item", "article", ".post", ".card"],
        limite=6,
    )


def scrape_petrobras() -> list[dict]:
    """Petrobras — Notícias e comunicados relevantes."""
    print("\n[Petrobras] Iniciando...")
    return scrape_generico(
        url="https://petrobras.com.br/noticias",
        orgao="Petrobras",
        tema="Petróleo e Gás",
        seletores=["article", ".news-card", ".card-noticia", ".card"],
        limite=6,
    )


def scrape_ame() -> list[dict]:
    """Amazonas Energia — Distribuidora local."""
    print("\n[AmE] Iniciando...")
    # URL principal atualizada
    resultados = scrape_generico(
        url="https://www.amazonasenergia.com/noticias",
        orgao="AmE",
        tema="Energia Elétrica / AM",
        seletores=["article", ".post", ".news-item", ".card"],
        limite=6,
    )
    if not resultados or (len(resultados) == 1 and "falha" in resultados[0]["titulo"].lower()):
        resultados = scrape_generico(
            url="https://www.amazonasenergia.com",
            orgao="AmE",
            tema="Energia Elétrica / AM",
            seletores=["article", ".post", "a"],
            limite=6,
        )
    return resultados


def scrape_cigas() -> list[dict]:
    """Cigás — Companhia de Gás do Amazonas."""
    print("\n[Cigás] Iniciando...")
    return scrape_generico(
        url="https://www.cigas-am.com.br",
        orgao="Cigás",
        tema="Gás Natural / AM",
        seletores=["article", ".post", ".news-card", ".noticia", "a"],
        limite=6,
    )


def scrape_arsepam() -> list[dict]:
    """ARSEPAM — Legislação e regulação estadual AM."""
    print("\n[ARSEPAM] Iniciando...")
    import time as _time
    _time.sleep(3)  # delay extra por restrições do site
    return scrape_generico(
        url="https://www.arsepam.am.gov.br/legislacao/",
        orgao="ARSEPAM",
        tema="Regulação Estadual / AM",
        seletores=[".entry-content a", ".legislacao a", "table tr td a", "li a", "article a"],
        limite=10,
    )


def scrape_semig() -> list[dict]:
    """SEMIG — Secretaria de Energia, Mineração e Gás do AM."""
    print("\n[SEMIG] Iniciando...")
    return scrape_generico(
        url="https://www.semig.am.gov.br",
        orgao="SEMIG",
        tema="Política Energética / AM",
        seletores=["article", ".post", ".noticia", "a"],
        limite=6,
    )


def scrape_prefeitura_manaus() -> list[dict]:
    """Prefeitura de Manaus — Notícias relevantes."""
    print("\n[Prefeitura Manaus] Iniciando...")
    return scrape_generico(
        url="https://www.manaus.am.gov.br/noticia",
        orgao="Prefeitura Manaus",
        tema="Gestão Municipal",
        seletores=["article", ".noticia", ".card", "li"],
        limite=5,
    )


def scrape_immu() -> list[dict]:
    """IMMU — Instituto Municipal de Mobilidade Urbana."""
    print("\n[IMMU] Iniciando...")
    return scrape_generico(
        url="https://immu.manaus.am.gov.br",
        orgao="IMMU",
        tema="Mobilidade Urbana",
        seletores=["article", ".post", "a"],
        limite=4,
    )


def scrape_implurb() -> list[dict]:
    """Implurb — Instituto Municipal de Planejamento Urbano."""
    print("\n[Implurb] Iniciando...")
    return scrape_generico(
        url="https://implurb.manaus.am.gov.br",
        orgao="Implurb",
        tema="Planejamento Urbano",
        seletores=["article", ".post", "a"],
        limite=4,
    )


def scrape_sefaz_am() -> list[dict]:
    """SEFAZ-AM — Legislação tributária do Amazonas."""
    print("\n[SEFAZ-AM] Iniciando...")
    return scrape_generico(
        url="https://www.sefaz.am.gov.br/area/legislacao",
        orgao="SEFAZ-AM",
        tema="Tributação / AM",
        seletores=[".legislacao a", "table tr td a", "li a", "article"],
        limite=6,
    )


def scrape_seinfra_am() -> list[dict]:
    """Seinfra-AM — Secretaria de Infraestrutura."""
    print("\n[Seinfra-AM] Iniciando...")
    return scrape_generico(
        url="https://www.seinfra.am.gov.br",
        orgao="Seinfra-AM",
        tema="Infraestrutura / AM",
        seletores=["article", ".post", "a"],
        limite=4,
    )


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    inicio = datetime.now()
    print(f"\n{'='*55}")
    print(f"  CEA Radar — Scraping iniciado: {inicio:%d/%m/%Y %H:%M}")
    print(f"{'='*55}")

    scrapers = [
        # Diários Oficiais (prioridade máxima)
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
    falhas = []
    for fn in scrapers:
        try:
            resultado = fn()
            todas.extend(resultado)
        except Exception as e:
            nome = fn.__name__.replace("scrape_", "").upper()
            print(f"  ⚠️  Erro inesperado em {fn.__name__}: {e}")
            falhas.append(nome)

    # Ordena: críticos primeiro, depois alto, médio, baixo; desempate por data desc
    ordem = {"critico": 0, "alto": 1, "medio": 2, "baixo": 3}
    todas.sort(key=lambda p: (ordem.get(p["impacto"], 9), p["data"]))

    # Remove duplicatas pelo título (primeiros 80 chars, case-insensitive)
    vistos = set()
    unicas = []
    for p in todas:
        chave = p["titulo"][:80].lower().strip()
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(p)

    # Remove entradas de falha se houver publicações reais do mesmo órgão
    orgaos_com_dados = {p["orgao"] for p in unicas if "falha" not in p["titulo"].lower() and "verificado" not in p["titulo"].lower()}
    unicas_filtradas = [
        p for p in unicas
        if not (("falha" in p["titulo"].lower() or "verificado" in p["titulo"].lower())
                and p["orgao"] in orgaos_com_dados)
    ]

    fim = datetime.now()

    # Estatísticas para o dashboard
    total = len(unicas_filtradas)
    criticas = sum(1 for p in unicas_filtradas if p["impacto"] == "critico")
    altas = sum(1 for p in unicas_filtradas if p["impacto"] == "alto")
    medias = sum(1 for p in unicas_filtradas if p["impacto"] == "medio")
    baixas = sum(1 for p in unicas_filtradas if p["impacto"] == "baixo")
    consultas = sum(1 for p in unicas_filtradas if "consulta" in p["tags"])

    # Contagem por órgão para o gráfico de barras
    por_orgao = {}
    for p in unicas_filtradas:
        por_orgao[p["orgao"]] = por_orgao.get(p["orgao"], 0) + 1

    saida = {
        # Metadados para o dashboard
        "ultima_coleta":   fim.strftime("%d/%m/%Y %H:%M"),
        "mes_ano":         hoje_mes_ano(),
        "total":           total,
        "fontes":          len(scrapers),
        "fontes_com_falha": falhas,
        # Estatísticas de impacto
        "stats": {
            "critico": criticas,
            "alto":    altas,
            "medio":   medias,
            "baixo":   baixas,
            "consultas_abertas": consultas,
        },
        # Contagem por órgão (para gráfico de barras)
        "por_orgao": dict(sorted(por_orgao.items(), key=lambda x: x[1], reverse=True)),
        # Publicações completas
        "publicacoes": unicas_filtradas,
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
