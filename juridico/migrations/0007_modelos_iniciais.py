from django.db import migrations

RODAPE = "Tels.: (21) 98894-9616 / 98578-1594 / 98505-6426 - E-mail: contato@benitessilva.com"

PODERES = (
    "Pelo presente instrumento de ***PROCURAÇÃO***, nomeia seu bastante Procurador(as), ora Outorgado(as), a "
    "**Dra. KÁTIA MILENY BENITES PEREIRA DA SILVA**, brasileira, casada, advogada, inscrita na OAB/RJ sob o nº. "
    "184.436, sócia-proprietária do escritório BENITES SILVA SOCIEDADE INDIVIDUAL DE ADVOCACIA inscrito no CNPJ "
    "nº 40.328.901/0001-21, domiciliado na Avenida Nelson Cardoso, nº 309, Taquara, Jacarepaguá, Rio de "
    "Janeiro/RJ; **a quem confere, amplos poderes para o Foro em geral, com a cláusula a CLÁUSULA** "
    "***AD JUDÍCIA*** perante qualquer Instância ou Tribunal de Justiça do Brasil, incluindo STF, STJ, TRT, TRF, "
    "podendo propor contra quem de direito, realizar todos os atos que se fizerem necessários ao bom e fiel "
    "cumprimento deste mandato, propor quaisquer ações, defender-me (nos) nas que (me) (nos) forem propostas, "
    "reconvir, promover quaisquer medidas cautelares, recorrer em qualquer instância ou tribunal, arrolar, "
    "inquirir, contraditar e recusar testemunhas, produzir provas, arrazoar processos, requerer vistas dos "
    "mesmos, concordar com cálculos, custas e contas processuais, podendo ainda, fazer defesas prévias, "
    "alegações finais, formar os documentos necessários, efetuar levantamentos, receber mandado de pagamento, "
    "alvarás em seu nome, requerer laudos e desarquivamentos, avaliações e perícias, bem como arguir suspeição, "
    "falsidade e exceção, transigir, fazer acordo, confessar, renunciar, desistir, impugnar, receber e dar "
    "quitação, firmar compromissos, requerer abertura de inventário ou arrolamentos, assinar termo de "
    "compromisso de inventariante, de renúncia, perante qualquer juízo, instância ou tribunal, repartição "
    "pública e órgãos da administração pública, direta ou indireta, federal, estadual e municipal, autarquia ou "
    "entidade paraestatal, propondo ação competente em que o(s) outorgante(s) seja(m) autor(es) ou "
    "reclamante(s) e defendendo-o(s), na condição de reclamada(s) bem como substabelecer a presente com ou sem "
    "reserva de poderes se assim lhe convier, concordando desde já com a expedição de mandado de pagamento em "
    "nome da sua patrona, dando tudo por bom, firme e valioso. O(As) outorgado(as) está(ão) expressamente "
    "autorizado(as) a receber valores a título de honorários contratuais, sucumbenciais ou indenizatórios em "
    "nome do outorgante. É vedado o recebimento ou a transferência desses valores para qualquer conta bancária "
    "pessoal ou diversa da mencionada conta do escritório ou do representante legal."
)

FUNDAMENTO = (
    "A presente declaração é firmada com fundamento no art. 5º, inciso LXXIV, da Constituição Federal, bem como "
    "nos arts. 98 e 99 do Código de Processo Civil, fazendo jus, portanto, aos benefícios da gratuidade de justiça."
)

MODELOS = [
    {
        "nome": "Procuração — pessoa física",
        "titulo": "PROCURAÇÃO",
        "aplica_a": "pf",
        "corpo": PODERES,
        "assinatura": "ASSINATURA DO(A) OUTORGANTE",
        "incluir_telefone": True,
        "usar_logo": True,
        "rodape": RODAPE,
        "ordem": 1,
    },
    {
        "nome": "Procuração — pessoa jurídica",
        "titulo": "PROCURAÇÃO",
        "aplica_a": "pj",
        "corpo": PODERES,
        "assinatura": "ASSINATURA DO(A) REPRESENTANTE LEGAL",
        "usar_logo": True,
        "rodape": RODAPE,
        "ordem": 2,
    },
    {
        "nome": "Declaração de hipossuficiência — pessoa física",
        "titulo": "DECLARAÇÃO",
        "aplica_a": "pf",
        "corpo": "\n\n".join(
            [
                "DECLARO, para todos os fins de direito, sob as penas da lei, que não possuo condições financeiras "
                "de arcar com o pagamento das custas, despesas processuais e honorários advocatícios, sem prejuízo "
                "do meu sustento e de minha família.",
                FUNDAMENTO,
                "Por ser expressão da verdade, firmo a presente para que produza seus efeitos legais.",
            ]
        ),
        "assinatura": "ASSINATURA DO(A) DECLARANTE",
        "ordem": 3,
    },
    {
        "nome": "Declaração de hipossuficiência — pessoa jurídica",
        "titulo": "DECLARAÇÃO",
        "aplica_a": "pj",
        "corpo": "\n\n".join(
            [
                "A pessoa jurídica identificada acima, DECLARA, para todos os fins de direito, sob as penas da lei, "
                "que não possui condições financeiras de arcar com o pagamento das custas, despesas processuais e "
                "honorários advocatícios, sem prejuízo da manutenção de suas atividades empresariais, bem como do "
                "sustento de seu representante legal e de sua família.",
                FUNDAMENTO,
                "Por ser expressão da verdade, firma a presente para que produza seus efeitos legais.",
            ]
        ),
        "assinatura": "ASSINATURA DO(A) REPRESENTANTE LEGAL",
        "ordem": 4,
    },
]


def criar(apps, schema_editor):
    Modelo = apps.get_model("juridico", "ModeloDocumento")
    for dados in MODELOS:
        Modelo.objects.get_or_create(nome=dados["nome"], defaults=dados)


class Migration(migrations.Migration):
    dependencies = [("juridico", "0006_modelos_documentos")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
