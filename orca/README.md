# Orca com Coder CLI

A imagem instala o Coder CLI pelo instalador oficial, em modo `standalone`
(`/usr/local/bin/coder`). Os ambientes de desenvolvimento ficam no servidor
Coder; o container do Orca atua como cliente.

Para construir e iniciar, configure `.env` a partir de `.env.example` e execute
neste diretório:

```bash
docker compose up -d --build
```

## Login manual e SSH

Abra um bash como o usuário da aplicação para gravar a sessão no local correto:

```bash
docker compose exec --user orca orca-server bash
coder login https://coder.lab.wilgnne.dev
coder config-ssh --yes
```

Siga as instruções interativas do login. Para ferramentas que precisam descobrir
os workspaces lendo entradas individuais em `~/.ssh/config`, use
`coder config-ssh --yes --no-wildcard` após o login e repita quando os workspaces
mudarem.

`config-ssh` precisa do login: no CLI v2.39.0, a execução com um diretório de
configuração vazio retorna `You are not logged in`. A inicialização do container
não tenta autenticar nem configurar SSH automaticamente.

## Persistência

O Compose mantém os seguintes volumes nomeados:

| Volume | Montagem | Conteúdo |
| --- | --- | --- |
| `orca-home` | `/home/orca` | Dados e configurações do Orca |
| `coder-config` | `/home/orca/.config/coderv2` | Configuração e sessão do Coder (`CODER_CONFIG_DIR`) |
| `coder-ssh` | `/home/orca/.ssh` | Configuração SSH, chaves e hosts conhecidos |

O entrypoint prepara os diretórios do Coder e SSH com proprietário `orca` e modo
`700`. As montagens preservam o login e a configuração SSH ao recriar o container.
`docker compose down -v` remove os volumes, incluindo a sessão salva.

Referências: [instalador oficial](https://coder.com/install.sh),
[acesso aos workspaces](https://coder.com/docs/user-guides/workspace-access) e
[config-ssh](https://coder.com/docs/reference/cli/config-ssh).
