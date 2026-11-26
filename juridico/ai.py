"""Recursos de IA do módulo jurídico."""

from datetime import timedelta

from django.utils import timezone

from core.ai import generate
from core.models import AIUsageLog

from .models import PecaJuridica, Publicacao, Tarefa

MAX_CONTEXT_CHARS = 60000

PUBLICACAO_SCHEMA = {
    "type": "object",
    "properties": {
        "resumo": {
            "type": "string",
            "description": "Resumo em linguagem simples, em até 4 frases.",
        },
        "classificacao": {
            "type": "string",
            "enum": [choice for choice, _ in Publicacao.ClassificacaoIA.choices],
        },
        "urgente": {"type": "boolean"},
        "providencia": {
            "type": "string",
            "description": "Próxima providência para o advogado, em uma frase curta. Vazio se nenhuma.",
        },
        "prazo_dias": {
            "type": "integer",
            "description": "Prazo em dias indicado ou legalmente aplicável; 0 se não houver prazo.",
        },
        "prazo_fundamento": {
            "type": "string",
            "description": "De onde vem o prazo (texto da publicação ou dispositivo legal). Vazio se 0.",
        },
    },
    "required": [
        "resumo",
        "classificacao",
        "urgente",
        "providencia",
        "prazo_dias",
        "prazo_fundamento",
    ],
    "additionalProperties": False,
}


def analisar_publicacao(publicacao, user=None):
    processo = publicacao.processo
    contexto_processo = (
        f"Processo vinculado: {processo.numero_cnj} — {processo.titulo} "
        f"(área {processo.get_area_display()}, cliente {processo.cliente})."
        if processo
        else "Publicação ainda sem processo vinculado."
    )
    prompt = (
        f"{contexto_processo}\n"
        f"Tribunal/diário: {publicacao.tribunal}\n"
        f"Data da publicação: {publicacao.publicada_em:%d/%m/%Y}\n"
        f"Título: {publicacao.titulo}\n\n"
        "<publicacao>\n"
        f"{publicacao.conteudo[:MAX_CONTEXT_CHARS]}\n"
        "</publicacao>\n\n"
        "Analise a publicação acima. Classifique o ato, diga se é urgente, sugira a próxima "
        "providência e indique o prazo em dias quando o texto ou a lei processual o definirem. "
        "Se o prazo não estiver claro, use 0."
    )
    result = generate(
        AIUsageLog.Feature.PUBLICATION,
        prompt,
        system="Você analisa publicações de diários oficiais para advogados.",
        user=user,
        json_schema=PUBLICACAO_SCHEMA,
        effort="low",
    )
    data = result.data
    classificacao = data.get("classificacao", "")
    if classificacao not in Publicacao.ClassificacaoIA.values:
        classificacao = Publicacao.ClassificacaoIA.OUTRO
    prazo = data.get("prazo_dias") or 0
    publicacao.resumo_ia = (data.get("resumo") or "").strip()
    publicacao.classificacao_ia = classificacao
    publicacao.urgente_ia = bool(data.get("urgente"))
    publicacao.providencia_ia = (data.get("providencia") or "").strip()[:255]
    publicacao.prazo_ia_dias = prazo if 0 < prazo <= 365 else None
    publicacao.prazo_ia_fundamento = (
        (data.get("prazo_fundamento") or "").strip()[:255] if publicacao.prazo_ia_dias else ""
    )
    publicacao.analisada_em = timezone.now()
    publicacao.save(
        update_fields=[
            "resumo_ia",
            "classificacao_ia",
            "urgente_ia",
            "providencia_ia",
            "prazo_ia_dias",
            "prazo_ia_fundamento",
            "analisada_em",
        ]
    )
    return publicacao


