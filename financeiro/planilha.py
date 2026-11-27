"""Importa e exporta o controle financeiro no formato da planilha do escritório.

Colunas: Data | Tipo | Categoria | Descrição / Detalhamento | Entrada (R$) | Saída (R$) |
Forma de pagamento | Observações.
"""

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import CategoriaFinanceira, Lancamento, TipoLancamento

MAX_LINHAS = 5000
COLUNAS = (
    ("data", "Data", 12),
    ("tipo", "Tipo", 10),
    ("categoria", "Categoria", 28),
    ("descricao", "Descrição / Detalhamento", 48),
    ("entrada", "Entrada (R$)", 16),
    ("saida", "Saída (R$)", 16),
    ("forma_pagamento", "Forma de pagamento", 20),
    ("observacoes", "Observações", 32),
)
FORMAS = {
    "pix": "pix",
    "boleto": "boleto",
    "transferencia": "transferencia",
    "ted": "transferencia",
    "doc": "transferencia",
    "cartao de credito": "cartao_credito",
    "credito": "cartao_credito",
    "cartao de debito": "cartao_debito",
    "debito": "cartao_debito",
    "debito automatico": "debito_automatico",
    "dinheiro": "dinheiro",
    "especie": "dinheiro",
}


class PlanilhaInvalida(Exception):
    pass


def _norm(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texto).strip().lower()


def _campo_do_cabecalho(texto):
    t = _norm(texto)
    if t == "data":
        return "data"
    if t == "tipo":
        return "tipo"
    if t == "categoria":
        return "categoria"
    for prefixo, campo in (
        ("descri", "descricao"),
        ("entrada", "entrada"),
        ("saida", "saida"),
        ("forma", "forma_pagamento"),
        ("observ", "observacoes"),
    ):
        if t.startswith(prefixo):
            return campo
    return None


def _valor(celula):
    if celula in (None, ""):
        return None
    if isinstance(celula, (int, float, Decimal)):
        numero = Decimal(str(celula))
    else:
        texto = str(celula).replace("R$", "").replace(" ", "").replace("\xa0", "")
        if not texto or texto in {"-", "–"}:
            return None
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        try:
            numero = Decimal(texto)
        except InvalidOperation:
            raise ValueError(f"valor “{celula}” não é um número")
    numero = abs(numero).quantize(Decimal("0.01"))
    return numero or None


def _data(celula):
    if isinstance(celula, datetime):
        return celula.date()
    if isinstance(celula, date):
        return celula
    texto = str(celula or "").strip()
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _forma(texto):
    t = _norm(texto)
    if not t:
        return "", ""
    if t in FORMAS:
        return FORMAS[t], ""
    for chave, valor in FORMAS.items():
        if chave in t:
            return valor, ""
    return "", str(texto).strip()


def _achar_cabecalho(planilha):
    for numero, linha in enumerate(planilha.iter_rows(min_row=1, max_row=30, values_only=True), start=1):
        mapa = {}
        for indice, celula in enumerate(linha):
            campo = _campo_do_cabecalho(celula)
            if campo and campo not in mapa:
                mapa[campo] = indice
        if {"data", "categoria"} <= mapa.keys() and ({"entrada", "saida"} & mapa.keys()):
            return numero, mapa
    return None, None


