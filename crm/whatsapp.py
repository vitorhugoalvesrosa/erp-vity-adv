"""Atendimento pelo WhatsApp Cloud API (Meta).

- `credenciais()` lê a conexão salva em Administração → WhatsApp (ou as variáveis
  de ambiente, como alternativa);
- `processar_webhook()` registra as mensagens recebidas e os status de entrega e
  aciona o bot;
- `enviar_texto()`, `enviar_arquivo()` e `enviar_modelo()` enviam pela conversa e
  registram a mensagem, inclusive quando o envio falha.
"""

import json
import logging
import re
import secrets
import urllib.request
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core.secure_storage import decrypt_text

from .models import Conta, ConversaWhatsApp, MensagemWhatsApp, WhatsAppConfiguration
from .services import distribuir_lead, qualificar_mensagem, responder_com_ia

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
REQUEST_TIMEOUT = 20
HISTORICO_BOT = 20
MAX_ARQUIVO = 16 * 1024 * 1024
TIPOS_ARQUIVO = {
    "image/jpeg": "image",
    "image/png": "image",
    "audio/mpeg": "audio",
    "audio/ogg": "audio",
    "audio/aac": "audio",
    "audio/mp4": "audio",
    "video/mp4": "video",
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
    "application/vnd.ms-excel": "document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document",
    "text/plain": "document",
}
DESCRICAO_TIPO = {
    "image": "📷 Imagem",
    "audio": "🎤 Áudio",
    "video": "🎬 Vídeo",
    "document": "📄 Documento",
    "sticker": "Figurinha",
    "location": "📍 Localização",
    "contacts": "👤 Contato",
}


class WhatsAppError(Exception):
    """Erro com mensagem segura para exibir ao usuário."""


@dataclass
class Credenciais:
    phone_number_id: str = ""
    access_token: str = ""
    app_secret: str = ""
    verify_token: str = ""
    business_account_id: str = ""
    config: WhatsAppConfiguration | None = None

    @property
    def pode_enviar(self):
        return bool(self.phone_number_id and self.access_token)


def get_configuration():
    return WhatsAppConfiguration.objects.first()


