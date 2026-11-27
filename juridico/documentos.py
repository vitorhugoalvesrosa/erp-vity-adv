"""Preenche procurações e declarações com a qualificação do cliente (PDF e Word)."""

import re
from io import BytesIO
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from .models import ModeloDocumento

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
VAZIO = "_______________"
VAZIOS = {"ESTADO": "______", "CEP": "____________", "TEL": "______________", "NOME": "_" * 40, "EMPRESA": "_" * 40}


def _vazio(rotulo):
    if rotulo.startswith("ENDEREÇO"):
        return "_" * 34
    return VAZIOS.get(rotulo, VAZIO)
_INLINE = re.compile(r"(\*\*\*.+?\*\*\*|\*\*.+?\*\*|\*[^*\s][^*]*?\*)")


def _digitos(valor):
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def formatar_documento(valor):
    d = _digitos(valor)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return str(valor or "").strip()


def formatar_cep(valor):
    d = _digitos(valor)
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else str(valor or "").strip()


def qualificacao(modelo, conta):
    """Linhas da qualificação: cada linha é uma lista de (rótulo, valor)."""
    endereco = [
        [("ENDEREÇO RESIDENCIAL" if modelo.aplica_a == "pf" else "ENDEREÇO COMERCIAL", conta.endereco)],
        [("BAIRRO", conta.bairro), ("CIDADE", conta.cidade), ("ESTADO", (conta.uf or "").upper())],
        [("CEP", formatar_cep(conta.cep))] + ([("TEL", conta.telefone)] if modelo.incluir_telefone else []),
    ]
    if modelo.aplica_a == ModeloDocumento.AplicaA.PJ:
        return [
            [("EMPRESA", conta.nome)],
            [("CNPJ", formatar_documento(conta.documento)), ("REPRESENTANTE LEGAL", conta.representante_nome)],
            [("NACIONALIDADE", conta.representante_nacionalidade), ("ESTADO CIVIL", conta.representante_estado_civil)],
            [("IDENTIDADE Nº", conta.representante_rg), ("CPF Nº", formatar_documento(conta.representante_cpf))],
            *endereco,
        ]
    return [
        [("NOME", conta.nome)],
        [("NACIONALIDADE", conta.nacionalidade), ("ESTADO CIVIL", conta.estado_civil), ("PROFISSÃO", conta.profissao)],
        [("IDENTIDADE Nº", conta.rg), ("CPF Nº", formatar_documento(conta.documento))],
        *endereco,
    ]


def campos_faltando(modelo, conta):
    return [
        rotulo if len(rotulo) <= 4 else rotulo.capitalize()
        for linha in qualificacao(modelo, conta)
        for rotulo, valor in linha
        if not str(valor).strip()
    ]


def linha_data(modelo, data=None):
    if data is None:
        return f"{modelo.local}, ________ de ____________________ de ________."
    return f"{modelo.local}, {data.day} de {MESES[data.month - 1]} de {data.year}."


def _pedacos(texto):
    """Divide o texto em (trecho, negrito, itálico) a partir de ***, ** e *."""
    for parte in _INLINE.split(texto):
        if not parte:
            continue
        if parte.startswith("***") and parte.endswith("***") and len(parte) > 6:
            yield parte[3:-3], True, True
        elif parte.startswith("**") and parte.endswith("**") and len(parte) > 4:
            yield parte[2:-2], True, False
        elif parte.startswith("*") and parte.endswith("*") and len(parte) > 2:
            yield parte[1:-1], False, True
        else:
            yield parte, False, False


def _paragrafos(corpo):
    return [" ".join(bloco.split()) for bloco in re.split(r"\n\s*\n", corpo or "") if bloco.strip()]


def nome_arquivo(modelo, conta, extensao):
    from django.utils.text import slugify

    return f"{slugify(modelo.nome)[:60]}-{slugify(conta.nome)[:40]}.{extensao}"


# --- PDF ---------------------------------------------------------------------------------


def gerar_pdf(modelo, conta, data=None, logo=None):
    """Gera o PDF; se não couber em uma página, reduz a letra um pouco (como no modelo original)."""
    for tamanho, entrelinha, respiro in ((12, 18, 1.0), (11.5, 16.5, 0.8), (11, 15.5, 0.6), (10.5, 14.5, 0.5)):
        conteudo, paginas = _montar_pdf(modelo, conta, data, logo, tamanho, entrelinha, respiro)
        if paginas == 1:
            break
    return conteudo


