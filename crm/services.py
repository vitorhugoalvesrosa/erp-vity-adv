import json
import re
import urllib.request
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Conta, LeadDistributionRule


def normalizar_documento(documento):
    return re.sub(r"\D", "", documento or "")


def identificar_tipo_pessoa(documento):
    digits = normalizar_documento(documento)
    if len(digits) == 11:
        return Conta.TipoPessoa.PF
    if len(digits) == 14:
        return Conta.TipoPessoa.PJ
    return Conta.TipoPessoa.NAO_INFORMADO


@transaction.atomic
def distribuir_lead(conta):
    if conta.vendedor_id:
        return conta.vendedor

    regra = (
        LeadDistributionRule.objects.select_for_update()
        .select_related("vendedor")
        .filter(ativo=True)
        .order_by("atribuicoes", "pk")
        .first()
    )
    if not regra:
        return None

    conta.vendedor = regra.vendedor
    conta.save(update_fields=["vendedor"])
    regra.atribuicoes += 1
    regra.save(update_fields=["atribuicoes"])
    return regra.vendedor


def _documento_da_mensagem(texto):
    compact = normalizar_documento(texto)
    match = re.search(r"(?<!\d)(\d{11}|\d{14})(?!\d)", compact)
    return match.group(1) if match else ""


def qualificar_mensagem(conta, texto):
    lower = texto.lower()
    qualificacao = dict(conta.qualificacao)
    documento = _documento_da_mensagem(texto)
    if documento:
        qualificacao["documento"] = documento
        tipo = identificar_tipo_pessoa(documento)
        conta.tipo_pessoa = tipo

    intents = {
        "urgencia": ("urgente", "hoje", "agora", "prazo"),
        "interesse": ("processo", "advogado", "consulta", "contratar", "orçamento"),
        "negocio": ("empresa", "cnpj", "meu negócio", "sociedade"),
    }
    score = conta.score_lead
    for key, terms in intents.items():
        matched = any(term in lower for term in terms)
        qualificacao[key] = matched
        if matched:
            score += 15
    if documento:
        score += 25
    conta.score_lead = min(score, 100)
    conta.qualificacao = qualificacao
    conta.data_ultima_interacao = timezone.now()
    conta.save(
        update_fields=[
            "tipo_pessoa",
            "score_lead",
            "qualificacao",
            "data_ultima_interacao",
        ]
    )
    return qualificacao


WHATSAPP_SYSTEM_PROMPT = (
    "Você é o atendente inicial de um escritório de advocacia no WhatsApp. "
    "Responda de forma profissional, cordial e curta (até 3 frases), sem prometer resultado "
    "jurídico e sem dar orientação jurídica definitiva. Colete: nome, se é pessoa física ou "
    "jurídica (CPF/CNPJ), assunto, urgência e melhor horário para contato. Nunca peça senhas, "
    "códigos ou dados bancários."
)


def responder_com_ia(texto, qualificacao, nome_cliente=""):
    from core.ai import AIError, feature_enabled, generate
    from core.models import AIUsageLog

    if feature_enabled(AIUsageLog.Feature.WHATSAPP):
        prompt = json.dumps(
            {"mensagem_do_lead": texto, "qualificacao_atual": qualificacao, "nome": nome_cliente},
            ensure_ascii=False,
        )
        try:
            resposta = generate(
                AIUsageLog.Feature.WHATSAPP,
                prompt,
                system=WHATSAPP_SYSTEM_PROMPT,
                max_tokens=4000,
                effort="low",
            ).text
            if resposta:
                return resposta
        except AIError:
            pass
        return "Recebi sua mensagem. Um especialista irá priorizar o atendimento. Você pode informar seu nome e, se quiser, CPF ou CNPJ?"

    api_url = getattr(settings, "AI_API_URL", "")
    api_key = getattr(settings, "AI_API_KEY", "")
    if not api_url or not api_key:
        tipo = qualificacao.get("tipo_pessoa", "")
        documento_pedido = "Qual é o CPF (PF) ou CNPJ (PJ) para identificar o atendimento?"
        if tipo in (Conta.TipoPessoa.PF, Conta.TipoPessoa.PJ):
            documento_pedido = "Como posso ajudar com seu caso ou demanda?"
        return (
            f"Olá{', ' + nome_cliente if nome_cliente else ''}! Sou o assistente comercial "
            f"e jurídico. {documento_pedido}"
        )

    model = getattr(settings, "AI_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Você é o atendente inicial de um escritório e ERP jurídico brasileiro. "
                    "Responda de forma profissional em português, não prometa sucesso jurídico, "
                    "não dê orientação jurídica definitiva e colecione: nome, PF ou PJ, assunto, "
                    "urgência e melhor horário. Não solicite senha, código ou dados bancários."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mensagem": texto,
                        "qualificacao": qualificacao,
                        "cliente": nome_cliente,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return "Recebi sua mensagem. Um especialista irá priorizar o atendimento. Você pode informar seu nome e, se quiser, CPF ou CNPJ?"

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError):
        return "Recebi sua mensagem. Como posso ajudar no seu atendimento?"
