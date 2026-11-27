import base64
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from docx import Document

from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from core.models import CompanyProfile
from crm.models import Conta
from juridico.documentos import MESES, campos_faltando
from juridico.models import ModeloDocumento

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def texto_docx(conteudo):
    documento = Document(BytesIO(conteudo))
    partes = [p.text for p in documento.paragraphs]
    for secao in documento.sections:
        partes += [p.text for p in secao.footer.paragraphs]
    return "\n".join(partes)


class DocumentosClienteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.manager = users.create_user("doc-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("doc-operador", password="test-password")
        cls.operator.groups.add(groups[OPERATOR_GROUP])
        cls.pf = Conta.objects.create(
            nome="Maria da Silva", documento="12345678901", tipo_pessoa="pf", telefone="(21) 99999-0000",
            nacionalidade="brasileira", estado_civil="casada", profissao="professora", rg="12.345.678-9",
            cep="22640100", endereco="Av. das Américas, 700", bairro="Barra da Tijuca", cidade="Rio de Janeiro", uf="RJ",
        )
        cls.pj = Conta.objects.create(
            nome="Empresa Exemplo Ltda", documento="11222333000181", tipo_pessoa="pj",
            representante_nome="João Souza", representante_cpf="98765432100",
        )

    def modelo(self, nome):
        return ModeloDocumento.objects.get(nome=nome)

    def gerar(self, conta, modelo, formato="docx", **params):
        url = reverse("cliente-documento-gerar", args=[conta.pk, modelo.pk, formato])
        return self.client.get(url, params)

    def test_quatro_modelos_cadastrados(self):
        self.assertEqual(
            sorted(ModeloDocumento.objects.values_list("aplica_a", flat=True)), ["pf", "pf", "pj", "pj"]
        )
        self.assertIn("184.436", self.modelo("Procuração — pessoa física").corpo)

    def test_procuracao_pf_sai_preenchida(self):
        self.client.force_login(self.operator)
        response = self.gerar(self.pf, self.modelo("Procuração — pessoa física"))
        self.assertEqual(response.status_code, 200)
        texto = texto_docx(response.content)
        hoje = timezone.localdate()
        for esperado in (
            "PROCURAÇÃO", "NOME: Maria da Silva", "CPF Nº: 123.456.789-01", "ESTADO CIVIL: casada",
            "PROFISSÃO: professora", "CEP: 22640-100", "TEL: (21) 99999-0000", "ESTADO: RJ",
            "KÁTIA MILENY BENITES PEREIRA DA SILVA", f"Rio de Janeiro, {hoje.day} de {MESES[hoje.month - 1]}",
            "ASSINATURA DO(A) OUTORGANTE", "contato@benitessilva.com",
        ):
            self.assertIn(esperado, texto)
        self.assertNotIn("**", texto)

    def test_declaracao_pj_com_representante_e_campos_em_branco(self):
        self.client.force_login(self.operator)
        modelo = self.modelo("Declaração de hipossuficiência — pessoa jurídica")
        texto = texto_docx(self.gerar(self.pj, modelo, data="branco").content)
        self.assertIn("EMPRESA: Empresa Exemplo Ltda", texto)
        self.assertIn("CNPJ: 11.222.333/0001-81", texto)
        self.assertIn("REPRESENTANTE LEGAL: João Souza", texto)
        self.assertIn("CPF Nº: 987.654.321-00", texto)
        self.assertIn("ENDEREÇO COMERCIAL: ____", texto)
        self.assertIn("Rio de Janeiro, ________ de", texto)
        self.assertIn("CEP", campos_faltando(modelo, self.pj))
        self.assertIn("Endereço comercial", campos_faltando(modelo, self.pj))

    def test_pdf_com_logo(self):
        CompanyProfile.objects.create(legal_name="Escritório", logo_data=PNG_1PX, logo_content_type="image/png")
        self.client.force_login(self.operator)
        response = self.gerar(self.pf, self.modelo("Procuração — pessoa física"), formato="pdf")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        docx = self.gerar(self.pf, self.modelo("Procuração — pessoa física"))
        self.assertEqual(docx.status_code, 200)

    def test_pagina_mostra_so_modelos_do_tipo_e_o_que_falta(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("cliente-documentos", args=[self.pj.pk]))
        self.assertContains(response, "Procuração — pessoa jurídica")
        self.assertNotContains(response, "Procuração — pessoa física")
        self.assertContains(response, "Faltam no cadastro")
        completo = self.client.get(reverse("cliente-documentos", args=[self.pf.pk]))
        self.assertContains(completo, "Cadastro completo para este documento")

    def test_clientes_no_menu_e_novo_cliente_abre_os_documentos(self):
        self.client.force_login(self.manager)
        home = self.client.get(reverse("home"))
        self.assertContains(home, f'href="{reverse("record-list", args=["contas"])}"')
        self.assertNotContains(home, "home-clientes")
        lista = self.client.get(reverse("record-list", args=["contas"]))
        self.assertContains(lista, "?depois=documentos")
        response = self.client.post(
            f"{reverse('record-create', args=['contas'])}?depois=documentos",
            {"tipo_pessoa": "pf", "nome": "Carlos", "documento": "111.222.333-44", "status": "cliente", "uf": "rj"},
        )
        conta = Conta.objects.get(nome="Carlos")
        self.assertRedirects(response, reverse("cliente-documentos", args=[conta.pk]), fetch_redirect_response=False)
        self.assertEqual(conta.uf, "RJ")

    def test_cpf_com_digitos_errados_e_recusado(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["contas"]),
            {"tipo_pessoa": "pf", "nome": "Errado", "documento": "123", "status": "lead"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CPF precisa ter 11 dígitos")

    def test_formato_invalido_da_404(self):
        self.client.force_login(self.operator)
        response = self.client.get(
            reverse("cliente-documento-gerar", args=[self.pf.pk, self.modelo("Procuração — pessoa física").pk, "exe"])
        )
        self.assertEqual(response.status_code, 404)

    def test_baixar_selecionados_em_pdf_junta_num_arquivo(self):
        from pypdf import PdfReader

        self.client.force_login(self.operator)
        modelos = ModeloDocumento.objects.filter(aplica_a="pf")
        response = self.client.get(
            reverse("cliente-documentos-lote", args=[self.pf.pk]),
            {"formato": "pdf", "modelos": [m.pk for m in modelos]},
        )
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        leitor = PdfReader(BytesIO(response.content))
        self.assertEqual(len(leitor.pages), 2)
        texto = " ".join(pagina.extract_text() for pagina in leitor.pages)
        self.assertIn("PROCURAÇÃO", texto)
        self.assertIn("DECLARAÇÃO", texto)

    def test_baixar_todos_em_word_vem_em_zip_e_ignora_outro_tipo(self):
        import zipfile

        self.client.force_login(self.operator)
        pj_modelo = ModeloDocumento.objects.filter(aplica_a="pj").first()
        response = self.client.get(
            reverse("cliente-documentos-lote", args=[self.pf.pk]),
            {"formato": "docx", "todos": "1", "modelos": [pj_modelo.pk]},
        )
        self.assertEqual(response["Content-Type"], "application/zip")
        nomes = zipfile.ZipFile(BytesIO(response.content)).namelist()
        self.assertEqual(len(nomes), 2)
        self.assertTrue(all(nome.endswith(".docx") and "pessoa-fisica" in nome for nome in nomes))

    def test_sem_selecao_volta_com_aviso(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("cliente-documentos-lote", args=[self.pf.pk]), {"formato": "pdf"}, follow=True)
        self.assertContains(response, "Selecione ao menos um documento")

    def test_procuracao_sai_com_o_logo_do_escritorio(self):
        from pypdf import PdfReader

        self.client.force_login(self.operator)
        procuracao = self.modelo("Procuração — pessoa física")
        pdf = PdfReader(BytesIO(self.gerar(self.pf, procuracao, formato="pdf").content))
        self.assertEqual(len(pdf.pages[0].images), 1)
        word = Document(BytesIO(self.gerar(self.pf, procuracao).content))
        self.assertEqual(len(word.sections[0].header.part.package.image_parts), 1)
        # A declaração segue o original: sem logo.
        declaracao = self.modelo("Declaração de hipossuficiência — pessoa física")
        pdf = PdfReader(BytesIO(self.gerar(self.pf, declaracao, formato="pdf").content))
        self.assertEqual(len(pdf.pages[0].images), 0)