def _montar_pdf(modelo, conta, data, logo, tamanho, entrelinha, respiro):
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=3 * cm, rightMargin=2 * cm,
        topMargin=2.6 * cm, bottomMargin=2.8 * cm,
        title=f"{modelo.titulo} — {conta.nome}", author="ERP Vity ADV",
    )
    base = ParagraphStyle("base", fontName="Times-Roman", fontSize=tamanho, leading=entrelinha)
    titulo = ParagraphStyle("titulo", parent=base, fontName="Times-Bold", fontSize=14, alignment=TA_CENTER)
    linha = ParagraphStyle("linha", parent=base, alignment=TA_LEFT, leading=entrelinha + 2)
    corpo = ParagraphStyle("corpo", parent=base, alignment=TA_JUSTIFY, firstLineIndent=2.5 * cm, spaceAfter=8 * respiro)
    centro = ParagraphStyle("centro", parent=base, alignment=TA_CENTER)
    assinatura = ParagraphStyle("assinatura", parent=centro, fontName="Times-Bold", fontSize=11)

    def inline(texto):
        html = []
        for trecho, negrito, italico in _pedacos(texto):
            pedaco = escape(trecho)
            if italico:
                pedaco = f"<i>{pedaco}</i>"
            if negrito:
                pedaco = f"<b>{pedaco}</b>"
            html.append(pedaco)
        return "".join(html)

    historia = [Paragraph(f"<u>{escape(modelo.titulo)}</u>", titulo), Spacer(1, 1.1 * cm * respiro)]
    for campos in qualificacao(modelo, conta):
        texto = "&nbsp;&nbsp;&nbsp;&nbsp;".join(
            f"{escape(rotulo)}: {escape(str(valor).strip()) or _vazio(rotulo)}" for rotulo, valor in campos
        )
        historia.append(Paragraph(texto, linha))
    historia.append(Spacer(1, 0.8 * cm * respiro))
    for paragrafo in _paragrafos(modelo.corpo):
        historia.append(Paragraph(inline(paragrafo), corpo))
    historia += [
        Spacer(1, 0.9 * cm * respiro),
        KeepTogether(
            [
                Paragraph(escape(linha_data(modelo, data)), centro),
                Spacer(1, 1.8 * cm * respiro),
                Paragraph("_" * 52, centro),
                Paragraph(escape(modelo.assinatura), assinatura),
            ]
        ),
    ]

    imagem = ImageReader(BytesIO(logo)) if modelo.usar_logo and logo else None

    def moldura(canvas, doc):
        largura, altura = A4
        canvas.saveState()
        if imagem:
            iw, ih = imagem.getSize()
            # O logo fica à esquerda, na altura do título (como no modelo do escritório).
            alto = 2.2 * cm
            largura_logo = min(alto * iw / ih, 4 * cm)
            canvas.drawImage(
                imagem, 3 * cm, altura - 1.4 * cm - alto, width=largura_logo, height=largura_logo * ih / iw,
                mask="auto",
            )
        if modelo.rodape:
            canvas.setLineWidth(0.6)
            canvas.line(3 * cm, 2.1 * cm, largura - 2 * cm, 2.1 * cm)
            canvas.setFont("Times-Roman", 10)
            canvas.drawCentredString((largura + 1 * cm) / 2, 1.5 * cm, modelo.rodape)
        canvas.restoreState()

    documento.build(historia, onFirstPage=moldura, onLaterPages=moldura)
    return buffer.getvalue(), documento.page


# --- Word --------------------------------------------------------------------------------


def _sublinhado_inferior(paragrafo):
    borda = OxmlElement("w:pBdr")
    linha = OxmlElement("w:top")
    for chave, valor in (("w:val", "single"), ("w:sz", "6"), ("w:space", "4"), ("w:color", "000000")):
        linha.set(qn(chave), valor)
    borda.append(linha)
    paragrafo._p.get_or_add_pPr().append(borda)


def gerar_docx(modelo, conta, data=None, logo=None):
    documento = Document()
    for secao in documento.sections:
        secao.page_height, secao.page_width = Cm(29.7), Cm(21)
        secao.top_margin, secao.bottom_margin = Cm(3), Cm(2.5)
        secao.left_margin, secao.right_margin = Cm(3), Cm(2)
    normal = documento.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(0)

    secao = documento.sections[0]
    if modelo.usar_logo and logo:
        try:
            secao.header.paragraphs[0].add_run().add_picture(BytesIO(logo), height=Cm(2))
        except Exception:  # formato de imagem que o Word não aceita: segue sem logo
            pass
    if modelo.rodape:
        rodape = secao.footer.paragraphs[0]
        rodape.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _sublinhado_inferior(rodape)
        rodape.add_run(modelo.rodape).font.size = Pt(10)

    titulo = documento.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = titulo.add_run(modelo.titulo)
    run.bold, run.underline, run.font.size = True, True, Pt(14)
    documento.add_paragraph()

    for campos in qualificacao(modelo, conta):
        paragrafo = documento.add_paragraph()
        for indice, (rotulo, valor) in enumerate(campos):
            if indice:
                paragrafo.add_run("\t")
            paragrafo.add_run(f"{rotulo}: {str(valor).strip() or _vazio(rotulo)}")
    documento.add_paragraph()

    for texto in _paragrafos(modelo.corpo):
        paragrafo = documento.add_paragraph()
        paragrafo.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragrafo.paragraph_format.first_line_indent = Cm(2.5)
        paragrafo.paragraph_format.space_after = Pt(8)
        for trecho, negrito, italico in _pedacos(texto):
            run = paragrafo.add_run(trecho)
            run.bold, run.italic = negrito, italico

    documento.add_paragraph()
    data_paragrafo = documento.add_paragraph(linha_data(modelo, data))
    data_paragrafo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for _ in range(3):
        documento.add_paragraph()
    for texto, negrito in (("_" * 52, False), (modelo.assinatura, True)):
        paragrafo = documento.add_paragraph()
        paragrafo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragrafo.add_run(texto).bold = negrito

    buffer = BytesIO()
    documento.save(buffer)
    return buffer.getvalue()