def criar_tarefa_sugerida(publicacao, responsavel):
    """Cria a tarefa sugerida pela IA. O vencimento é uma estimativa em dias corridos."""
    vencimento = None
    if publicacao.prazo_ia_dias:
        vencimento = publicacao.publicada_em + timedelta(days=publicacao.prazo_ia_dias)
    descricao = (
        f"Sugerida pela IA a partir da publicação “{publicacao.titulo}” "
        f"({publicacao.publicada_em:%d/%m/%Y}).\n\n{publicacao.resumo_ia}"
    )
    if publicacao.prazo_ia_dias:
        descricao += (
            f"\n\nPrazo sugerido: {publicacao.prazo_ia_dias} dia(s)"
            f"{' — ' + publicacao.prazo_ia_fundamento if publicacao.prazo_ia_fundamento else ''}. "
            "O vencimento foi estimado em dias corridos: confira a contagem em dias úteis, "
            "feriados e suspensões antes de confiar nele."
        )
    return Tarefa.objects.create(
        processo=publicacao.processo,
        titulo=(publicacao.providencia_ia or f"Tratar publicação: {publicacao.titulo}")[:255],
        descricao=descricao,
        responsavel=responsavel,
        prioridade=(
            Tarefa.Prioridade.URGENTE if publicacao.urgente_ia else Tarefa.Prioridade.ALTA
        ),
        vencimento=vencimento,
    )


def contexto_processo(processo):
    linhas = [
        f"Número CNJ: {processo.numero_cnj}",
        f"Título: {processo.titulo}",
        f"Cliente: {processo.cliente}",
        f"Parte contrária: {processo.parte_contraria or 'não informada'}",
        f"Tribunal / vara: {processo.tribunal} / {processo.vara or 'não informada'}",
        f"Área: {processo.get_area_display()} · Status: {processo.get_status_display()}",
        f"Valor da causa: R$ {processo.valor_causa:.2f}",
        f"Responsável: {processo.responsavel.get_full_name() or processo.responsavel.username}",
    ]
    if processo.observacoes:
        linhas.append(f"Observações internas: {processo.observacoes}")

    movimentacoes = processo.movimentacoes.all()[:40]
    if movimentacoes:
        linhas.append("\nMovimentações (mais recentes primeiro):")
        linhas += [f"- {m.data:%d/%m/%Y}: {m.descricao}" for m in movimentacoes]
    prazos = processo.prazos.all()[:20]
    if prazos:
        linhas.append("\nPrazos:")
        linhas += [
            f"- {p.data_vencimento:%d/%m/%Y} · {p.titulo} · {p.get_status_display()}" for p in prazos
        ]
    tarefas = processo.tarefas.all()[:20]
    if tarefas:
        linhas.append("\nTarefas:")
        linhas += [
            f"- {t.titulo} · {t.get_status_display()}"
            f"{' · vence ' + t.vencimento.strftime('%d/%m/%Y') if t.vencimento else ''}"
            for t in tarefas
        ]
    audiencias = processo.audiencias.all()[:10]
    if audiencias:
        linhas.append("\nAudiências:")
        linhas += [
            f"- {timezone.localtime(a.data_hora):%d/%m/%Y %H:%M} · {a.titulo} · {a.get_status_display()}"
            for a in audiencias
        ]
    publicacoes = processo.publicacoes.all()[:10]
    if publicacoes:
        linhas.append("\nPublicações recentes:")
        for pub in publicacoes:
            resumo = pub.resumo_ia or pub.conteudo[:600]
            linhas.append(f"- {pub.publicada_em:%d/%m/%Y} · {pub.titulo}: {resumo}")
    return "\n".join(linhas)[:MAX_CONTEXT_CHARS]


def resumir_processo(processo, user=None):
    prompt = (
        "<processo>\n"
        f"{contexto_processo(processo)}\n"
        "</processo>\n\n"
        "Escreva um resumo do caso para o advogado responsável, em markdown simples, com as "
        "seções: **Situação atual**, **Histórico relevante**, **Próximos passos e prazos** e "
        "**Pontos de atenção**. Seja objetivo: no máximo 250 palavras. Use somente as "
        "informações acima."
    )
    result = generate(
        AIUsageLog.Feature.CASE_SUMMARY,
        prompt,
        system="Você prepara resumos de processos judiciais para advogados.",
        user=user,
    )
    processo.resumo_ia = result.text
    processo.resumo_ia_em = timezone.now()
    processo.save(update_fields=["resumo_ia", "resumo_ia_em"])
    return processo


