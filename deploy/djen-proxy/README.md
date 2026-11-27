# Ponto de acesso ao DJEN no Brasil

A API pública do DJEN (`comunicaapi.pje.jus.br`) só atende conexões vindas do Brasil. O ERP roda
no Render, fora do país, então a busca de processos por OAB passa por esta função, publicada na
Vercel na região de São Paulo (`gru1`). O plano gratuito da Vercel é suficiente.

## Publicar (uma vez)

1. Crie uma conta em https://vercel.com (pode entrar com o GitHub).
2. **Add New → Project**, escolha este repositório e, em **Root Directory**, informe `deploy/djen-proxy`.
3. Em **Environment Variables**, crie `PROXY_TOKEN` com uma senha longa e aleatória.
4. Clique em **Deploy**. Anote o endereço gerado, por exemplo `https://djen-proxy-xxxx.vercel.app`.

## Ligar ao ERP

No Render, em **Environment**, crie:

- `DJEN_BASE_URL` = `https://djen-proxy-xxxx.vercel.app/api/comunicacao`
- `DJEN_PROXY_TOKEN` = a mesma senha do `PROXY_TOKEN`

Salve. O Render reinicia o serviço e o botão **Buscar processos no DJEN** passa a funcionar.

A função só repassa consultas de comunicações (filtros de OAB, processo, tribunal e datas) e
recusa qualquer chamada sem o token.
