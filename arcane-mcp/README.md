# Arcane MCP

Gateway privado MCP Streamable HTTP em `/mcp`, com três ferramentas lazy:
`arcane_search`, `arcane_describe` e `arcane_execute`. O catálogo vem do
OpenAPI 3.1 da instância em `/api/openapi.json`; não há dependência de docker.sock.

## Execução local

Requer Node.js 22 e npm. O projeto é ESM e o ponto de entrada compilado é
`dist/server.js`.

```sh
npm ci
npm run build
cp .env.example .env
# Edite .env com a origem exata e credenciais próprias.
node --env-file=.env dist/server.js
```

`npm start` inicia o código compilado e lê as variáveis do ambiente do processo;
ele não carrega `.env` automaticamente. `npm test` compila e executa `node --test`.
`GET /healthz` verifica liveness; `GET /readyz` verifica prontidão local, sem
comprovar permissões ou compatibilidade com um Arcane real.

## Docker e Compose

```sh
cp .env.example .env
# Edite .env antes de iniciar; não versione credenciais.
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs --tail=50
```

O Compose usa `127.0.0.1:3000`, processo non-root, filesystem read-only, tmpfs,
capabilities removidas, `no-new-privileges` e healthcheck em `/readyz`.
`MCP_PORT` altera a porta do host. A imagem usa build multi-stage Node 22 e
instala somente dependências de produção na etapa final; não inclui `.env`,
fixture ou testes. Não monte Docker socket.

O Compose usa a rede externa `proxy` e publica apenas `/mcp` pelo entrypoint
Traefik `websecure`, que deve ser acessível somente pela VPN. O domínio padrão
é `arcane-mcp.lab.wilgnne.dev`. Defina `MCP_DOMAIN` para usar outro domínio. A
porta publicada em loopback mantém o acesso administrativo local. Não configure
múltiplas réplicas: aprovações ficam na memória de uma instância.
Reiniciar o processo invalida aprovações pendentes.

## Configuração

| Variável | Uso |
| --- | --- |
| `ARCANE_URL` | Opcional no modo header-only; origem padrão, sem path, query, fragment ou credenciais na URL |
| `ARCANE_API_KEY` | Opcional no modo header-only; chave Arcane dedicada, injetada como `X-Api-Key` |
| `MCP_AUTH_TOKEN` | Bearer token obrigatório para clientes MCP |
| `MCP_ALLOWED_ORIGINS` | CSV de origens Arcane exatas autorizadas, incluindo scheme e porta; vazio é válido para um destino padrão HTTPS público |
| `MCP_ALLOW_HEADER_TARGETS` | `false` por padrão; habilita seleção por headers quando `true` |
| `WRITE_POLICY` | `read-only` por padrão neste Compose; `all-with-approval` exige decisão explícita |
| `READ_ONLY` | `false` por padrão; `true` bloqueia toda escrita, mesmo com `all-with-approval` |
| `ALLOW_MUTATIONS` | `true` por padrão; `false` bloqueia operações classificadas como mutação |
| `ALLOW_DESTRUCTIVE` | `true` por padrão; `false` bloqueia destrutivas; permitir exige também política de escrita e aprovação humana |
| `MCP_APPROVAL_ADMIN_TOKEN` | Segredo humano separado, necessário para aprovar destrutivas |
| `PORT` | Porta interna, padrão 3000 |
| `OPENAPI_TTL_SECONDS` | TTL do catálogo, padrão 3600 |
| `REQUEST_TIMEOUT_MS` | Timeout de upstream, padrão 30000 |
| `MAX_RESPONSE_BYTES` | Limite de resposta, padrão 1048576 |
| `LOG_LEVEL` | Nível de logs, padrão `info` |

O Compose deixa `ARCANE_URL`, `ARCANE_API_KEY` e `MCP_ALLOWED_ORIGINS` vazios por
padrão; o serviço valida as combinações na inicialização. Configure URL e chave
juntas, ou use modo header-only: ambas vazias, `MCP_ALLOW_HEADER_TARGETS=true` e
allowlist não vazia em `MCP_ALLOWED_ORIGINS`. Nesse modo cada requisição envia
os dois headers de destino. HTTP exige allowlist explícita, inclusive no destino
padrão. Os três flags de escrita acima são repassados pelo Compose.

A autorização final continua sendo feita pelo Arcane. `all-with-approval` permite
mutações compatíveis e autorizadas; destrutivas exigem aprovação independente.
Não desabilite validação TLS. HTTP só é apropriado para uma rede privada
explicitamente autorizada. A allowlist não deve incluir origens desnecessárias.

## Cliente MCP

