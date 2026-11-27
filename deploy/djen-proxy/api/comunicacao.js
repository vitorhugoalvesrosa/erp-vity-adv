// Ponto de acesso no Brasil para a API pública do DJEN (CNJ), que recusa conexões de fora do país.
// Só repassa GET /api/v1/comunicacao e exige o token combinado com o ERP (PROXY_TOKEN).
const DESTINO = "https://comunicaapi.pje.jus.br/api/v1/comunicacao";
const PERMITIDOS = new Set([
  "numeroOab", "ufOab", "nomeAdvogado", "numeroProcesso", "siglaTribunal",
  "dataDisponibilizacaoInicio", "dataDisponibilizacaoFim", "pagina", "itensPorPagina",
]);

module.exports = async (req, res) => {
  const token = process.env.PROXY_TOKEN || "";
  if (!token || req.headers["x-proxy-token"] !== token) {
    res.status(401).json({ message: "Token inválido." });
    return;
  }
  if (req.method !== "GET") {
    res.status(405).json({ message: "Use GET." });
    return;
  }
  const params = new URLSearchParams();
  for (const [chave, valor] of Object.entries(req.query || {})) {
    if (PERMITIDOS.has(chave) && typeof valor === "string") params.set(chave, valor);
  }
  try {
    const resposta = await fetch(`${DESTINO}?${params}`, {
      headers: { Accept: "application/json", "User-Agent": "ERP-Vity-ADV/1.0" },
    });
    const restante = resposta.headers.get("x-ratelimit-remaining");
    if (restante) res.setHeader("x-ratelimit-remaining", restante);
    res.setHeader("Content-Type", resposta.headers.get("content-type") || "application/json");
    res.status(resposta.status).send(Buffer.from(await resposta.arrayBuffer()));
  } catch (erro) {
    res.status(502).json({ message: "Falha ao consultar o DJEN." });
  }
};
