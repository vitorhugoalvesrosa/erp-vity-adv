"""Documentos do cliente: procurações e declarações preenchidas com a qualificação."""

import zipfile
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from pypdf import PdfWriter

from crm.models import Conta
from juridico.documentos import campos_faltando, formatar_documento, gerar_docx, gerar_pdf, nome_arquivo
from juridico.models import ModeloDocumento

from .models import CompanyProfile

TIPOS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _modelos_para(conta):
    modelos = ModeloDocumento.objects.filter(ativo=True)
    if conta.tipo_pessoa in (Conta.TipoPessoa.PF, Conta.TipoPessoa.PJ):
        modelos = modelos.filter(aplica_a=conta.tipo_pessoa)
    return modelos


@login_required
def cliente_documentos(request, pk):
    _require(request, "crm.view_conta")
    conta = get_object_or_404(Conta, pk=pk)
    modelos = list(_modelos_para(conta))
    for modelo in modelos:
        modelo.faltando = campos_faltando(modelo, conta)
    return render(
        request,
        "core/cliente_documentos.html",
        {
            "page_title": f"Documentos de {conta.nome}",
            "active_module": "contas",
            "conta": conta,
            "documento_formatado": formatar_documento(conta.documento),
            "modelos": modelos,
            "tipo_definido": conta.tipo_pessoa in (Conta.TipoPessoa.PF, Conta.TipoPessoa.PJ),
            "can_edit": request.user.has_perm("crm.change_conta"),
            "can_edit_modelos": request.user.has_perm("juridico.change_modelodocumento"),
        },
    )


@login_required
def cliente_documento_gerar(request, pk, modelo_pk, formato):
    _require(request, "crm.view_conta")
    if formato not in TIPOS:
        raise Http404
    conta = get_object_or_404(Conta, pk=pk)
    modelo = get_object_or_404(ModeloDocumento, pk=modelo_pk, ativo=True)
    data = None if request.GET.get("data") == "branco" else timezone.localdate()
    logo = _logo()
    gerar = gerar_pdf if formato == "pdf" else gerar_docx
    response = HttpResponse(gerar(modelo, conta, data=data, logo=logo), content_type=TIPOS[formato])
    disposicao = "inline" if formato == "pdf" else "attachment"
    response["Content-Disposition"] = f'{disposicao}; filename="{nome_arquivo(modelo, conta, formato)}"'
    return response


def _logo():
    perfil = CompanyProfile.objects.first()
    return bytes(perfil.logo_data) if perfil and perfil.has_logo else None


@login_required
def cliente_documentos_lote(request, pk):
    """Vários documentos de uma vez: PDF num arquivo só (um por página) ou Word em .zip."""
    _require(request, "crm.view_conta")
    conta = get_object_or_404(Conta, pk=pk)
    formato = request.GET.get("formato", "pdf")
    if formato not in TIPOS:
        raise Http404
    modelos = _modelos_para(conta)
    if request.GET.get("todos") != "1":
        ids = [int(valor) for valor in request.GET.getlist("modelos") if valor.isdigit()]
        modelos = modelos.filter(pk__in=ids)
    modelos = list(modelos)
    if not modelos:
        messages.error(request, "Selecione ao menos um documento para baixar.")
        return redirect("cliente-documentos", pk=conta.pk)

    data = None if request.GET.get("data") == "branco" else timezone.localdate()
    logo = _logo()
    base = f"documentos-{slugify(conta.nome)[:50] or conta.pk}"
    if formato == "pdf":
        juntos = PdfWriter()
        for modelo in modelos:
            juntos.append(BytesIO(gerar_pdf(modelo, conta, data=data, logo=logo)))
        buffer = BytesIO()
        juntos.write(buffer)
        response = HttpResponse(buffer.getvalue(), content_type=TIPOS["pdf"])
        response["Content-Disposition"] = f'attachment; filename="{base}.pdf"'
        return response

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        for numero, modelo in enumerate(modelos, start=1):
            nome = f"{numero:02d}-{nome_arquivo(modelo, conta, 'docx')}"
            pacote.writestr(nome, gerar_docx(modelo, conta, data=data, logo=logo))
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{base}.zip"'
    return response
