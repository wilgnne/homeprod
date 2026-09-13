# FreeToken via API Ollama

Este Compose executa o `ft daemon` e um proxy leve na porta interna `11434`. O proxy oferece `/api/tags`, `/api/show`, `/api/ps`, `/api/version`, `/api/chat` e `/api/generate` para o Open WebUI. Ele inicia o modelo pedido via daemon, aguarda `/v1/models` ficar pronto e para o engine depois de cinco minutos sem requisições. O daemon e o `ft serve` não publicam portas no host.

Edite [models.json](models.json) para definir os modelos visíveis. Cada entrada contém `name` (o alias exposto ao cliente), `model` (ID Hugging Face ou caminho no container do daemon) e `args` opcionais para `ft serve`. Para caminhos locais, monte o diretório de modelos no serviço `daemon` e use o caminho dentro desse container. O catálogo é lido ao iniciar o proxy; reinicie-o após editar o arquivo. A primeira entrada usa `google/gemma-4-E2B`, presente no exemplo original deste repositório.

Crie a rede compartilhada `proxy` se ainda não existir e suba os serviços com `docker compose -f freetoken/docker-compose.yml up -d --build`. O serviço `freetoken-proxy` ingressa nessa rede e pode ser alcançado pelo Open WebUI em `http://freetoken-proxy:11434`. O Compose em `ollama/` configura `OLLAMA_BASE_URLS` com o Ollama atual e o novo proxy. Em instalações existentes, o Open WebUI pode manter no banco as URLs alteradas pelo painel; nesse caso, adicione a URL em **Admin → Connections → Ollama**. Não é necessário redefinir as outras configurações persistidas.

`FT_IDLE_SECONDS` (padrão 300) e `FT_START_TIMEOUT_SECONDS` (padrão 900) podem ser ajustados via ambiente do Compose. `keep_alive` em `/api/chat` e `/api/generate` aceita segundos ou durações como `10m`; `0` descarrega após a requisição e valor negativo mantém o modelo carregado. Um generate sem prompt com `keep_alive: 0` descarrega o modelo, como no Ollama. O daemon aceita `FREETOKEN_DAEMON_TOKEN`; o Compose usa o mesmo valor no proxy e um token local padrão quando a variável não é definida.

O daemon controla um engine por vez. Requisições a outro modelo enquanto um está ativo retornam HTTP 409. `/api/tags` lista o catálogo sem iniciar o engine; `/api/ps` mostra apenas o modelo ativo. Os metadados Ollama que não existem no FreeToken, como tamanho em disco e formato GGUF, não são inventados: o tamanho é `0` e o formato é `freetoken`. O `digest` é uma identidade estável derivada do ID do catálogo, não um hash dos pesos.

O proxy cobre texto e chamadas de ferramenta nos chats, além de completions simples. Recursos de gerenciamento de modelos do Ollama, embeddings, geração de imagens e ajustes de contexto por requisição não fazem parte desta versão. Configure os argumentos de contexto e runtime em `models.json` para o `ft serve`. A URL de inferência do FreeToken permanece interna à rede Docker.

Para rodar os testes do proxy com Python 3.12: instale `proxy/requirements-dev.txt` e execute `pytest -q freetoken/proxy/test_app.py`. Eles usam servidores simulados e não exigem GPU.
