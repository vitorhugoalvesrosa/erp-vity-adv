from rest_framework import serializers

from .models import Conta, ConversaWhatsApp, FollowUp, MensagemWhatsApp, Proposta


class ContaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conta
        fields = [
            "id",
            "nome",
            "documento",
            "email",
            "telefone",
            "tipo_pessoa",
            "origem",
            "vendedor",
            "qualificacao",
            "score_lead",
            "data_ultima_interacao",
            "status",
            "data_criacao",
        ]
        read_only_fields = [
            "id",
            "data_criacao",
            "data_ultima_interacao",
        ]


class PropostaSerializer(serializers.ModelSerializer):
    conta = serializers.PrimaryKeyRelatedField(queryset=Conta.objects.all())

    class Meta:
        model = Proposta
        fields = [
            "id",
            "conta",
            "valor_total",
            "condicao_pagamento",
            "status",
            "data_validade",
        ]
        read_only_fields = ["id"]


class ConversaWhatsAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversaWhatsApp
        fields = "__all__"
        read_only_fields = ["id", "iniciada_em", "atualizada_em"]


class MensagemWhatsAppSerializer(serializers.ModelSerializer):
    class Meta:
        model = MensagemWhatsApp
        fields = "__all__"
        read_only_fields = ["id", "criada_em"]


class FollowUpSerializer(serializers.ModelSerializer):
    class Meta:
        model = FollowUp
        fields = "__all__"
        read_only_fields = ["id", "criado_em"]