O Compose inclui `MCP_DOMAIN` em `MCP_ALLOWED_HOSTS` por padrão. Se substituir
essa lista, inclua o host HTTP exato do proxy (com porta, se não padrão).
Para clientes browser, configure também
`MCP_ALLOWED_REQUEST_ORIGINS` com as origens HTTPS exatas permitidas.

Configure a URL `http://127.0.0.1:3000/mcp` (ou a URL HTTPS privada do proxy)
e o header `Authorization: Bearer <MCP_AUTH_TOKEN>` no cliente HTTP MCP.
O cliente e o SDK negociam a versão de protocolo; não fixe uma versão futura.

No proxy opt-in, envie `X-Arcane-Url` e `X-Arcane-Api-Key` juntos em cada
requisição. Não coloque credenciais nos argumentos das ferramentas. Cada chamada
usa seu contexto HTTP atual. Não forneça o segredo administrativo ao cliente.

Fluxo típico: pesquise com `arcane_search`, descreva o `id` retornado com
`arcane_describe` e execute com `{ "id": "...", "args": { "path": {}, "query": {} } }`.
Use apenas parâmetros documentados. IDs pertencem ao snapshot do catálogo;
pesquise novamente após uma alteração do OpenAPI. NDJSON, streaming, uploads,
WebSocket e downloads binários estão fora do MVP.

## Aprovação humana

Uma execução destrutiva retorna `approval_required` e um `approvalId` sem
alterar o upstream. A pessoa responsável revisa o pedido usando o canal
administrativo separado: `GET /admin/approvals`, depois
`POST /admin/approvals/{approvalId}/approve` ou `/reject`, com
`Authorization: Bearer <MCP_APPROVAL_ADMIN_TOKEN>`.

O agente repete a ferramenta com o mesmo `id`, argumentos e `approvalId`.
A aprovação é limitada ao principal, credencial, origem, snapshot e argumentos,
expira e é de uso único. Falha após o consumo exige nova aprovação; não há
retry silencioso. O token administrativo não pode estar disponível ao agente,
seus arquivos, shell, env ou ferramentas HTTP. Guarde-o no ambiente do gateway
e no canal humano; o arquivo `.env` local é somente uma opção de desenvolvimento,
não deve estar no workspace de um agente com acesso ao shell.

## Testes

```sh
npm test
```

A suíte padrão sobe um upstream HTTP descartável numa interface RFC1918 local (por exemplo, rede Docker) e um processo do
gateway. Usa `Client` e `StreamableHTTPClientTransport` do SDK MCP oficial para
initialize, tools/list e tools/call. Mutações da suíte ocorrem exclusivamente no
mock. `test/fixtures/openapi.json` é sintético, **não** uma captura validada de
Arcane 3. A fixture genuína `test/fixtures/arcane-v3.0.0.openapi.json.gz` preserva o export
completo Arcane 3.0.0, validado por SHA-256 em `catalog-real.test.mjs`; ela cobre
descoberta, schemas e preparação de GET sem acessar uma instância.

A opção real é desabilitada por padrão. Execute contra um gateway configurado
com `WRITE_POLICY=read-only` e uma chave Arcane somente leitura, sem segredo
administrativo no ambiente de teste:

```sh
export ARCANE_REAL_TEST=1
export REAL_MCP_URL=https://mcp.example.internal/mcp
# Defina REAL_MCP_TOKEN por um gerenciador de segredos ou shell privado.
npm run build
node --test test/real-readonly.test.mjs
```

Por padrão a opção real apenas lista ferramentas, pesquisa e descreve uma
operação GET classificada como `read`. Para executar uma consulta específica,
defina também `REAL_READ_PATH` com o path exato devolvido pelo catálogo,
`REAL_SEARCH_QUERY` para encontrá-lo e, se necessário, `REAL_READ_ARGS` como JSON
contendo apenas `path` e `query`. O teste recusa body, aprovação e métodos
mutáveis. Não há testes destrutivos nem escrita contra serviço real.

Verificações já realizadas: Codex `arcane_search`/`arcane_describe` via HTTP;
SDK MCP com GET de agents e projects numa instância Arcane 3.1.1 descartável;
Compose healthy, processo non-root, filesystem read-only e sem Docker socket,
com GET real através do container. Essas verificações de leitura não validam
escritas reais. A suíte cobre mutações apenas no mock, 403 estruturado e
aprovações expiradas, vinculadas à chave/origem e de uso único, inclusive após
falha simulada depois do consumo.

A aprovação de release `writes=all` exige os gates de escrita, autorização e
aprovação de `../untitled.md`, usando infraestrutura descartável.

Referências: [MCP transports](https://modelcontextprotocol.io/specification/latest/basic/transports)
e [SDK oficial](https://github.com/modelcontextprotocol/typescript-sdk).
