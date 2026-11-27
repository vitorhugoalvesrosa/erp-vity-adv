"""Busca, no DJEN (Comunica PJe / CNJ), os processos em que uma OAB aparece nas publicações.

A API é pública, mas só atende acessos vindos do Brasil. Fora do Brasil (o Render roda nos
EUA), aponte DJEN_BASE_URL para um ponto de acesso no Brasil — veja deploy/djen-proxy/.
"""

import json
import time
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Advogado, Processo, ProcessoEncontrado, somente_digitos

URL_OFICIAL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
POR_PAGINA = 100
MAX_PAGINAS = 10
INTERVALO = 3  # segundos entre páginas: a API aceita cerca de 20 chamadas por minuto
TIMEOUT = 30


class DJENErro(Exception):
    pass


class DJENBloqueado(DJENErro):
    pass


def _url_base():
    return getattr(settings, "DJEN_BASE_URL", "") or URL_OFICIAL


def formatar_cnj(numero):
    d = somente_digitos(numero)
    if len(d) != 20:
        return numero.strip()[:25]
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"


def _get(params, espera=time.sleep):
    url = f"{_url_base()}?{urlencode(params)}"
    headers = {"Accept": "application/json", "User-Agent": "ERP-Vity-ADV/1.0"}
    token = getattr(settings, "DJEN_PROXY_TOKEN", "")
    if token:
        headers["X-Proxy-Token"] = token
    for tentativa in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as resposta:
                corpo = resposta.read()
                restante = resposta.headers.get("x-ratelimit-remaining")
        except HTTPError as exc:
            if exc.code == 403:
                raise DJENBloqueado(
                    "O DJEN recusou o acesso (ele só atende conexões do Brasil). "
                    "Configure o ponto de acesso no Brasil (DJEN_BASE_URL)."
                ) from exc
            if exc.code in (429, 503) and tentativa < 2:
                espera(20)
                continue
            raise DJENErro(f"O DJEN respondeu com erro {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DJENErro("Não foi possível conectar ao DJEN. Tente de novo em alguns minutos.") from exc
        try:
            dados = json.loads(corpo)
        except ValueError as exc:
            raise DJENErro("O DJEN devolveu uma resposta inesperada.") from exc
        if not isinstance(dados, dict) or not isinstance(dados.get("items"), list):
            raise DJENErro("O DJEN devolveu uma resposta sem a lista de publicações.")
        if restante is not None and restante.isdigit() and int(restante) <= 2:
            espera(60)
        return dados
    raise DJENErro("O DJEN está ocupado. Tente de novo em alguns minutos.")


def buscar_publicacoes(numero_oab, uf_oab, dias=365, espera=time.sleep):
    """Devolve (itens, total_informado). Lê no máximo MAX_PAGINAS páginas de 100."""
    hoje = timezone.localdate()
    params = {
        "numeroOab": somente_digitos(numero_oab).lstrip("0"),
        "ufOab": uf_oab.upper(),
        "dataDisponibilizacaoInicio": (hoje - timedelta(days=dias)).isoformat(),
        "dataDisponibilizacaoFim": hoje.isoformat(),
        "itensPorPagina": POR_PAGINA,
    }
    itens, total = [], 0
    for pagina in range(1, MAX_PAGINAS + 1):
        if pagina > 1:
            espera(INTERVALO)
        dados = _get({**params, "pagina": pagina}, espera=espera)
        total = dados.get("count") or total
        lote = dados["items"]
        itens.extend(lote)
        if len(lote) < POR_PAGINA or len(itens) >= (total or 0):
            break
    return itens, total


def _data(texto):
    try:
        return date.fromisoformat(str(texto)[:10])
    except ValueError:
        return None


def agrupar_por_processo(itens):
    processos = {}
    for item in itens:
        if not isinstance(item, dict):
            continue
        digitos = somente_digitos(item.get("numero_processo") or item.get("numeroprocessocommascara"))
        if len(digitos) != 20:
            continue
        info = processos.setdefault(
            digitos,
            {"numero_cnj": formatar_cnj(digitos), "total": 0, "ativo": set(), "passivo": set(), "data": None},
        )
        info["total"] += 1
        data = _data(item.get("data_disponibilizacao") or item.get("datadisponibilizacao") or "")
        if data and (info["data"] is None or data >= info["data"]):
            info["data"] = data
            info["tribunal"] = str(item.get("siglaTribunal") or "")[:30]
            info["orgao"] = str(item.get("nomeOrgao") or "")[:255]
            info["classe"] = str(item.get("nomeClasse") or "")[:255]
            link = str(item.get("link") or "")
            info["link"] = link[:500] if link.startswith("https://") else ""
        for parte in item.get("destinatarios") or []:
            if not isinstance(parte, dict) or not parte.get("nome"):
                continue
            polo = str(parte.get("polo") or "").upper()
            if polo.startswith("A"):
                info["ativo"].add(parte["nome"].strip())
            elif polo.startswith("P"):
                info["passivo"].add(parte["nome"].strip())
    return processos


def _juntar(nomes):
    return ", ".join(sorted(nomes))[:500]


def sincronizar_advogado(advogado: Advogado, dias=365, espera=time.sleep):
    """Busca a OAB no DJEN, grava os processos encontrados e vincula os que já estão no ERP."""
    try:
        itens, total = buscar_publicacoes(advogado.oab_numero, advogado.oab_uf, dias=dias, espera=espera)
    except DJENErro as exc:
        Advogado.objects.filter(pk=advogado.pk).update(
            ultima_busca_em=timezone.now(), ultima_busca_mensagem=str(exc)[:255]
        )
        raise

    encontrados = agrupar_por_processo(itens)
    existentes = {somente_digitos(p.numero_cnj): p for p in Processo.objects.only("pk", "numero_cnj")}
    resumo = {"publicacoes": len(itens), "processos": len(encontrados), "vinculados": 0, "novos": 0}
    with transaction.atomic():
        for digitos, info in encontrados.items():
            registro, _ = ProcessoEncontrado.objects.get_or_create(numero_cnj=info["numero_cnj"])
            registro.tribunal = info.get("tribunal", "")
            registro.orgao = info.get("orgao", "")
            registro.classe = info.get("classe", "")
            registro.polo_ativo = _juntar(info["ativo"])
            registro.polo_passivo = _juntar(info["passivo"])
            registro.ultima_publicacao = info["data"]
            registro.total_publicacoes = info["total"]
            registro.link = info.get("link", "")
            processo = existentes.get(digitos)
            if processo:
                processo.advogados.add(advogado)
                registro.processo = processo
                registro.situacao = ProcessoEncontrado.Situacao.VINCULADO
                resumo["vinculados"] += 1
            elif registro.situacao != ProcessoEncontrado.Situacao.IGNORADO:
                registro.situacao = ProcessoEncontrado.Situacao.NOVO
                resumo["novos"] += 1
            registro.save()
            registro.advogados.add(advogado)
    mensagem = f"{resumo['processos']} processo(s) em {resumo['publicacoes']} publicação(ões)"
    if total and total > len(itens):
        mensagem += f" — lidas {len(itens)} de {total}; busque de novo com período menor"
        resumo["incompleto"] = True
    Advogado.objects.filter(pk=advogado.pk).update(ultima_busca_em=timezone.now(), ultima_busca_mensagem=mensagem[:255])
    return resumo