def importar(arquivo, usuario=None):
    """Lê o .xlsx e cria os lançamentos. Devolve um resumo com contagens e avisos."""
    try:
        livro = load_workbook(arquivo, data_only=True, read_only=True)
    except Exception as exc:  # arquivo corrompido ou não é .xlsx
        raise PlanilhaInvalida("Não foi possível abrir o arquivo. Envie uma planilha .xlsx.") from exc

    resumo = {"criados": 0, "duplicados": 0, "ignorados": 0, "categorias_novas": [], "avisos": []}
    encontrou = False
    categorias = {(c.tipo, _norm(c.nome)): c for c in CategoriaFinanceira.objects.all()}

    with transaction.atomic():
        for planilha in livro.worksheets:
            linha_cabecalho, mapa = _achar_cabecalho(planilha)
            if not mapa:
                continue
            encontrou = True
            for numero, linha in enumerate(
                planilha.iter_rows(min_row=linha_cabecalho + 1, values_only=True), start=linha_cabecalho + 1
            ):
                if numero - linha_cabecalho > MAX_LINHAS:
                    resumo["avisos"].append(f"{planilha.title}: só as primeiras {MAX_LINHAS} linhas foram lidas.")
                    break

                def pega(campo):
                    indice = mapa.get(campo)
                    return linha[indice] if indice is not None and indice < len(linha) else None

                if not any(v not in (None, "") for v in linha):
                    continue
                onde = f"{planilha.title}, linha {numero}"
                try:
                    entrada, saida = _valor(pega("entrada")), _valor(pega("saida"))
                except ValueError as exc:
                    resumo["ignorados"] += 1
                    resumo["avisos"].append(f"{onde}: {exc}.")
                    continue
                data = _data(pega("data"))
                nome_categoria = str(pega("categoria") or "").strip()
                if not data or not (entrada or saida) or not nome_categoria:
                    # Linhas de total, subtítulos e linhas incompletas.
                    resumo["ignorados"] += 1
                    if data or nome_categoria:
                        resumo["avisos"].append(f"{onde}: sem data, categoria ou valor; ignorada.")
                    continue

                tipo_texto = _norm(pega("tipo"))
                if tipo_texto.startswith("entr"):
                    tipo = TipoLancamento.ENTRADA
                elif tipo_texto.startswith("sai"):
                    tipo = TipoLancamento.SAIDA
                else:
                    tipo = TipoLancamento.ENTRADA if entrada else TipoLancamento.SAIDA
                valor = (entrada if tipo == TipoLancamento.ENTRADA else saida) or entrada or saida

                chave = (tipo, _norm(nome_categoria))
                categoria = categorias.get(chave)
                if categoria is None:
                    categoria = CategoriaFinanceira.objects.create(tipo=tipo, nome=nome_categoria[:100])
                    categorias[chave] = categoria
                    resumo["categorias_novas"].append(f"{categoria.nome} ({categoria.sigla})")

                forma, forma_outra = _forma(pega("forma_pagamento"))
                observacoes = str(pega("observacoes") or "").strip()
                if forma_outra:
                    observacoes = f"{observacoes} · Forma: {forma_outra}".strip(" ·")
                descricao = (str(pega("descricao") or "").strip() or categoria.nome)[:255]

                dados = {
                    "data": data,
                    "tipo": tipo,
                    "categoria": categoria,
                    "descricao": descricao,
                    "valor": valor,
                }
                if Lancamento.objects.filter(**dados).exists():
                    resumo["duplicados"] += 1
                    continue
                Lancamento.objects.create(
                    **dados,
                    forma_pagamento=forma,
                    observacoes=observacoes[:255],
                    criado_por=usuario,
                )
                resumo["criados"] += 1

    if not encontrou:
        raise PlanilhaInvalida(
            "Não achei o cabeçalho. A planilha precisa das colunas Data, Categoria e Entrada/Saída."
        )
    return resumo


# --- Exportação -------------------------------------------------------------------------

AZUL = PatternFill("solid", fgColor="0D1B2A")
DOURADO = PatternFill("solid", fgColor="A07F2C")
VERDE = PatternFill("solid", fgColor="E3F4EA")
VERMELHO = PatternFill("solid", fgColor="FBE9E9")
LINHA = Border(bottom=Side(style="thin", color="D9DEE5"))
MOEDA = '"R$" #,##0.00'


def exportar(lancamentos, titulo):
    livro = Workbook()
    folha = livro.active
    folha.title = "Controle financeiro"
    ultima = get_column_letter(len(COLUNAS))

    folha.merge_cells(f"A1:{ultima}1")
    folha["A1"] = titulo
    folha["A1"].font = Font(bold=True, size=14, color="FFFFFF")
    folha["A1"].fill = AZUL
    folha["A1"].alignment = Alignment(horizontal="center", vertical="center")
    folha.row_dimensions[1].height = 28

    for indice, (_, rotulo, largura) in enumerate(COLUNAS, start=1):
        celula = folha.cell(row=2, column=indice, value=rotulo)
        celula.font = Font(bold=True, color="1A1A1A")
        celula.fill = DOURADO
        celula.alignment = Alignment(horizontal="center")
        folha.column_dimensions[get_column_letter(indice)].width = largura
    folha.freeze_panes = "A3"

    total_entrada = total_saida = Decimal("0")
    linha = 2
    for lancamento in lancamentos:
        linha += 1
        valores = (
            lancamento.data,
            lancamento.get_tipo_display(),
            lancamento.categoria.nome,
            lancamento.descricao,
            lancamento.entrada,
            lancamento.saida,
            lancamento.get_forma_pagamento_display() if lancamento.forma_pagamento else "",
            lancamento.observacoes,
        )
        for indice, valor in enumerate(valores, start=1):
            celula = folha.cell(row=linha, column=indice, value=valor)
            celula.border = LINHA
        folha.cell(row=linha, column=1).number_format = "DD/MM/YYYY"
        for coluna, preenchimento in ((5, VERDE), (6, VERMELHO)):
            celula = folha.cell(row=linha, column=coluna)
            celula.number_format = MOEDA
            if celula.value is not None:
                celula.fill = preenchimento
        total_entrada += lancamento.entrada or 0
        total_saida += lancamento.saida or 0

    linha += 1
    folha.cell(row=linha, column=4, value="Totais").font = Font(bold=True)
    for coluna, total in ((5, total_entrada), (6, total_saida)):
        celula = folha.cell(row=linha, column=coluna, value=total)
        celula.number_format = MOEDA
        celula.font = Font(bold=True)
    linha += 1
    folha.cell(row=linha, column=4, value="Saldo (entradas − saídas)").font = Font(bold=True)
    saldo = folha.cell(row=linha, column=5, value=total_entrada - total_saida)
    saldo.number_format = MOEDA
    saldo.font = Font(bold=True)

    buffer = BytesIO()
    livro.save(buffer)
    return buffer.getvalue()