def perguntar_assistente(pergunta, *, processo=None, historico=None, user=None):
    system = (
        "Você é o assistente jurídico do escritório. Ajude com análise de casos, rascunhos de "
        "peças, organização de prazos e dúvidas processuais. Quando citar lei ou "
        "jurisprudência, indique o dispositivo e lembre que deve ser conferido na fonte "
        "oficial. Responda em markdown simples e seja direto."
    )
    if processo:
        system += (
            "\n\nO usuário está consultando este processo do ERP:\n"
            f"<processo>\n{contexto_processo(processo)}\n</processo>"
        )
    result = generate(
        AIUsageLog.Feature.ASSISTANT,
        pergunta,
        system=system,
        user=user,
        history=historico,
    )
    return result.text


ORIENTACOES_PECA = {
    PecaJuridica.Tipo.PETICAO_INICIAL: "Siga o art. 319 do CPC: endereçamento, qualificação das partes, fatos, fundamentos jurídicos, pedidos com especificações, valor da causa, provas e opção pela audiência de conciliação.",
    PecaJuridica.Tipo.RECLAMACAO_TRABALHISTA: "Siga o art. 840 da CLT: endereçamento à Vara do Trabalho, qualificação, breve exposição dos fatos, pedidos certos, determinados e com indicação de valor.",
    PecaJuridica.Tipo.CONTESTACAO: "Siga os arts. 335 a 342 do CPC: preliminares cabíveis (art. 337), impugnação específica dos fatos, fundamentos de direito e pedidos.",
    PecaJuridica.Tipo.REPLICA: "Réplica à contestação (arts. 350 e 351 do CPC): rebata preliminares e fatos impeditivos, modificativos ou extintivos alegados.",
    PecaJuridica.Tipo.ESPECIFICACAO_PROVAS: "Especifique as provas que se pretende produzir, justificando a pertinência de cada uma.",
    PecaJuridica.Tipo.APELACAO: "Siga o art. 1.010 do CPC: interposição ao juízo de origem e razões ao tribunal, com exposição do fato e do direito, razões do pedido de reforma ou nulidade e pedido de nova decisão.",
    PecaJuridica.Tipo.EMBARGOS_DECLARACAO: "Art. 1.022 do CPC: aponte de forma objetiva a obscuridade, contradição, omissão ou erro material da decisão.",
    PecaJuridica.Tipo.AGRAVO_INSTRUMENTO: "Arts. 1.015 a 1.017 do CPC: verifique o cabimento, exponha fatos e direito, razões do pedido de reforma, pedido de efeito suspensivo ou tutela recursal se aplicável e relação das peças obrigatórias.",
    PecaJuridica.Tipo.OUTRA: "Estruture a peça conforme o tipo descrito pelo advogado.",
}


def gerar_peca(peca, user=None):
    """Gera a minuta da peça a partir das instruções e do processo vinculado."""
    contexto = (
        f"<processo>\n{contexto_processo(peca.processo)}\n</processo>\n\n"
        if peca.processo
        else "Peça sem processo vinculado no ERP.\n\n"
    )
    prompt = (
        f"{contexto}"
        f"Tipo de peça: {peca.get_tipo_display()}\n"
        f"Orientação de estrutura: {ORIENTACOES_PECA.get(peca.tipo, '')}\n\n"
        "<instrucoes_do_advogado>\n"
        f"{peca.instrucoes[:MAX_CONTEXT_CHARS]}\n"
        "</instrucoes_do_advogado>\n\n"
        "Redija a minuta completa da peça em português jurídico formal, pronta para revisão. "
        "Use títulos em markdown (## SEÇÃO) e parágrafos. Onde faltar uma informação "
        "(nomes, endereços, documentos, valores, datas), deixe um marcador entre colchetes, "
        "como [NOME DO AUTOR], em vez de inventar. Cite apenas dispositivos legais que você "
        "tem segurança de existirem; não invente jurisprudência, números de julgados ou "
        "súmulas. Termine com local, data e espaço para assinatura do advogado."
    )
    result = generate(
        AIUsageLog.Feature.DRAFT,
        prompt,
        system=(
            "Você é um advogado brasileiro experiente que redige minutas de peças processuais "
            "para revisão do advogado responsável."
        ),
        user=user,
        max_tokens=32000,
        effort="high",
    )
    peca.conteudo = result.text
    peca.save(update_fields=["conteudo", "atualizada_em"])
    return peca