def credenciais():
    config = get_configuration()
    if config and config.is_active:
        try:
            token = decrypt_text(config.access_token_encrypted) if config.has_access_token else ""
            secret = decrypt_text(config.app_secret_encrypted) if config.has_app_secret else ""
        except ImproperlyConfigured:
            logger.exception("Não foi possível descriptografar as credenciais do WhatsApp.")
            token = secret = ""
        return Credenciais(
            phone_number_id=config.phone_number_id,
            access_token=token,
            app_secret=secret,
            verify_token=config.verify_token,
            business_account_id=config.business_account_id,
            config=config,
        )
    if config:
        # Conexão desativada: não usa as variáveis de ambiente como reserva.
        return Credenciais(verify_token=config.verify_token, config=config)
    return Credenciais(
        phone_number_id=getattr(settings, "WHATSAPP_PHONE_ID", ""),
        access_token=getattr(settings, "WHATSAPP_ACCESS_TOKEN", ""),
        app_secret=getattr(settings, "WHATSAPP_APP_SECRET", ""),
        verify_token=getattr(settings, "WHATSAPP_VERIFY_TOKEN", ""),
        business_account_id=getattr(settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", ""),
    )


def normalizar_telefone(valor):
    """Converte o número para o formato internacional usado pela Meta (só dígitos)."""
    internacional = (valor or "").strip().startswith("+")
    digitos = re.sub(r"\D", "", valor or "").lstrip("0")
    if not internacional and len(digitos) in (10, 11):
        digitos = f"55{digitos}"
    return digitos


def formatar_telefone(valor):
    digitos = re.sub(r"\D", "", valor or "")
    if digitos.startswith("55") and len(digitos) in (12, 13):
        ddd, numero = digitos[2:4], digitos[4:]
        return f"+55 ({ddd}) {numero[:-4]}-{numero[-4:]}"
    return f"+{digitos}" if digitos else ""


# --- Graph API ----------------------------------------------------------------------


def _graph_request(method, path, cred, *, payload=None, body=None, content_type=None, raw=False):
    if not cred.access_token:
        raise WhatsAppError("WhatsApp não configurado. Conecte o número em Administração → WhatsApp.")
    version = getattr(settings, "WHATSAPP_GRAPH_VERSION", "v23.0")
    url = path if path.startswith("https://") else f"{GRAPH_URL}/{version}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {cred.access_token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = response.read()
            if raw:
                return data, response.headers.get("Content-Type", "application/octet-stream")
            return json.loads(data.decode("utf-8") or "{}")
    except HTTPError as exc:
        raise WhatsAppError(_erro_graph(exc)) from exc
    except (URLError, TimeoutError) as exc:
        raise WhatsAppError("Não foi possível conectar ao WhatsApp. Tente novamente.") from exc
    except json.JSONDecodeError as exc:
        raise WhatsAppError("Resposta inválida do WhatsApp.") from exc


def _erro_graph(exc):
    try:
        error = json.loads(exc.read().decode("utf-8")).get("error", {})
    except (ValueError, AttributeError):
        error = {}
    code = error.get("code")
    conhecidos = {
        190: "Token de acesso inválido ou expirado. Gere um token permanente e salve novamente.",
        131047: "Passaram mais de 24 h desde a última mensagem do cliente: envie um modelo aprovado.",
        131026: "Não foi possível entregar: o número não tem WhatsApp ou não aceita mensagens.",
        131030: "Número fora da lista de destinatários permitidos da conta de teste da Meta.",
        132001: "Modelo não encontrado ou ainda não aprovado neste idioma.",
        132000: "A quantidade de variáveis não confere com o modelo.",
        100: "Parâmetro inválido enviado ao WhatsApp.",
        10: "O token não tem permissão para este número (whatsapp_business_messaging).",
        200: "O token não tem permissão para este número (whatsapp_business_messaging).",
    }
    if code in conhecidos:
        return conhecidos[code]
    detalhe = error.get("error_user_msg") or error.get("message") or ""
    return f"O WhatsApp recusou o pedido (HTTP {exc.code}). {detalhe}".strip()[:300]


def testar_conexao(cred):
    data = _graph_request(
        "GET",
        f"{cred.phone_number_id}?fields=display_phone_number,verified_name,quality_rating",
        cred,
    )
    return data.get("display_phone_number", ""), data.get("verified_name", "")


def listar_modelos(cred):
    """Modelos aprovados da conta, com o texto do corpo e o número de variáveis."""
    if not cred.business_account_id:
        return []
    data = _graph_request(
        "GET",
        f"{cred.business_account_id}/message_templates"
        "?fields=name,language,status,category,components&status=APPROVED&limit=200",
        cred,
    )
    modelos = []
    for item in data.get("data", []):
        corpo = next(
            (c.get("text", "") for c in item.get("components", []) if c.get("type") == "BODY"),
            "",
        )
        cabecalho_midia = any(
            c.get("type") == "HEADER" and c.get("format") in ("IMAGE", "VIDEO", "DOCUMENT")
            for c in item.get("components", [])
        )
        if cabecalho_midia:
            continue
        variaveis = sorted({int(n) for n in re.findall(r"\{\{\s*(\d+)\s*\}\}", corpo)})
        modelos.append(
            {
                "name": item.get("name", ""),
                "language": item.get("language", "pt_BR"),
                "category": item.get("category", ""),
                "body": corpo,
                "variables": len(variaveis),
            }
        )
    return sorted(modelos, key=lambda m: (m["name"], m["language"]))


def baixar_midia(media_id, cred=None):
    cred = cred or credenciais()
    info = _graph_request("GET", media_id, cred)
    url = info.get("url")
    if not url:
        raise WhatsAppError("Arquivo não encontrado ou expirado no WhatsApp.")
    conteudo, _ = _graph_request("GET", url, cred, raw=True)
    return conteudo, info.get("mime_type") or "application/octet-stream"


def _enviar(cred, telefone, corpo):
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": telefone}
    payload.update(corpo)
    data = _graph_request("POST", f"{cred.phone_number_id}/messages", cred, payload=payload)
    try:
        wamid = data["messages"][0]["id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise WhatsAppError("Resposta inesperada do WhatsApp ao enviar.") from exc
    wa_id = ((data.get("contacts") or [{}])[0]).get("wa_id", "")
    return wamid, wa_id


def _upload_midia(cred, conteudo, mime, nome):
    limite = secrets.token_hex(16)
    nome_seguro = re.sub(r'["\r\n]', "", nome or "arquivo")
    partes = [
        f'--{limite}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
        f'--{limite}\r\nContent-Disposition: form-data; name="type"\r\n\r\n{mime}\r\n'.encode(),
        (
            f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{nome_seguro}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        + conteudo
        + b"\r\n",
        f"--{limite}--\r\n".encode(),
    ]
    data = _graph_request(
        "POST",
        f"{cred.phone_number_id}/media",
        cred,
        body=b"".join(partes),
        content_type=f"multipart/form-data; boundary={limite}",
    )
    if not data.get("id"):
        raise WhatsAppError("O WhatsApp não aceitou o arquivo.")
    return data["id"]


# --- Envio pela conversa -----------------------------------------------------------


def _registrar_saida(conversa, *, texto, tipo, usuario=None, gerada_por_ia=False, **extra):
    return MensagemWhatsApp.objects.create(
        conversa=conversa,
        direcao=MensagemWhatsApp.Direcao.SAIDA,
        tipo=tipo,
        texto=texto,
        status=MensagemWhatsApp.Status.PENDENTE,
        enviada_por=usuario if getattr(usuario, "is_authenticated", False) else None,
        gerada_por_ia=gerada_por_ia,
        **extra,
    )


def _concluir_envio(conversa, mensagem, envio):
    """Executa o envio e grava o resultado na mensagem (sucesso ou falha)."""
    try:
        wamid, wa_id = envio()
    except WhatsAppError as exc:
        mensagem.status = MensagemWhatsApp.Status.FALHOU
        mensagem.erro = str(exc)[:500]
        mensagem.save(update_fields=["status", "erro"])
        raise
    mensagem.provider_message_id = wamid
    mensagem.status = MensagemWhatsApp.Status.ENVIADA
    mensagem.save(update_fields=["provider_message_id", "status"])
    update = {"ultima_mensagem_em": mensagem.criada_em, "ultima_mensagem_texto": _resumo(mensagem)}
    if wa_id and wa_id != conversa.telefone:
        # A Meta pode devolver o número sem o nono dígito: as respostas chegam por ele.
        update["telefone"] = wa_id
    ConversaWhatsApp.objects.filter(pk=conversa.pk).update(atualizada_em=timezone.now(), **update)
    for campo, valor in update.items():
        setattr(conversa, campo, valor)
    return mensagem


def enviar_texto(conversa, texto, *, usuario=None, gerada_por_ia=False, cred=None):
    texto = (texto or "").strip()
    if not texto:
        raise WhatsAppError("Escreva a mensagem antes de enviar.")
    if len(texto) > 4096:
        raise WhatsAppError("A mensagem passa do limite de 4.096 caracteres do WhatsApp.")
    if not conversa.janela_aberta:
        raise WhatsAppError(
            "Passaram mais de 24 h desde a última mensagem do cliente. "
            "Para retomar, envie um modelo aprovado."
        )
    cred = cred or credenciais()
    mensagem = _registrar_saida(
        conversa, texto=texto, tipo=MensagemWhatsApp.Tipo.TEXTO, usuario=usuario, gerada_por_ia=gerada_por_ia
    )
    return _concluir_envio(
        conversa,
        mensagem,
        lambda: _enviar(cred, conversa.telefone, {"type": "text", "text": {"body": texto, "preview_url": True}}),
    )


def enviar_arquivo(conversa, arquivo, *, legenda="", usuario=None, cred=None):
    if not conversa.janela_aberta:
        raise WhatsAppError("Arquivos só podem ser enviados até 24 h depois da última mensagem do cliente.")
    if arquivo.size > MAX_ARQUIVO:
        raise WhatsAppError("O arquivo passa de 16 MB, o limite do WhatsApp.")
    mime = (getattr(arquivo, "content_type", "") or "").split(";")[0].lower()
    tipo = TIPOS_ARQUIVO.get(mime)
    if not tipo:
        raise WhatsAppError("Formato não aceito. Envie PDF, Word, Excel, TXT, JPG, PNG, MP3, OGG ou MP4.")
    cred = cred or credenciais()
    nome = arquivo.name[:255]
    conteudo = arquivo.read()
    legenda = (legenda or "").strip()[:1024]
    mensagem = _registrar_saida(
        conversa, texto=legenda, tipo=tipo, usuario=usuario, midia_mime=mime, midia_nome=nome
    )

    def envio():
        media_id = _upload_midia(cred, conteudo, mime, nome)
        mensagem.midia_id = media_id
        mensagem.save(update_fields=["midia_id"])
        corpo = {"id": media_id}
        if legenda and tipo != "audio":
            corpo["caption"] = legenda
        if tipo == "document":
            corpo["filename"] = nome
        return _enviar(cred, conversa.telefone, {"type": tipo, tipo: corpo})

    return _concluir_envio(conversa, mensagem, envio)


def texto_do_modelo(corpo, parametros, nome):
    if not corpo:
        extras = f" ({', '.join(parametros)})" if parametros else ""
        return f"[Modelo {nome}]{extras}"
    return re.sub(
        r"\{\{\s*(\d+)\s*\}\}",
        lambda m: parametros[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(parametros) else m.group(0),
        corpo,
    )


def enviar_modelo(conversa, nome, idioma, parametros, *, corpo="", usuario=None, cred=None):
    nome = (nome or "").strip()
    if not re.fullmatch(r"[a-z0-9_]{1,512}", nome):
        raise WhatsAppError("Informe o nome do modelo exatamente como aprovado (minúsculas e _).")
    idioma = (idioma or "pt_BR").strip()
    parametros = [p.strip() for p in parametros if p and p.strip()]
    cred = cred or credenciais()
    mensagem = _registrar_saida(
        conversa,
        texto=texto_do_modelo(corpo, parametros, nome),
        tipo=MensagemWhatsApp.Tipo.MODELO,
        usuario=usuario,
        dados_provedor={"template": nome, "language": idioma, "parameters": parametros},
    )
    template = {"name": nome, "language": {"code": idioma}}
    if parametros:
        template["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in parametros]}
        ]
    return _concluir_envio(
        conversa, mensagem, lambda: _enviar(cred, conversa.telefone, {"type": "template", "template": template})
    )


def marcar_como_lida(conversa, cred=None):
    """Zera o contador e envia o "visto" (✓✓ azul) da última mensagem do cliente."""
    if not conversa.nao_lidas:
        return
    ConversaWhatsApp.objects.filter(pk=conversa.pk).update(nao_lidas=0)
    conversa.nao_lidas = 0
    ultima = (
        conversa.mensagens.filter(direcao=MensagemWhatsApp.Direcao.ENTRADA)
        .exclude(provider_message_id="")
        .order_by("-pk")
        .first()
    )
    cred = cred or credenciais()
    if not ultima or not cred.pode_enviar:
        return
    try:
        _graph_request(
            "POST",
            f"{cred.phone_number_id}/messages",
            cred,
            payload={"messaging_product": "whatsapp", "status": "read", "message_id": ultima.provider_message_id},
        )
    except WhatsAppError:
        logger.info("Não foi possível marcar a mensagem como lida no WhatsApp.")


def abrir_conversa(telefone, *, nome="", conta=None):
    """Encontra ou cria a conversa (e o lead) para um número."""
    telefone = normalizar_telefone(telefone)
    conversa = (
        ConversaWhatsApp.objects.select_related("conta")
        .filter(telefone=telefone)
        .order_by("-ultima_mensagem_em", "-pk")
        .first()
    )
    if conversa:
        if conta and conversa.conta_id != conta.pk:
            conversa.conta = conta
            conversa.save(update_fields=["conta", "atualizada_em"])
        return conversa
    if conta is None:
        conta = Conta.objects.filter(telefone=telefone).first() or Conta.objects.filter(
            documento=f"WAPP:{telefone[-12:]}"
        ).first()
    if conta is None:
        conta = Conta.objects.create(
            nome=nome or f"Lead WhatsApp {telefone}",
            documento=f"WAPP:{telefone[-12:]}",
            telefone=telefone,
            origem="whatsapp",
            status=Conta.Status.LEAD,
        )
    return ConversaWhatsApp.objects.create(conta=conta, telefone=telefone, nome_contato=nome)


# --- Webhook ------------------------------------------------------------------------


def processar_webhook(payload):
    recebidas = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            nomes = {
                c.get("wa_id", ""): (c.get("profile") or {}).get("name", "")
                for c in value.get("contacts", []) or []
            }
            for status in value.get("statuses", []) or []:
                _atualizar_status(status)
            for message in value.get("messages", []) or []:
                entrada = _registrar_entrada(message, nomes.get(message.get("from", ""), ""))
                if entrada:
                    recebidas.append(entrada)
    for entrada in recebidas:
        try:
            responder_automaticamente(entrada.conversa, entrada)
        except Exception:  # o bot nunca deve impedir o registro da mensagem
            logger.exception("Falha no bot do WhatsApp (conversa %s).", entrada.conversa_id)
    return recebidas


STATUS_META = {
    "sent": MensagemWhatsApp.Status.ENVIADA,
    "delivered": MensagemWhatsApp.Status.ENTREGUE,
    "read": MensagemWhatsApp.Status.LIDA,
    "failed": MensagemWhatsApp.Status.FALHOU,
}
ORDEM_STATUS = [
    MensagemWhatsApp.Status.PENDENTE,
    MensagemWhatsApp.Status.ENVIADA,
    MensagemWhatsApp.Status.ENTREGUE,
    MensagemWhatsApp.Status.LIDA,
]


def _atualizar_status(status):
    novo = STATUS_META.get(status.get("status"))
    wamid = status.get("id")
    if not novo or not wamid:
        return
    for mensagem in MensagemWhatsApp.objects.filter(
        provider_message_id=wamid, direcao=MensagemWhatsApp.Direcao.SAIDA
    ):
        if novo == MensagemWhatsApp.Status.FALHOU:
            erro = (status.get("errors") or [{}])[0]
            mensagem.status = novo
            mensagem.erro = (erro.get("title") or erro.get("message") or "Falha na entrega.")[:500]
            mensagem.save(update_fields=["status", "erro"])
        elif mensagem.status in ORDEM_STATUS and ORDEM_STATUS.index(novo) > ORDEM_STATUS.index(mensagem.status):
            # Os eventos podem chegar fora de ordem: nunca volta de "lida" para "entregue".
            mensagem.status = novo
            mensagem.save(update_fields=["status"])


def _conteudo_da_mensagem(message):
    tipo = message.get("type") or ("text" if "text" in message else "unsupported")
    dados = message.get(tipo) or {}
    texto, midia = "", {}
    if tipo == "text":
        texto = dados.get("body", "")
    elif tipo in ("image", "video", "audio", "document", "sticker"):
        texto = dados.get("caption", "")
        midia = {
            "midia_id": dados.get("id", ""),
            "midia_mime": (dados.get("mime_type") or "").split(";")[0][:100],
            "midia_nome": (dados.get("filename") or "")[:255],
        }
    elif tipo == "location":
        partes = [dados.get("name"), dados.get("address")]
        local = " · ".join(p for p in partes if p)
        texto = f"{local}\nhttps://maps.google.com/?q={dados.get('latitude')},{dados.get('longitude')}".strip()
    elif tipo == "contacts":
        contatos = []
        for contato in dados if isinstance(dados, list) else []:
            nome = (contato.get("name") or {}).get("formatted_name", "")
            fones = ", ".join(p.get("phone", "") for p in contato.get("phones", []))
            contatos.append(f"{nome} {fones}".strip())
        texto = "\n".join(contatos)
    elif tipo == "interactive":
        resposta = dados.get("button_reply") or dados.get("list_reply") or {}
        texto = resposta.get("title", "")
    elif tipo == "button":
        tipo, texto = "interactive", dados.get("text", "")
    elif tipo == "reaction":
        texto = f"Reagiu com {dados.get('emoji', '')}".strip()
    else:
        tipo, texto = "unsupported", "Mensagem de um tipo que o ERP ainda não exibe. Veja no celular."
    if tipo not in MensagemWhatsApp.Tipo.values:
        tipo = MensagemWhatsApp.Tipo.OUTRO
    return tipo, texto, midia


def _resumo(mensagem):
    texto = (mensagem.texto or "").replace("\n", " ").strip()
    if mensagem.tipo in DESCRICAO_TIPO:
        rotulo = mensagem.midia_nome if mensagem.tipo == "document" and mensagem.midia_nome else DESCRICAO_TIPO[mensagem.tipo]
        texto = f"{rotulo} {texto}".strip()
    if mensagem.direcao == MensagemWhatsApp.Direcao.SAIDA:
        texto = f"Você: {texto}"
    return texto[:255]


def _registrar_entrada(message, nome_contato):
    telefone = re.sub(r"\D", "", message.get("from", ""))
    wamid = message.get("id", "")
    if not telefone:
        return None
    tipo, texto, midia = _conteudo_da_mensagem(message)
    with transaction.atomic():
        if wamid and MensagemWhatsApp.objects.filter(provider_message_id=wamid).exists():
            return None  # a Meta reenvia o mesmo evento quando não recebe resposta a tempo
        conversa = abrir_conversa(telefone, nome=nome_contato)
        conversa = ConversaWhatsApp.objects.select_for_update().select_related("conta").get(pk=conversa.pk)
        conta = conversa.conta
        if conta.origem == "":
            conta.origem = "whatsapp"
            conta.save(update_fields=["origem"])
        if nome_contato and conta.nome.startswith("Lead WhatsApp"):
            conta.nome = nome_contato[:255]
            conta.save(update_fields=["nome"])

        entrada = MensagemWhatsApp.objects.create(
            conversa=conversa,
            provider_message_id=wamid,
            direcao=MensagemWhatsApp.Direcao.ENTRADA,
            tipo=tipo,
            texto=texto,
            status=MensagemWhatsApp.Status.RECEBIDA,
            dados_provedor=message,
            **midia,
        )
        agora = timezone.now()
        conversa.ultima_entrada_em = agora
        conversa.ultima_mensagem_em = agora
        conversa.ultima_mensagem_texto = _resumo(entrada)
        conversa.nao_lidas += 1
        if nome_contato:
            conversa.nome_contato = nome_contato[:255]
        # Com o bot ligado a conversa segue "ativa"; com um atendente, volta para a fila dele.
        conversa.status = (
            ConversaWhatsApp.Status.ATIVA if conversa.bot_ativo else ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR
        )
        conversa.save()

        distribuir_lead(conta)
        if texto and tipo in (MensagemWhatsApp.Tipo.TEXTO, MensagemWhatsApp.Tipo.BOTAO):
            qualificar_mensagem(conta, texto)
    entrada.conversa = conversa
    return entrada


# --- Bot ----------------------------------------------------------------------------

BOT_SYSTEM_PROMPT = (
    "Você é o assistente virtual de atendimento do escritório de advocacia {escritorio} no "
    "WhatsApp. Atenda com cordialidade e objetividade, em mensagens curtas (até 4 frases), "
    "como numa conversa de WhatsApp, sem markdown. Seu papel é acolher o cliente, entender a "
    "demanda e coletar: nome, se é pessoa física ou jurídica (CPF/CNPJ), assunto, urgência e "
    "melhor horário para contato. Não prometa resultado, não dê parecer jurídico definitivo e "
    "não informe valores de honorários que não estejam nas instruções. Nunca peça senhas, "
    "códigos ou dados bancários. Marque transferir_para_humano=true quando o cliente pedir "
    "para falar com uma pessoa, quando houver prazo ou urgência real, quando ele já for "
    "cliente com processo em andamento, quando você já tiver coletado os dados principais ou "
    "quando não souber responder."
)
BOT_SCHEMA = {
    "type": "object",
    "properties": {
        "resposta": {"type": "string", "description": "Mensagem a enviar ao cliente."},
        "transferir_para_humano": {"type": "boolean"},
    },
    "required": ["resposta", "transferir_para_humano"],
    "additionalProperties": False,
}


def _historico_para_ia(conversa):
    mensagens = list(conversa.mensagens.exclude(status=MensagemWhatsApp.Status.FALHOU).order_by("-pk")[:HISTORICO_BOT])
    turnos = []
    for mensagem in reversed(mensagens):
        role = "user" if mensagem.direcao == MensagemWhatsApp.Direcao.ENTRADA else "assistant"
        conteudo = mensagem.texto or ""
        if mensagem.tipo in DESCRICAO_TIPO:
            conteudo = f"[{DESCRICAO_TIPO[mensagem.tipo]} enviado] {conteudo}".strip()
        if not conteudo:
            continue
        if turnos and turnos[-1]["role"] == role:
            turnos[-1]["content"] += f"\n{conteudo}"
        else:
            turnos.append({"role": role, "content": conteudo})
    while turnos and turnos[0]["role"] != "user":
        turnos.pop(0)
    return turnos


def _pediu_atendente(texto, config):
    termos = config.handoff_terms() if config else ["atendente", "humano"]
    texto = (texto or "").lower()
    return any(re.search(rf"\b{re.escape(t)}\b", texto) for t in termos)


def _transferir(conversa, cred, mensagem_cliente=True):
    config = cred.config
    texto = config.handoff_message if config else ""
    if mensagem_cliente and texto:
        try:
            enviar_texto(conversa, texto, gerada_por_ia=True, cred=cred)
        except WhatsAppError:
            pass
    conversa.bot_ativo = False
    conversa.status = ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR
    conversa.save(update_fields=["bot_ativo", "status", "atualizada_em"])


def responder_automaticamente(conversa, entrada):
    from core.ai import AIError, feature_enabled, generate
    from core.models import AIUsageLog, CompanyProfile

    cred = credenciais()
    config = cred.config
    if not cred.pode_enviar or not conversa.bot_ativo:
        return None
    if config and not config.bot_enabled:
        return None
    if entrada.tipo == MensagemWhatsApp.Tipo.REACAO:
        return None

    if _pediu_atendente(entrada.texto, config):
        _transferir(conversa, cred)
        return None

    if feature_enabled(AIUsageLog.Feature.WHATSAPP):
        historico = _historico_para_ia(conversa)
        if not historico:
            return None
        perfil = CompanyProfile.objects.defer("logo_data").first()
        escritorio = (perfil.trade_name or perfil.legal_name) if perfil else "do escritório"
        system = BOT_SYSTEM_PROMPT.format(escritorio=escritorio)
        if config and config.bot_instructions.strip():
            system += f"\n\nInformações do escritório (use somente se forem úteis):\n{config.bot_instructions.strip()}"
        conta = conversa.conta
        system += (
            f"\n\nDados já conhecidos do contato: nome={conversa.nome_exibicao!r}, "
            f"tipo_pessoa={conta.get_tipo_pessoa_display()}, status_no_crm={conta.get_status_display()}."
        )
        try:
            resultado = generate(
                AIUsageLog.Feature.WHATSAPP,
                historico[-1]["content"],
                history=historico[:-1],
                system=system,
                json_schema=BOT_SCHEMA,
                max_tokens=2000,
                effort="low",
            )
        except AIError:
            logger.warning("IA indisponível no atendimento do WhatsApp; transferindo para a equipe.")
            _transferir(conversa, cred)
            return None
        data = resultado.data or {}
        resposta = (data.get("resposta") or "").strip()
        mensagem = None
        if resposta:
            mensagem = enviar_texto(conversa, resposta[:4096], gerada_por_ia=True, cred=cred)
        if data.get("transferir_para_humano"):
            _transferir(conversa, cred, mensagem_cliente=False)
        return mensagem

    if getattr(settings, "AI_API_URL", "") and getattr(settings, "AI_API_KEY", ""):
        qualificacao = dict(conversa.conta.qualificacao, tipo_pessoa=conversa.conta.tipo_pessoa)
        resposta = responder_com_ia(entrada.texto, qualificacao, conversa.nome_exibicao)
        return enviar_texto(conversa, resposta, gerada_por_ia=True, cred=cred)

    # Sem IA: envia as boas-vindas e deixa a conversa para a equipe. O bot volta a
    # cumprimentar quando a conversa for encerrada e o cliente escrever de novo.
    texto = config.welcome_message if config else WhatsAppConfiguration._meta.get_field("welcome_message").default
    mensagem = enviar_texto(conversa, texto, gerada_por_ia=True, cred=cred)
    _transferir(conversa, cred, mensagem_cliente=False)
    return mensagem
